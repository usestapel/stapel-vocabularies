"""The vector seam: corpus provider out, similarity lookups in.

Two halves, both optional, both empty by default:

- :func:`label_corpus` is the corpus PROVIDER a deployment registers in
  ``STAPEL_SEARCH["VECTOR_CORPORA"]`` under the ``vocab_label`` kind: the
  distinct labels of the levels named by ``VECTOR_LABEL_LEVELS`` (glob
  patterns — ``brand*``, ``marka*``), which is where the typo problem
  lives. Scope is deliberate: an 800k-term store embedded wholesale is
  mostly strings nobody types; the levels people type toward are a corpus
  a thousand times smaller.
- :func:`similar_labels` is the CONSUMER: the term typeahead's question to
  ``VECTOR_SIMILAR_FUNCTION`` (``search.similar``) when its own
  deterministic answer came back thin. This module never learns what an
  embedding is — it sends the raw query and gets labels back, best first,
  floor already applied on the other side.

The split mirrors QUERY_EXPANDER exactly: the fleet keeps ONE similarity
layer, it lives in the search library, and this module consumes it through
a name rather than an import.
"""
from __future__ import annotations

import hashlib
import logging
from fnmatch import fnmatchcase

logger = logging.getLogger(__name__)


def _setting(key: str, default):
    from .conf import vocabularies_settings

    try:
        return getattr(vocabularies_settings, key)
    except AttributeError:
        return default


def label_corpus():
    """``{"key", "text", "payload"}`` per distinct label of the configured
    levels — the ``VECTOR_CORPORA`` provider contract."""
    from .models import Term

    patterns = [str(p).casefold() for p in (_setting("VECTOR_LABEL_LEVELS", ()) or ())]
    if not patterns:
        return
    levels = [
        level
        for level in Term.objects.values_list("level", flat=True).distinct()
        if any(fnmatchcase(level.casefold(), pattern) for pattern in patterns)
    ]
    if not levels:
        return
    seen: set[str] = set()
    for label in (
        Term.objects.filter(level__in=levels)
        .values_list("label", flat=True)
        .distinct()
        .iterator()
    ):
        folded = label.casefold()
        if not folded or folded in seen:
            continue
        seen.add(folded)
        yield {
            "key": hashlib.sha1(folded.encode("utf-8")).hexdigest(),
            "text": label,
            "payload": {},
        }


def similar_labels(query: str, language: str, *, limit: int) -> list[str]:
    """Labels an embedding space places near *query*, best first.

    Empty on every failure — an unreachable provider, a degraded answer, a
    disabled flag on the far side. The typeahead loses recall, never the
    response; same posture as a QUERY_EXPANDER that raises.
    """
    from stapel_core.comm import call

    name = _setting("VECTOR_SIMILAR_FUNCTION", "")
    if not name:
        return []
    try:
        answer = call(
            name,
            {
                "kind": str(_setting("VECTOR_KIND", "vocab_label")),
                "q": query,
                "language": language,
                "limit": int(limit),
            },
        )
    except Exception:  # noqa: BLE001 - a keystroke must not 500 on the net
        logger.warning("%s raised on %r; skipping the vector net", name, query,
                       exc_info=True)
        return []
    return [
        str(hit.get("text") or "")
        for hit in (answer or {}).get("results") or []
        if hit.get("text")
    ]


__all__ = ["label_corpus", "similar_labels"]
