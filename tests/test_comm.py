"""The comm surface: two Functions and one Event (spec §3.3).

These are what a service WITHOUT the tables asks — so what matters is that
they answer completely (one round trip per validation, not three) and that
their payloads match the committed schemas. The suite runs with
``VALIDATE_SCHEMAS`` on, so every call below is also a schema check.
"""
import json
from pathlib import Path

import pytest
from stapel_core.comm import call

pytestmark = pytest.mark.django_db

SCHEMAS = Path(__file__).resolve().parent.parent / "schemas"


# --- describe ---------------------------------------------------------------


def test_describe_answers_the_levels_and_the_revision(phones):
    answer = call("vocabularies.describe", {"vocabulary": "phones"})
    assert answer == {
        "slug": "phones",
        "levels": [
            {"name": "Vendor", "parent": None},
            {"name": "Model", "parent": "Vendor"},
            {"name": "Color", "parent": "Model"},
        ],
        "revision": phones.revision,
    }


def test_describe_of_an_unknown_vocabulary_is_none(phones):
    assert call("vocabularies.describe", {"vocabulary": "nope"}) is None


# --- resolve ----------------------------------------------------------------


def test_resolve_answers_existence_and_labels_for_every_code(phones):
    answer = call(
        "vocabularies.resolve",
        {"vocabulary": "phones", "level": "Model", "codes": ["iphone-10", "nope"]},
    )
    assert answer["exists"] == {"iphone-10": True, "nope": False}
    assert answer["labels"] == {"iphone-10": "iPhone 10"}
    # No parent was named, so parentage is unanswered rather than answered no.
    assert answer["is_child"] is None


def test_resolve_checks_parentage_when_a_parent_is_named(phones):
    answer = call(
        "vocabularies.resolve",
        {
            "vocabulary": "phones",
            "level": "Model",
            "codes": ["iphone-10", "galaxy-s10"],
            "parent": {"level": "Vendor", "code": "apple"},
        },
    )
    assert answer["is_child"] == {"iphone-10": True, "galaxy-s10": False}


def test_an_unknown_parent_makes_every_code_not_a_child(phones):
    answer = call(
        "vocabularies.resolve",
        {
            "vocabulary": "phones",
            "level": "Model",
            "codes": ["iphone-10"],
            "parent": {"level": "Vendor", "code": "nokia"},
        },
    )
    assert answer["is_child"] == {"iphone-10": False}


def test_resolve_prefers_a_translated_label_when_asked(phones):
    from stapel_vocabularies.models import Term

    Term.objects.filter(code="chernyy").update(labels={"en": "black"})
    answer = call(
        "vocabularies.resolve",
        {
            "vocabulary": "phones",
            "level": "Color",
            "codes": ["chernyy"],
            "language": "en",
        },
    )
    assert answer["labels"] == {"chernyy": "black"}


def test_duplicate_codes_collapse(phones):
    answer = call(
        "vocabularies.resolve",
        {"vocabulary": "phones", "level": "Model", "codes": ["iphone-10", "iphone-10"]},
    )
    assert answer["exists"] == {"iphone-10": True}


def test_an_empty_code_list_is_an_empty_answer(phones):
    answer = call(
        "vocabularies.resolve",
        {"vocabulary": "phones", "level": "Model", "codes": []},
    )
    assert answer == {"exists": {}, "labels": {}, "is_child": None}


def test_a_payload_the_schema_refuses_does_not_reach_the_function(phones):
    """VALIDATE_SCHEMAS is on: the contract is enforced, not documented."""
    with pytest.raises(Exception):
        call(
            "vocabularies.resolve",
            {"vocabulary": "phones", "level": "Model", "codes": ["x"], "extra": 1},
        )


# --- children ---------------------------------------------------------------


def test_children_lists_only_the_terms_the_parent_scopes(phones):
    """The whole point of the read: a page a caller can reason ABOUT, scoped
    the way the picker scopes it."""
    answer = call(
        "vocabularies.children",
        {
            "vocabulary": "phones",
            "level": "Model",
            "parent": {"level": "Vendor", "code": "apple"},
        },
    )
    assert sorted(row["code"] for row in answer["results"]) == [
        "iphone-10",
        "iphone-11",
    ]
    assert {row["label"] for row in answer["results"]} == {"iPhone 10", "iPhone 11"}
    assert answer["truncated"] is False


def test_children_with_no_parent_lists_the_whole_level(phones):
    answer = call("vocabularies.children", {"vocabulary": "phones", "level": "Vendor"})
    assert sorted(row["code"] for row in answer["results"]) == ["apple", "samsung"]


def test_a_parent_that_names_no_term_scopes_nothing(phones):
    """Never the whole level. "That vendor is not here" and "it has no
    models" are the same refusal to this caller — and an unscoped level is
    how a value from under the wrong parent gets written into a listing."""
    answer = call(
        "vocabularies.children",
        {
            "vocabulary": "phones",
            "level": "Model",
            "parent": {"level": "Vendor", "code": "nokia"},
        },
    )
    assert answer == {"results": [], "truncated": False}


def test_an_unknown_level_is_none_not_an_empty_page(phones):
    """A re-import that renames a level must not read as "this parent has no
    children" — that is a silent success on a question nobody answered."""
    assert call(
        "vocabularies.children", {"vocabulary": "phones", "level": "Gone"}
    ) is None
    assert call(
        "vocabularies.children", {"vocabulary": "nope", "level": "Vendor"}
    ) is None


def test_a_page_that_was_cut_short_says_so(phones):
    """`truncated` is what stops a caller concluding "exactly one" from a
    page the store cut short. Apple has two models; asking for one says both
    that there is one row and that there is more."""
    answer = call(
        "vocabularies.children",
        {
            "vocabulary": "phones",
            "level": "Model",
            "parent": {"level": "Vendor", "code": "apple"},
            "limit": 1,
        },
    )
    assert len(answer["results"]) == 1
    assert answer["truncated"] is True


# --- the event --------------------------------------------------------------


def test_a_load_emits_exactly_one_vocabulary_changed(captured_events, phones):
    """The `phones` fixture is one load, so this is one event."""
    assert [event.payload for event in captured_events] == [
        {"slug": "phones", "revision": phones.revision}
    ]


def test_the_emit_schema_is_committed():
    schema = json.loads(
        (SCHEMAS / "emits" / "vocabulary.changed.json").read_text(encoding="utf-8")
    )
    assert schema["required"] == ["slug", "revision"]
    assert schema["additionalProperties"] is False


@pytest.mark.parametrize(
    "name",
    ["vocabularies.resolve", "vocabularies.describe", "vocabularies.children"],
)
def test_every_function_carries_its_schema(name):
    schema = json.loads(
        (SCHEMAS / "functions" / f"{name}.json").read_text(encoding="utf-8")
    )
    assert schema["title"] == name
    assert schema["additionalProperties"] is False
