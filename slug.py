"""Deterministic term codes (spec §3.6).

A term's code is a transliterated slug of its label: ``[a-z0-9-]``, at most
128 characters, and stable across runs on any machine — a fixture is reviewed
as code, so the same catalogue must convert to the same bytes twice.

Django-free on purpose: ``django.utils.text.slugify`` drops Cyrillic
entirely (``slugify("Чёрный") == ""``), which would collapse every Russian
colour of a catalogue onto one empty code. The table below is the whole
mechanism and it is frozen: changing a letter renumbers codes that are
already stored as listing values.
"""
from __future__ import annotations

import hashlib
import re
from typing import Dict, Iterable, List, Sequence

#: Cyrillic -> Latin. Russian, plus the four Ukrainian letters that appear in
#: vendor catalogues. Frozen: these codes are persisted values.
TRANSLIT: Dict[str, str] = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    "і": "i", "ї": "yi", "є": "ye", "ґ": "g",
}

#: Maximum code length (``Term.code`` is CharField(128)).
MAX_CODE_LENGTH = 128

_NON_SLUG = re.compile(r"[^a-z0-9]+")
_DASHES = re.compile(r"-{2,}")


def transliterate(text: str) -> str:
    """Lowercase *text* and replace Cyrillic letters with their Latin forms."""
    lowered = text.casefold()
    return "".join(TRANSLIT.get(ch, ch) for ch in lowered)


def slugify_term(label: str) -> str:
    """``label`` -> a term code: ``[a-z0-9-]``, non-empty, <= 128 chars.

    A label that carries no sluggable character at all (``"—"``, ``"№"``, an
    emoji) still needs a code, and an empty one would collide with every
    other such label in the level. It gets ``t-<8 hex of sha1(label)>``:
    deterministic, in the charset, and visibly not a word.
    """
    slug = _NON_SLUG.sub("-", transliterate(label))
    slug = _DASHES.sub("-", slug).strip("-")
    if not slug:
        digest = hashlib.sha1(label.encode("utf-8")).hexdigest()[:8]
        return f"t-{digest}"
    if len(slug) > MAX_CODE_LENGTH:
        digest = hashlib.sha1(slug.encode("utf-8")).hexdigest()[:8]
        slug = f"{slug[:MAX_CODE_LENGTH - 9].rstrip('-')}-{digest}"
    return slug


def dedupe_codes(labels: Iterable[str]) -> Dict[str, str]:
    """Assign a unique code to every label of ONE level.

    Two different labels can slug to the same code (``"iPhone 10"`` and
    ``"iPhone-10"``). The second and later ones get ``-2``, ``-3``, … in
    **label sort order**, so the numbering depends on the level's contents
    and not on the order the parser happened to meet them in — the property
    that makes a re-converted fixture byte-identical.

    The suffix is chosen against every code ALREADY assigned in this level,
    not against a per-base counter: ``"iPhone 10"``, ``"iPhone-10"`` and
    ``"iPhone 10 2"`` all want ``iphone-10``-ish codes, and a counter kept per
    base would hand the second label ``iphone-10-2`` — the third label's own
    slug. The real phone catalogue contains that collision (measured on
    ``phone_catalog.xml``, 14 962 models), so this is not a hypothetical.

    Returns ``{label: code}``.
    """
    assigned: Dict[str, str] = {}
    taken: set = set()
    for label in sorted(set(labels)):
        base = slugify_term(label)
        code = base
        attempt = 1
        while code in taken:
            attempt += 1
            suffix = f"-{attempt}"
            trimmed = base[: MAX_CODE_LENGTH - len(suffix)].rstrip("-")
            code = f"{trimmed}{suffix}"
        taken.add(code)
        assigned[label] = code
    return assigned


def dedupe_code_list(labels: Sequence[str]) -> List[str]:
    """``dedupe_codes`` applied positionally, keeping the input order."""
    mapping = dedupe_codes(labels)
    return [mapping[label] for label in labels]


__all__ = [
    "MAX_CODE_LENGTH",
    "TRANSLIT",
    "dedupe_code_list",
    "dedupe_codes",
    "slugify_term",
    "transliterate",
]
