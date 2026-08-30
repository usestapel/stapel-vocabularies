"""``VocabularyResolver`` implementations (spec §3.1, §3.3).

The protocol is declared in stapel-attributes (L1) and answered here (L2) in
two shapes:

* ``OrmResolver`` — same process as the tables. Registered by
  ``AppConfig.ready()`` unless ``STAPEL_VOCABULARIES["REGISTER_RESOLVER"]``
  says otherwise.
* ``CommResolver`` — for a service that validates ``ref_select`` values but
  holds no vocabulary tables. Points at the two comm Functions; a host puts
  its dotted path in ``STAPEL_ATTRIBUTES["VOCABULARY_RESOLVER"]``.

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

import threading
import time
from typing import Dict, Optional, Sequence, Tuple

from .conf import number


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


__all__ = ["CommResolver", "OrmResolver", "register_orm_resolver"]
