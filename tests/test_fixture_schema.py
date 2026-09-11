"""``docs/vocabulary-fixture.schema.json`` against the loader that reads it.

Two gates on one contract, and they are not the same gate. The schema is what
an IMPORTER writes to — a catalogue converter in another repository, another
language if it likes — and it is the only thing that repository can read. The
loader's ``validate_fixture`` is what the READER accepts, and it deliberately
does not call jsonschema: that library is a test dependency, and a gate that
only runs where an optional library happens to be installed runs nowhere that
matters (``loader.py``).

Two independent statements of one contract drift. So every shape below is put
to BOTH, and they must agree: a file an importer is told is valid must load,
and a file the loader refuses must not have been called valid.
"""
import json
from pathlib import Path

import pytest

from stapel_vocabularies.loader import FixtureError, validate_fixture

jsonschema = pytest.importorskip("jsonschema")

SCHEMA = json.loads(
    (Path(__file__).resolve().parent.parent / "docs" / "vocabulary-fixture.schema.json")
    .read_text(encoding="utf-8")
)


def fixture(*terms):
    return {
        "slug": "tints",
        "name": "Tints",
        "levels": [{"name": "Tint"}],
        "terms": [list(row) for row in terms],
    }


def accepted_by_schema(candidate) -> bool:
    try:
        jsonschema.validate(candidate, SCHEMA)
    except jsonschema.ValidationError:
        return False
    return True


def accepted_by_loader(candidate) -> bool:
    try:
        validate_fixture(candidate)
    except FixtureError:
        return False
    return True


#: Every term-row width the contract has ever had, plus the two ways to get
#: the newest one wrong. ``valid`` is what BOTH sides must say.
ROWS = [
    (["Tint", "ink", "Ink", None], True, "the original four columns"),
    (["Tint", "ink", "Ink", None, 3], True, "with the sort rank (0.1.4)"),
    (["Tint", "ink", "Ink", None, 3, 7], True, "with the popular band (0.2.0)"),
    (
        ["Tint", "ink", "Ink", None, 3, 7, {"hue": "#1a1a1a"}],
        True,
        "with the source's own bag (0.4.0)",
    ),
    (["Tint", "ink", "Ink", None, 3, 7, {}], True, "an empty bag states nothing"),
    (
        ["Tint", "ink", "Ink", None, 3, 7, "#1a1a1a"],
        False,
        "a bare value is not a bag of named attributes",
    ),
    (
        ["Tint", "ink", "Ink", None, 3, 7, {}, "surplus"],
        False,
        "there is no eighth column",
    ),
]


@pytest.mark.parametrize(
    "row,valid,because", ROWS, ids=[case[2] for case in ROWS]
)
def test_the_schema_and_the_loader_agree_on_a_term_row(row, valid, because):
    candidate = fixture(row)
    assert accepted_by_schema(candidate) is valid, f"schema disagrees: {because}"
    assert accepted_by_loader(candidate) is valid, f"loader disagrees: {because}"


def test_a_pre_040_fixture_is_still_a_valid_fixture():
    """The whole reason the column is optional: no file has to be rewritten."""
    historical = fixture(
        ["Tint", "ink", "Ink", None],
        ["Tint", "snow", "Snow", "S", 1],
        ["Tint", "slate", "Slate", None, 2, 5],
    )
    assert accepted_by_schema(historical)
    assert accepted_by_loader(historical)
