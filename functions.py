"""comm surface of stapel-vocabularies (spec §3.3).

Two Functions, both read-only, both carrying a JSON schema in ``schemas/`` —
tests run with ``VALIDATE_SCHEMAS`` on, so a payload drifting from its schema
fails loudly. Registration happens on import from ``apps.py:ready()``.

They exist so a service that holds NO vocabulary tables can still validate a
``ref_select`` value: ``stapel_vocabularies.resolver.CommResolver`` is exactly
these two calls behind the ``VocabularyResolver`` protocol.

    from stapel_core.comm import call

    call("vocabularies.resolve", {"vocabulary": "avito-phones", "level": "Model",
                                  "codes": ["iphone-10"],
                                  "parent": {"level": "Vendor", "code": "apple"}})
    # -> {"exists": {"iphone-10": True}, "labels": {"iphone-10": "iPhone 10"},
    #     "is_child": {"iphone-10": True}}

    call("vocabularies.describe", {"vocabulary": "avito-phones"})
    # -> {"slug": "avito-phones", "levels": [{"name": "Vendor", "parent": None}, ...],
    #     "revision": 7}
"""
import json
from pathlib import Path

from stapel_core.comm import function

_SCHEMAS_DIR = Path(__file__).resolve().parent / "schemas" / "functions"


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


__all__ = ["describe_function", "resolve_function"]
