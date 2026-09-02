"""`vocabularies.match` — a free-text guess resolved, or honestly refused.

The caller is not a keyboard. It is a composer in another service asking
one question about one string it got out of a photo, a title or a language
model: *which term code is this, if any*. A typeahead may answer that
loosely — a human reads five rows and picks. A composer cannot: whatever
comes back is written into a listing, so a wrong code is silently wrong
data and a "no match" costs one clarifying question.

Which is why the read surface's vector net is not enough on its own. On a
live stand it answers `q='Самсунг'` with `[Samsung, Siemens]` — the right
term first — and `q='айфон'` with `[MyPhone, Fairphone, Elephone]`, all
three wrong, and the two rows are indistinguishable on the wire because
the listing DROPS the similarity score. This Function keeps the score and
compares it with a floor, and it returns `matched: false` for everything
that does not clear it, including the case where the far side hands back no
score at all: a confidence nobody measured is not a confidence.

The vector provider is a double here, scripted per test, exactly as
`tests/test_vector_fallback.py` does it — what is under test is the
ladder, the threshold and the failure posture, not an embedding space.
"""
import pytest
from django.test import override_settings
from stapel_core.comm import call, register_function
from stapel_core.comm.registry import function_registry

pytestmark = pytest.mark.django_db

VECTOR_ON = {"VECTOR_SIMILAR_FUNCTION": "search.similar"}


@pytest.fixture
def similar_provider():
    """A stand-in ``search.similar`` whose scores the test chooses."""
    answer: dict = {"results": [], "degraded": []}
    calls: list[dict] = []

    def _provider(payload):
        calls.append(payload)
        return dict(answer)

    register_function("search.similar", _provider)

    class Handle:
        payloads = calls

        @staticmethod
        def answers_with(*pairs):
            """``("Samsung", 0.93), ("Siemens", 0.61)`` — label and score."""
            answer["results"] = [
                {"key": f"k{index}", "text": label, "payload": {}, "similarity": score}
                for index, (label, score) in enumerate(pairs)
            ]

        @staticmethod
        def answers_unscored(*labels):
            answer["results"] = [
                {"key": f"k{index}", "text": label, "payload": {}}
                for index, label in enumerate(labels)
            ]

    try:
        yield Handle
    finally:
        function_registry._providers.pop("search.similar", None)


def match(**payload):
    payload.setdefault("vocabulary", "phones")
    return call("vocabularies.match", payload)


# --- the deterministic rungs ------------------------------------------------


def test_an_exact_label_is_a_certainty(phones):
    assert match(level="Vendor", text="Samsung") == {
        "matched": True,
        "code": "samsung",
        "label": "Samsung",
        "score": 1.0,
        "method": "exact",
    }


def test_case_does_not_make_it_less_exact(phones):
    answer = match(level="Vendor", text="  sAMSUNG ")
    assert (answer["code"], answer["score"], answer["method"]) == (
        "samsung",
        1.0,
        "exact",
    )


def test_a_code_is_an_exact_hit_too(phones):
    """A composer that already holds a code asks the same question."""
    answer = match(level="Model", text="iphone-10")
    assert (answer["code"], answer["method"]) == ("iphone-10", "exact")


def test_a_transliteration_lands_on_the_code_it_minted(phones):
    """«Самсунг» is not a guess: `slug.slugify_term` is the function that
    derived `samsung` from "Samsung" in the first place, and it maps both
    spellings onto the same string. No vector, no threshold, no bill."""
    answer = match(level="Vendor", text="Самсунг")
    assert (answer["code"], answer["score"], answer["method"]) == (
        "samsung",
        1.0,
        "exact",
    )


def test_a_unique_prefix_is_confident_but_not_certain(phones):
    answer = match(level="Model", text="Galaxy")
    assert answer["matched"] is True
    assert answer["code"] == "galaxy-s10"
    assert answer["method"] == "prefix"
    assert 0.8 <= answer["score"] < 1.0


def test_an_ambiguous_prefix_is_not_a_match(phones):
    """"iPhone 1" is two phones. A composer picking one of them at random
    writes the wrong model number into somebody's listing."""
    assert match(level="Model", text="iPhone 1") == {
        "matched": False,
        "reason": "no_confident_match",
    }


# --- the vector rung and its floor ------------------------------------------


def test_a_high_scoring_neighbour_is_returned_with_its_real_score(
    phones, similar_provider
):
    similar_provider.answers_with(("Samsung", 0.93), ("Apple", 0.44))
    with override_settings(STAPEL_VOCABULARIES=VECTOR_ON):
        answer = match(level="Vendor", text="самсуг")
    assert answer == {
        "matched": True,
        "code": "samsung",
        "label": "Samsung",
        "score": 0.93,
        "method": "vector",
    }


def test_a_low_scoring_neighbour_is_refused(phones, similar_provider):
    """The «айфон» case: the net answers, every answer is wrong, and the
    only thing that separates it from the «Самсунг» case is the score."""
    similar_provider.answers_with(("Apple", 0.58), ("Samsung", 0.51))
    with override_settings(STAPEL_VOCABULARIES=VECTOR_ON):
        answer = match(level="Vendor", text="айфон")
    assert answer == {"matched": False, "reason": "no_confident_match"}


def test_an_unscored_neighbour_is_refused_rather_than_assigned_a_score(
    phones, similar_provider
):
    """A provider that returns no score has not told us it is confident,
    and inventing a number here would be the defect this Function exists to
    close, one layer down."""
    similar_provider.answers_unscored("Samsung")
    with override_settings(STAPEL_VOCABULARIES=VECTOR_ON):
        assert match(level="Vendor", text="самсуг") == {
            "matched": False,
            "reason": "no_confident_match",
        }


def test_the_caller_may_raise_or_lower_the_floor(phones, similar_provider):
    similar_provider.answers_with(("Samsung", 0.71))
    with override_settings(STAPEL_VOCABULARIES=VECTOR_ON):
        assert match(level="Vendor", text="самсуг")["matched"] is False
        loose = match(level="Vendor", text="самсуг", min_score=0.7)
        assert (loose["matched"], loose["score"]) == (True, 0.71)
        strict = match(level="Vendor", text="Samsung", min_score=1.1)
        assert strict == {"matched": False, "reason": "no_confident_match"}


def test_a_neighbour_outside_this_level_is_not_a_match(phones, similar_provider):
    """The vector store spans every corpus; the question named one level."""
    similar_provider.answers_with(("iPhone 10", 0.99))
    with override_settings(STAPEL_VOCABULARIES=VECTOR_ON):
        assert match(level="Vendor", text="айфон") == {
            "matched": False,
            "reason": "no_confident_match",
        }


def test_the_default_floor_is_the_one_documented(phones):
    from stapel_vocabularies.conf import vocabularies_settings

    assert float(vocabularies_settings.MATCH_MIN_SCORE) == 0.8


# --- degrading honestly -----------------------------------------------------


def test_an_unreachable_vector_function_is_a_no_match_not_an_exception(phones):
    def _broken(payload):
        raise RuntimeError("agent down")

    register_function("search.similar", _broken)
    try:
        with override_settings(STAPEL_VOCABULARIES=VECTOR_ON):
            assert match(level="Vendor", text="самсуг") == {
                "matched": False,
                "reason": "no_confident_match",
            }
    finally:
        function_registry._providers.pop("search.similar", None)


def test_an_unconfigured_vector_seam_is_a_no_match(phones, similar_provider):
    assert match(level="Vendor", text="самсуг") == {
        "matched": False,
        "reason": "no_confident_match",
    }
    assert similar_provider.payloads == []


def test_an_unknown_vocabulary_says_so(phones):
    assert match(vocabulary="nope", level="Vendor", text="Samsung") == {
        "matched": False,
        "reason": "unknown_vocabulary",
    }


def test_an_unknown_level_says_so(phones):
    assert match(level="Nope", text="Samsung") == {
        "matched": False,
        "reason": "unknown_level",
    }


def test_an_empty_text_is_a_no_match(phones):
    assert match(level="Vendor", text="   ") == {
        "matched": False,
        "reason": "no_confident_match",
    }


# --- parentage, the way resolve does it -------------------------------------


def test_a_parent_scopes_the_answer(phones):
    answer = match(
        level="Model",
        text="Galaxy S10",
        parent={"level": "Vendor", "code": "samsung"},
    )
    assert answer["code"] == "galaxy-s10"


def test_a_term_under_a_different_parent_is_not_a_match(phones):
    assert match(
        level="Model",
        text="Galaxy S10",
        parent={"level": "Vendor", "code": "apple"},
    ) == {"matched": False, "reason": "no_confident_match"}


def test_an_unknown_parent_matches_nothing(phones):
    assert match(
        level="Model",
        text="Galaxy S10",
        parent={"level": "Vendor", "code": "nokia"},
    ) == {"matched": False, "reason": "no_confident_match"}


def test_the_parent_scopes_the_vector_rung_too(phones, similar_provider):
    similar_provider.answers_with(("Galaxy S10", 0.97))
    with override_settings(STAPEL_VOCABULARIES=VECTOR_ON):
        assert match(
            level="Model",
            text="галакси",
            parent={"level": "Vendor", "code": "apple"},
        ) == {"matched": False, "reason": "no_confident_match"}
        answer = match(
            level="Model",
            text="галакси",
            parent={"level": "Vendor", "code": "samsung"},
        )
    assert (answer["code"], answer["method"]) == ("galaxy-s10", "vector")


# --- the contract -----------------------------------------------------------


def test_a_payload_the_schema_refuses_does_not_reach_the_function(phones):
    with pytest.raises(Exception):
        call(
            "vocabularies.match",
            {"vocabulary": "phones", "level": "Vendor", "text": "x", "extra": 1},
        )


@pytest.mark.parametrize("name", ["vocabularies.match", "vocabularies.set_popularity"])
def test_every_new_function_carries_its_schema(name):
    import json
    from pathlib import Path

    schemas = Path(__file__).resolve().parent.parent / "schemas" / "functions"
    schema = json.loads((schemas / f"{name}.json").read_text(encoding="utf-8"))
    assert schema["title"] == name
    assert schema["additionalProperties"] is False
