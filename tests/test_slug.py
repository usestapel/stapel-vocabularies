"""Term codes are persisted values, so the slugger is pinned (spec §3.6).

A code ends up inside a saved listing. Changing how one is derived renames
data that is already stored, so the properties below are a contract and not
an implementation detail: Cyrillic survives, the charset is closed, the
length is bounded, and two labels that slug alike are numbered in label sort
order rather than in the order a parser met them.
"""
import pytest

from stapel_vocabularies.slug import (
    MAX_CODE_LENGTH,
    dedupe_code_list,
    dedupe_codes,
    slugify_term,
)


@pytest.mark.parametrize(
    "label, code",
    [
        ("Apple", "apple"),
        ("iPhone 10", "iphone-10"),
        ("  Galaxy  S10  ", "galaxy-s10"),
        ("чёрный", "chernyy"),
        ("Чёрный", "chernyy"),
        ("Жёлто-зелёный", "zhelto-zelenyy"),
        ("Щука", "schuka"),
        ("16 ГБ", "16-gb"),
        ("Xiaomi/Redmi", "xiaomi-redmi"),
        ("---", "t-58b63e27"),
    ],
)
def test_known_labels_slug_to_known_codes(label, code):
    assert slugify_term(label) == code


def test_every_code_is_in_the_closed_charset():
    for label in ["Ёлка!", "A_B", "50%", "Ⅻ", "🙂", "  "]:
        code = slugify_term(label)
        assert code
        assert all(ch in "abcdefghijklmnopqrstuvwxyz0123456789-" for ch in code), code


def test_a_label_with_nothing_sluggable_still_gets_a_distinct_code():
    """Two unsluggable labels must not collapse onto one code."""
    assert slugify_term("—") != slugify_term("№")


def test_a_long_label_is_truncated_deterministically():
    label = "Очень длинное название модели " * 20
    code = slugify_term(label)
    assert len(code) <= MAX_CODE_LENGTH
    assert code == slugify_term(label)
    # Truncation keeps a digest, so two labels sharing a 119-character prefix
    # do not become the same code.
    other = label + " Plus"
    assert slugify_term(other) != code


def test_duplicates_are_numbered_in_label_sort_order():
    """Numbering follows the level's contents, not the parser's walk order."""
    labels = ["iPhone-10", "iPhone 10", "iPhone_10"]
    forward = dedupe_codes(labels)
    backward = dedupe_codes(list(reversed(labels)))
    assert forward == backward
    assert sorted(forward.values()) == ["iphone-10", "iphone-10-2", "iphone-10-3"]
    # "iPhone 10" sorts first among the three, so it keeps the bare code.
    assert forward["iPhone 10"] == "iphone-10"


def test_a_suffix_never_steals_another_labels_own_code():
    """The collision the real phone catalogue contains.

    "iPhone 10" takes `iphone-10`; "iPhone-10" wants it too and must not be
    handed `iphone-10-2`, because "iPhone 10 2" slugs to exactly that. A
    per-base counter gets this wrong and the duplicate only surfaces as an
    IntegrityError 12 000 rows into a bulk insert.
    """
    codes = dedupe_codes(["iPhone 10", "iPhone-10", "iPhone 10 2"])
    assert len(set(codes.values())) == 3
    assert codes["iPhone 10"] == "iphone-10"
    assert codes["iPhone 10 2"] == "iphone-10-2"
    assert codes["iPhone-10"] == "iphone-10-3"


def test_a_deduped_code_still_fits_the_column():
    long_label = "х" * 200
    labels = [long_label + suffix for suffix in ("", "!", "?")]
    for code in dedupe_codes(labels).values():
        assert len(code) <= MAX_CODE_LENGTH
    assert len(set(dedupe_codes(labels).values())) == 3


def test_dedupe_code_list_keeps_input_order():
    assert dedupe_code_list(["B", "A", "B"]) == ["b", "a", "b"]
