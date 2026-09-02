"""comm surface of stapel-vocabularies (spec §3.3).

Four Functions, each carrying a JSON schema in ``schemas/`` — tests run with
``VALIDATE_SCHEMAS`` on, so a payload drifting from its schema fails loudly.
Registration happens on import from ``apps.py:ready()``.

Two of them exist so a service that holds NO vocabulary tables can still
validate a ``ref_select`` value: ``stapel_vocabularies.resolver.CommResolver``
is exactly ``describe`` + ``resolve`` behind the ``VocabularyResolver``
protocol. Both take codes a caller already has.

    from stapel_core.comm import call

    call("vocabularies.resolve", {"vocabulary": "phone-models", "level": "Model",
                                  "codes": ["iphone-10"],
                                  "parent": {"level": "Vendor", "code": "apple"}})
    # -> {"exists": {"iphone-10": True}, "labels": {"iphone-10": "iPhone 10"},
    #     "is_child": {"iphone-10": True}}

    call("vocabularies.describe", {"vocabulary": "phone-models"})
    # -> {"slug": "phone-models", "levels": [{"name": "Vendor", "parent": None}, ...],
    #     "revision": 7}

The other two are the ones a caller with no code uses. ``match`` turns one
free-text guess into one code or an explicit refusal; ``set_popularity``
pushes the observed listing counts that decide which terms a level opens on.

    call("vocabularies.match", {"vocabulary": "phone-models", "level": "Vendor",
                                "text": "Самсунг"})
    # -> {"matched": True, "code": "samsung", "label": "Samsung",
    #     "score": 1.0, "method": "exact"}

    call("vocabularies.set_popularity", {"vocabulary": "phone-models",
                                         "level": "Vendor",
                                         "counts": {"samsung": 41_233, "apple": 38_902}})
    # -> {"ranked": 2, "revision": 8}
"""
import json
import logging
from pathlib import Path

from stapel_core.comm import function

logger = logging.getLogger(__name__)

_SCHEMAS_DIR = Path(__file__).resolve().parent / "schemas" / "functions"

#: Score of a UNIQUE prefix hit. Below 1.0 because a prefix is not an
#: identity — "Galaxy" is only "Galaxy S10" while that is the one term
#: starting with it, and the next catalogue import can end that. Above the
#: default floor because, while it holds, it is the only thing it can mean.
PREFIX_SCORE = 0.9


def _schema(name: str) -> dict:
    """Load a committed contract — one source of truth, no inline copy."""
    return json.loads((_SCHEMAS_DIR / f"{name}.json").read_text(encoding="utf-8"))


@function("vocabularies.describe", schema=_schema("vocabularies.describe"))
def describe_function(payload: dict) -> dict:
    """The level structure of one vocabulary, or ``None`` if there is no such slug.

    ``revision`` is part of the answer, not decoration: it is what lets a
    caller cache this result and know when it went stale. Without it a remote
    resolver could only cache on a timer.
    """
    from .models import Vocabulary

    row = (
        Vocabulary.objects.filter(slug=payload["vocabulary"])
        .values("slug", "levels", "revision")
        .first()
    )
    if row is None:
        return None
    return {
        "slug": row["slug"],
        "levels": [
            {"name": level["name"], "parent": level.get("parent")}
            for level in row["levels"] or []
        ],
        "revision": row["revision"],
    }


@function("vocabularies.resolve", schema=_schema("vocabularies.resolve"))
def resolve_function(payload: dict) -> dict:
    """Existence, labels and (optionally) parentage for a batch of term codes.

    One round trip answers all three questions ``ref_select`` validation asks,
    because asking them separately would be three round trips per submitted
    listing. Unknown codes are ``False`` in ``exists`` and simply absent from
    ``labels`` (the ``projections.read()`` convention) — the caller falls back
    to the code, which is what ``format_value`` does.

    ``is_child`` is ``None`` when no ``parent`` was named: "no parent given"
    and "no code is a child" are different answers and a caller must be able
    to tell them apart.
    """
    from .models import Term, TermEdge

    vocabulary = payload["vocabulary"]
    level = payload["level"]
    codes = list(dict.fromkeys(payload.get("codes") or []))
    language = payload.get("language") or None

    rows = list(
        Term.objects.filter(
            vocabulary__slug=vocabulary, level=level, code__in=codes
        ).values("id", "code", "label", "labels")
    )
    found = {row["code"]: row for row in rows}
    exists = {code: code in found for code in codes}
    labels = {}
    for code, row in found.items():
        translated = (row["labels"] or {}).get(language) if language else None
        labels[code] = translated or row["label"]

    is_child = None
    parent = payload.get("parent")
    if parent:
        parent_id = (
            Term.objects.filter(
                vocabulary__slug=vocabulary,
                level=parent["level"],
                code=parent["code"],
            )
            .values_list("id", flat=True)
            .first()
        )
        if parent_id is None:
            is_child = {code: False for code in codes}
        else:
            child_ids = set(
                TermEdge.objects.filter(
                    parent_id=parent_id, child_id__in=[r["id"] for r in rows]
                ).values_list("child_id", flat=True)
            )
            is_child = {
                code: found[code]["id"] in child_ids if code in found else False
                for code in codes
            }

    return {"exists": exists, "labels": labels, "is_child": is_child}


@function("vocabularies.set_popularity", schema=_schema("vocabularies.set_popularity"))
def set_popularity_function(payload: dict) -> dict:
    """Rebuild one level's popular band from a host's observed listing counts.

    The push half of ``ranking.apply_popularity`` — see that module for why
    the counts arrive from outside and why the call is idempotent. ``None``
    for an unknown vocabulary or an unknown level, the same way ``describe``
    says "no such thing": a host pushing counts at a level that was renamed
    in the last catalogue import must not read a silent success.
    """
    from .models import Vocabulary
    from .ranking import apply_popularity

    vocabulary = Vocabulary.objects.filter(slug=payload["vocabulary"]).first()
    if vocabulary is None or not vocabulary.has_level(payload["level"]):
        return None
    ranked = apply_popularity(
        vocabulary, payload["level"], payload.get("counts") or {}
    )
    vocabulary.refresh_from_db(fields=["revision"])
    return {"ranked": ranked, "revision": vocabulary.revision}


def _match_scope(vocabulary, level, parent):
    """The candidate queryset for a match, or ``None`` if nothing can match.

    ``None`` rather than an empty queryset when a named parent resolves to
    no term: "that vendor is not here" and "that vendor has no models
    matching" are the same refusal to this Function's caller, but they must
    not silently become "search the whole level", which is how a Color under
    the wrong Model gets written into a listing.
    """
    from .models import Term

    terms = Term.objects.filter(vocabulary=vocabulary, level=level)
    if not parent:
        return terms
    parent_id = (
        Term.objects.filter(
            vocabulary=vocabulary, level=parent["level"], code=parent["code"]
        )
        .values_list("id", flat=True)
        .first()
    )
    if parent_id is None:
        return None
    return terms.filter(parent_edges__parent_id=parent_id)


def _hit(row, score: float, method: str) -> dict:
    return {
        "matched": True,
        "code": row["code"],
        "label": row["label"],
        "score": float(score),
        "method": method,
    }


def _no_match() -> dict:
    """The one miss shape for "the question was well formed, nothing cleared
    the floor" — a fresh dict per call, never a shared module-level one a
    caller could mutate into somebody else's answer."""
    return {"matched": False, "reason": "no_confident_match"}


@function("vocabularies.match", schema=_schema("vocabularies.match"))
def match_function(payload: dict) -> dict:
    """One free-text guess -> one term code with a score, or an explicit refusal.

    Written for a composer in another service, not for a keyboard. A
    typeahead may answer loosely: it puts five rows in front of a person and
    the person picks. This answer is written straight into a listing, so a
    near-miss is not a worse result, it is wrong data nobody looked at. The
    contract is therefore two-shaped — a scored hit, or ``matched: false``
    — with no room for a maybe.

    Three rungs, tried in order, each with a score the caller can threshold:

    * **exact (1.0)** — the folded label, the code itself, or the code
      ``slug.slugify_term`` would mint from the text. That third one is not
      a guess: it is the very function every code in the table was derived
      by, so «Самсунг» and "Samsung" fold onto ``samsung`` by construction,
      and a whole class of cross-script questions is answered with no
      embedding, no bill and no threshold.
    * **prefix (0.9)** — only when exactly ONE term starts with the text.
      Two candidates is not a weaker match, it is a different question
      ("iPhone 1" is two phones), and picking one of them writes the wrong
      model number into somebody's listing.
    * **vector (the stated similarity)** — the ``VECTOR_SIMILAR_FUNCTION``
      seam, scoped back down to this vocabulary/level/parent, carrying the
      number the far side reported. A hit whose score the provider did not
      state is REFUSED: a confidence nobody measured is not a confidence,
      and inventing one here would be the exact defect this Function was
      built to close.

    ``MATCH_MIN_SCORE`` defaults to **0.8**, calibrated on a live stand
    whose vector layer answers «Самсунг» with [Samsung, Siemens] and «айфон»
    with [MyPhone, Fairphone, Elephone] — three wrong brands that merely
    end in the same letters. A floor has to sit above that "shares a
    substring" band, and above the far side's own floor, which plainly
    passed it. It also has to leave room for the true neighbours, which is
    why it is not 0.9. The asymmetry decides the rest: a refusal costs the
    composer one clarifying question, a false positive costs a wrong value
    in a published listing, so where the two are close the default refuses.
    A deployment that has logged its own scores should tune this; a caller
    that still shows a human the answer can pass a lower ``min_score``.
    """
    from .conf import real
    from .models import Vocabulary
    from .slug import slugify_term

    vocabulary = Vocabulary.objects.filter(slug=payload["vocabulary"]).first()
    if vocabulary is None:
        return {"matched": False, "reason": "unknown_vocabulary"}
    level = payload["level"]
    if not vocabulary.has_level(level):
        return {"matched": False, "reason": "unknown_level"}

    text = (payload.get("text") or "").strip()
    if not text:
        return _no_match()
    floor = payload.get("min_score")
    floor = real("MATCH_MIN_SCORE") if floor is None else float(floor)

    scope = _match_scope(vocabulary, level, payload.get("parent"))
    if scope is None:
        return _no_match()

    # --- exact
    if floor <= 1.0:
        from django.db.models import Q

        exact = list(
            scope.filter(
                Q(label__iexact=text)
                | Q(code__iexact=text)
                | Q(code=slugify_term(text))
            ).values("code", "label")[:1]
        )
        if exact:
            return _hit(exact[0], 1.0, "exact")

    # --- unique prefix
    if floor <= PREFIX_SCORE:
        prefixed = list(
            scope.filter(label__istartswith=text).values("code", "label")[:2]
        )
        if len(prefixed) == 1:
            return _hit(prefixed[0], PREFIX_SCORE, "prefix")

    # --- the vector seam
    return _match_by_vector(scope, text, floor)


def _match_by_vector(scope, text, floor) -> dict:
    """The similarity rung, degrading to a refusal on anything unexpected.

    Nothing here may raise past the Function boundary. An unconfigured seam,
    a provider that is down, a malformed answer and a genuinely low score
    are one answer to this caller — "no", promptly — and a composer that
    500s because a vector index is rebuilding is a composer that stops
    composing.
    """
    from .vector import similar_scored

    try:
        neighbours = similar_scored(text, "", limit=5)
    except Exception:  # noqa: BLE001 - the seam already logs; this is the floor
        logger.warning(
            "the vector seam raised while matching %r; answering no match",
            text,
            exc_info=True,
        )
        return _no_match()
    # Score first, scope second: an unscored or low neighbour is not worth a
    # query, and the caller cannot use it either way.
    labels = [
        label
        for label, score in neighbours
        if label and score is not None and score >= floor
    ]
    if not labels:
        return _no_match()
    scores = {label: score for label, score in neighbours}
    rows = {
        row["label"]: row
        for row in scope.filter(label__in=labels).values("code", "label")
    }
    for label in labels:
        row = rows.get(label)
        if row is not None:
            return _hit(row, scores[label], "vector")
    return _no_match()


__all__ = [
    "PREFIX_SCORE",
    "describe_function",
    "match_function",
    "resolve_function",
    "set_popularity_function",
]
