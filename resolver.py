"""``VocabularyResolver`` implementations (spec §3.1, §3.3).

The protocol is declared in stapel-attributes (L1) and answered here (L2) in
two shapes:

* ``OrmResolver`` — same process as the tables. Registered by
  ``AppConfig.ready()`` unless ``STAPEL_VOCABULARIES["REGISTER_RESOLVER"]``
  says otherwise.
* ``CommResolver`` — for a service that validates ``ref_select`` values but
  holds no vocabulary tables. Points at the comm Functions; a host puts its
  dotted path in ``STAPEL_ATTRIBUTES["VOCABULARY_RESOLVER"]``.

Both also answer ``terms(vocabulary, level)``, which the protocol does NOT
declare — the OPTIONAL listing reader stapel-categories (>= 0.22) generates a
category's virtual children with. Both go through ``level_terms()``, so the
two implementations cannot answer "what are this category's children"
differently. ``terms_with_extra`` is the same read widened to
``(code, label, extra)`` for a caller that renders the source catalogue's own
per-term attributes — a facet drawing a colour swatch from ``extra["hue"]``.
It is a second method, not a wider ``terms``, because the pair is what every
0.3.0 caller unpacks.

Both cache ``describe`` **by revision**, never by wall clock alone: a level
list is read on every config validation, and a re-imported catalogue must stop
validating against the levels it used to have the moment the import commits.
The ORM side revalidates with one ``values_list("revision")``; the comm side
gets the revision in the describe answer and drops its entry when
``vocabulary.changed`` arrives.

stapel-attributes is imported lazily, inside the methods that need its
dataclasses, so importing this module costs nothing and a checkout without the
0.5.0 symbols still imports (``checks.py`` reports that deployment at boot).
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Dict, List, Optional, Sequence, Tuple

from .conf import number

logger = logging.getLogger(__name__)


def _types():
    """The L1 dataclasses, imported at call time.

    A module-level import would make every import of this package depend on a
    stapel-attributes release, and would turn a floor violation into an
    ImportError at Django startup instead of a system check that says what to
    do about it.
    """
    from stapel_attributes.vocabularies import VocabularyInfo, VocabularyLevel

    return VocabularyInfo, VocabularyLevel


def _build_info(slug: str, levels):
    VocabularyInfo, VocabularyLevel = _types()
    return VocabularyInfo(
        slug=slug,
        levels=tuple(
            VocabularyLevel(name=level["name"], parent=level.get("parent"))
            for level in levels or []
        ),
    )


#: ``(vocabulary, level)`` pairs whose size has already been reported. The cap
#: is a property of the catalogue, not of the request, so saying so once per
#: process is the whole signal — a tree read that draws a capped level draws it
#: on every page view, and a log line per view buries the one that matters.
_reported_caps = set()
_reported_lock = threading.Lock()


def _report_cap(vocabulary: str, level: str, cap: int) -> None:
    key = (vocabulary, level)
    with _reported_lock:
        if key in _reported_caps:
            return
        _reported_caps.add(key)
    logger.warning(
        "vocabulary %r level %r holds more than STAPEL_VOCABULARIES"
        "['TERMS_LIMIT'] (%d) terms; terms() answers the first %d. A level "
        "this large is not a browse level — expand the category by a coarser "
        "level instead.",
        vocabulary,
        level,
        cap,
        cap,
    )


def level_terms(
    vocabulary: str,
    level: str,
    parent: Optional[str] = None,
    limit: Optional[int] = None,
    with_extra: bool = False,
) -> Optional[Tuple[List[tuple], bool]]:
    """``([(code, label)], truncated)`` for one level — or ``None``.

    ONE implementation behind two shapes: the in-process ``terms()`` reader
    and the ``vocabularies.terms`` Function that serves the same reader in a
    service without the tables. Two copies of this query would be two answers
    to "what are this category's children", and the whole point of the seam is
    that a fleet cannot tell which side answered.

    ``None`` — not an empty list — for an unknown vocabulary or an unknown
    level, the way ``describe``, ``children`` and ``set_popularity`` already
    say "no such thing". The distinction is kept HERE and flattened by each
    caller that has to flatten it (``terms()`` answers ``[]``, because its
    consumer reads "no values" from anything else).

    Order is the level's own — ``Term.Meta.ordering``: the popular band
    first, then the fixture's curated rank, then the label. A deployment that
    has promoted nothing gets the alphabet, which is what "the vocabulary's
    own order, and its labels when it has none" means.

    ``parent`` is a term CODE at the level above ``level`` (the level chain
    says which), not a level/code pair: the caller browsing a hierarchy holds
    the code it descended through and nothing else. A parent naming no term —
    and any parent at all on a root level — scopes NOTHING and answers an
    empty list rather than the whole level, the rule ``_match_scope`` and
    ``children_function`` already state.

    ``with_extra`` widens the rows to ``(code, label, extra)`` — the source
    catalogue's own per-term bag (``Term.extra``), ``{}`` for a term carrying
    none. It is a separate argument rather than a wider default because the
    pair is what every 0.3.0 caller unpacks positionally, here and in
    stapel-categories; a third element that arrives unasked is a caller's
    ``ValueError``, not a new feature.
    """
    from .models import Term, Vocabulary

    row = (
        Vocabulary.objects.filter(slug=vocabulary).values("id", "levels").first()
    )
    if row is None:
        return None
    levels = row["levels"] or []
    if level not in [entry["name"] for entry in levels]:
        return None

    terms = Term.objects.filter(vocabulary_id=row["id"], level=level)
    if parent not in (None, ""):
        parent_level = None
        for entry in levels:
            if entry["name"] == level:
                parent_level = entry.get("parent")
                break
        if not parent_level:
            return [], False
        parent_id = (
            Term.objects.filter(
                vocabulary_id=row["id"], level=parent_level, code=parent
            )
            .values_list("id", flat=True)
            .first()
        )
        if parent_id is None:
            return [], False
        terms = terms.filter(parent_edges__parent_id=parent_id)

    cap = number("TERMS_LIMIT")
    wanted = cap if limit is None else max(1, min(int(limit), cap))
    # One row more than asked for, never returned: `truncated` without a
    # second COUNT over the same set (`children_function`'s trick).
    columns = ("code", "label", "extra") if with_extra else ("code", "label")
    rows = list(terms.values_list(*columns)[: wanted + 1])
    truncated = len(rows) > wanted
    if truncated and wanted == cap:
        _report_cap(vocabulary, level, cap)
    if with_extra:
        return [
            (code, label, extra if isinstance(extra, dict) else {})
            for code, label, extra in rows[:wanted]
        ], truncated
    return [(code, label) for code, label in rows[:wanted]], truncated


class OrmResolver:
    """Answers from this process's own tables."""

    def __init__(self):
        self._lock = threading.Lock()
        #: slug -> (revision, VocabularyInfo)
        self._described: Dict[str, Tuple[int, object]] = {}

    # --- VocabularyResolver ------------------------------------------------

    def describe(self, vocabulary: str):
        from .models import Vocabulary

        row = (
            Vocabulary.objects.filter(slug=vocabulary)
            .values("levels", "revision")
            .first()
        )
        if row is None:
            with self._lock:
                self._described.pop(vocabulary, None)
            return None
        cached = self._described.get(vocabulary)
        if cached is not None and cached[0] == row["revision"]:
            return cached[1]
        info = _build_info(vocabulary, row["levels"])
        with self._lock:
            self._described[vocabulary] = (row["revision"], info)
        return info

    def exists(self, vocabulary: str, level: str, code: str) -> bool:
        from .models import Term

        return Term.objects.filter(
            vocabulary__slug=vocabulary, level=level, code=code
        ).exists()

    def is_child(
        self,
        vocabulary: str,
        level: str,
        code: str,
        parent_level: str,
        parent_code: str,
    ) -> bool:
        from .models import TermEdge

        return TermEdge.objects.filter(
            parent__vocabulary__slug=vocabulary,
            parent__level=parent_level,
            parent__code=parent_code,
            child__level=level,
            child__code=code,
        ).exists()

    def labels(
        self, vocabulary: str, level: str, codes: Sequence[str]
    ) -> Dict[str, str]:
        from .models import Term

        wanted = list(dict.fromkeys(codes or []))
        if not wanted:
            return {}
        rows = Term.objects.filter(
            vocabulary__slug=vocabulary, level=level, code__in=wanted
        ).values_list("code", "label")
        return dict(rows)

    # --- the optional listing reader ---------------------------------------

    def terms(
        self,
        vocabulary: str,
        level: str,
        *,
        parent: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Tuple[str, str]]:
        """``[(code, label)]`` of one level — the reader ``VocabularyResolver``
        does not declare.

        The protocol is four questions about ONE code, deliberately: listing
        belongs to the HTTP surface a typeahead talks to. But a category whose
        ``children_expand_by`` names a ``ref_select`` feature has no code to
        ask about — its children ARE the level — and stapel-categories
        (>= 0.22) reads that through this OPTIONAL method, drawing no virtual
        children at all when the registered resolver lacks it. This is that
        method; adding it is what turns those categories from empty into a
        branch.

        Labels are the ``label`` column, exactly as ``labels()`` resolves
        them — one language question, answered in one place. A caller wanting
        a translated set asks the HTTP surface, which takes
        ``Accept-Language``.

        ``[]`` for an unknown vocabulary or level, and never a raise: the
        consumer treats a raise as "no values" while logging a traceback per
        tree read, so an honest empty list is the only useful answer to a
        question about a catalogue this deployment does not have.

        Capped at ``STAPEL_VOCABULARIES["TERMS_LIMIT"]`` (2000): over it the
        first N come back and the level is reported once. A level larger than
        the cap is not a browse level.
        """
        answer = level_terms(vocabulary, level, parent=parent, limit=limit)
        return [] if answer is None else answer[0]

    def terms_with_extra(
        self,
        vocabulary: str,
        level: str,
        *,
        parent: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Tuple[str, str, dict]]:
        """``[(code, label, extra)]`` — ``terms()`` plus the source's own bag.

        Same query, same order, same cap, same ``[]`` for a catalogue this
        deployment does not have. The only difference is the third element:
        ``Term.extra``, the attributes the SOURCE catalogue owns, ``{}`` for
        a term carrying none.

        A separate method rather than a wider ``terms()`` because ``terms()``
        is a shipped shape: stapel-categories and ``CommResolver`` both
        unpack its rows, and a 0.3.0 checkout of either unpacks exactly two
        elements. The caller that wants the bag — a facet drawing a colour
        swatch from ``extra["hue"]`` — asks for it by name.
        """
        answer = level_terms(
            vocabulary, level, parent=parent, limit=limit, with_extra=True
        )
        return [] if answer is None else answer[0]


class CommResolver:
    """Answers over ``stapel_core.comm`` — for a service without the tables."""

    def __init__(self):
        self._lock = threading.Lock()
        #: slug -> (revision, VocabularyInfo, fetched_at)
        self._described: Dict[str, Tuple[int, object, float]] = {}
        self._subscribed = False

    # --- cache -------------------------------------------------------------

    def _subscribe(self) -> None:
        """Drop a cached describe when its vocabulary is re-imported.

        Subscribed on first use rather than in ``__init__``: this class is
        instantiated by ``import_string`` from a settings seam, and a library
        must not touch the bus while settings are still being read.
        """
        if self._subscribed:
            return
        with self._lock:
            if self._subscribed:
                return
            self._subscribed = True
        try:
            from stapel_core.comm import subscribe_action
        except ImportError:  # pragma: no cover - core always ships comm
            return
        subscribe_action("vocabulary.changed", self._on_changed)

    def _on_changed(self, event) -> None:
        payload = getattr(event, "payload", None) or {}
        slug = payload.get("slug")
        with self._lock:
            if slug:
                self._described.pop(slug, None)
            else:
                self._described.clear()

    def _call(self, name: str, payload: dict):
        from stapel_core.comm import call

        return call(name, payload)

    # --- VocabularyResolver ------------------------------------------------

    def describe(self, vocabulary: str):
        self._subscribe()
        ttl = number("RESOLVER_CACHE_TTL_SECONDS")
        cached = self._described.get(vocabulary)
        if cached is not None and (time.monotonic() - cached[2]) < ttl:
            return cached[1]
        answer = self._call("vocabularies.describe", {"vocabulary": vocabulary})
        if not answer:
            with self._lock:
                self._described.pop(vocabulary, None)
            return None
        revision = int(answer.get("revision") or 0)
        if cached is not None and cached[0] == revision:
            info = cached[1]
        else:
            info = _build_info(answer["slug"], answer.get("levels") or [])
        with self._lock:
            self._described[vocabulary] = (revision, info, time.monotonic())
        return info

    def exists(self, vocabulary: str, level: str, code: str) -> bool:
        answer = self._call(
            "vocabularies.resolve",
            {"vocabulary": vocabulary, "level": level, "codes": [code]},
        )
        return bool((answer or {}).get("exists", {}).get(code))

    def is_child(
        self,
        vocabulary: str,
        level: str,
        code: str,
        parent_level: str,
        parent_code: str,
    ) -> bool:
        answer = self._call(
            "vocabularies.resolve",
            {
                "vocabulary": vocabulary,
                "level": level,
                "codes": [code],
                "parent": {"level": parent_level, "code": parent_code},
            },
        )
        child_map = (answer or {}).get("is_child") or {}
        return bool(child_map.get(code))

    def labels(
        self, vocabulary: str, level: str, codes: Sequence[str]
    ) -> Dict[str, str]:
        wanted = list(dict.fromkeys(codes or []))
        if not wanted:
            return {}
        answer = self._call(
            "vocabularies.resolve",
            {"vocabulary": vocabulary, "level": level, "codes": wanted},
        )
        return dict((answer or {}).get("labels") or {})

    # --- the optional listing reader ---------------------------------------

    def terms(
        self,
        vocabulary: str,
        level: str,
        *,
        parent: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Tuple[str, str]]:
        """``[(code, label)]`` of one level, over the bus.

        The same answer ``OrmResolver.terms`` gives, through
        ``vocabularies.terms`` — see it for the contract. Not cached: a
        describe is a shape that changes once per import, a level's term list
        is the body of a page, and this reader is called behind a tree read
        that has its own ETag.

        The Function's ``null`` for an unknown vocabulary or level flattens to
        ``[]`` here, so both implementations answer a consumer that reads
        anything but a list of pairs as "no values" the same way.
        """
        answer = self._terms_answer(vocabulary, level, parent, limit)
        rows = (answer or {}).get("terms") or []
        return [(str(code), str(label)) for code, label in rows]

    def terms_with_extra(
        self,
        vocabulary: str,
        level: str,
        *,
        parent: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Tuple[str, str, dict]]:
        """``[(code, label, extra)]`` over the bus — see ``OrmResolver``.

        The bag travels in the reply's OWN key, ``extras`` (``{code: {...}}``,
        only the terms that carry one), not as a third element of each
        ``terms`` row. A third element would be read by a 0.3.0
        ``CommResolver.terms`` — which unpacks ``for code, label in rows`` —
        as a ``ValueError`` per browse read, so the newest service in a fleet
        would break the oldest one by existing. A key an old reader has never
        heard of is simply not read.

        A 0.3.0 server answers no ``extras`` at all, and every term then
        carries ``{}``: the facet draws no swatches until the catalogue side
        is upgraded, which is the honest answer to "this deployment has no
        hues".
        """
        answer = self._terms_answer(vocabulary, level, parent, limit) or {}
        extras = answer.get("extras") or {}
        rows = answer.get("terms") or []
        return [
            (
                str(code),
                str(label),
                dict(extras.get(code) or {}) if isinstance(extras, dict) else {},
            )
            for code, label in rows
        ]

    def _terms_answer(self, vocabulary, level, parent, limit):
        payload = {"vocabulary": vocabulary, "level": level}
        if parent not in (None, ""):
            payload["parent"] = str(parent)
        if limit is not None:
            payload["limit"] = int(limit)
        return self._call("vocabularies.terms", payload)


def register_orm_resolver() -> Optional[OrmResolver]:
    """Hand stapel-attributes the in-process resolver.

    Returns the registered instance, or ``None`` when the installed
    stapel-attributes predates the ``vocabularies`` protocol module — that
    deployment is reported by ``checks.py`` (W001) rather than crashed at
    startup, because a service can boot perfectly well while its ref-typed
    features are the part that will not validate.
    """
    try:
        from stapel_attributes.vocabularies import register_vocabulary_resolver
    except ImportError:
        return None
    resolver = OrmResolver()
    register_vocabulary_resolver(resolver)
    return resolver


__all__ = [
    "CommResolver",
    "OrmResolver",
    "level_terms",
    "register_orm_resolver",
]
