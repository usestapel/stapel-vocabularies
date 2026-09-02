"""The default query expander — the standalone floor of ``QUERY_EXPANDER``.

The fleet keeps ONE cross-script normalization layer (folding, RU↔EN
transliteration, curated alias groups) and it lives in the search library,
not here. This module must stand alone, so its own expander is deliberately
trivial: the literal query and nothing else — a standalone install matches
byte-for-byte what it matched before the seam existed. A deployment that
runs the search library points ``STAPEL_VOCABULARIES["QUERY_EXPANDER"]`` at
``stapel_search.suggest.query_terms`` and both surfaces speak the same
variants; growing a second alias table in this file is exactly the fork the
seam exists to prevent.

Contract of any expander: ``(query: str, language: str) -> Sequence[str]``,
the literal query included. *language* is the language the labels resolve
for — the best ``Accept-Language`` tag, empty when the client states none.
"""


def literal(query: str, language: str) -> tuple[str, ...]:
    """The identity expansion: match exactly what was typed."""
    return (query,)


__all__ = ["literal"]
