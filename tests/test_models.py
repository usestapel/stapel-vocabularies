"""``Vocabulary.levels`` is a schema, not a blob (spec §3.3).

The field is JSON, so nothing but this validator stands between a typo and a
vocabulary whose level graph nothing can walk. Three properties are pinned:
names are unique, a parent names a level declared earlier, and a key nobody
recognises is refused rather than stored and silently ignored.

The "declared earlier" rule is also the whole acyclicity argument, so the
cycle cases below are not extra defence — they are that rule seen from the
other side.
"""
import pytest
from django.core.exceptions import ValidationError
from django.db.utils import IntegrityError

from stapel_vocabularies.models import Term, TermEdge, Vocabulary, validate_levels

VALID = [
    {"name": "Vendor"},
    {"name": "Model", "parent": "Vendor"},
    {"name": "Color", "parent": "Model"},
]


def test_a_chain_of_levels_is_valid():
    validate_levels(VALID)


def test_a_level_may_hang_off_any_earlier_level_not_just_the_previous_one():
    """The levels are a DAG, not a list: two levels may share a parent."""
    validate_levels(
        [
            {"name": "Model", "parent": None},
            {"name": "Memory", "parent": "Model"},
            {"name": "Color", "parent": "Model"},
        ]
    )


@pytest.mark.parametrize(
    "levels, because",
    [
        ([], "a vocabulary with no levels has nothing to hold terms"),
        ("Vendor", "levels is a list of objects, not a string"),
        (["Vendor"], "each level is an object"),
        ([{"name": ""}], "an empty level name addresses nothing"),
        ([{"name": "V" * 65}], "the level column is 64 characters"),
        ([{"name": "V"}, {"name": "V"}], "duplicate names make a parent ambiguous"),
        ([{"name": "V", "parrent": "X"}], "an unknown key is a typo, not a feature"),
        ([{"name": "V", "parent": "Nope"}], "a parent must exist"),
        (
            [{"name": "Model", "parent": "Vendor"}, {"name": "Vendor"}],
            "a forward reference is how a cycle would get in",
        ),
        (
            [{"name": "A", "parent": "A"}],
            "a self-parent is a one-element cycle",
        ),
        (
            [{"name": "A", "parent": "B"}, {"name": "B", "parent": "A"}],
            "a two-element cycle cannot be declared backwards-only",
        ),
    ],
)
def test_invalid_level_lists_are_refused(levels, because):
    with pytest.raises(ValidationError):
        validate_levels(levels)


@pytest.mark.django_db
def test_clean_runs_the_same_validator():
    vocabulary = Vocabulary(slug="v", name="V", levels=[{"name": "A", "parent": "B"}])
    with pytest.raises(ValidationError):
        vocabulary.full_clean()


@pytest.mark.django_db
def test_a_code_is_unique_within_a_level_and_free_across_levels():
    vocabulary = Vocabulary.objects.create(slug="v", name="V", levels=VALID)
    Term.objects.create(vocabulary=vocabulary, level="Vendor", code="apple", label="Apple")
    # The same code at another level is a different term — that is what makes
    # "Color=chernyy" addressable independently of any Model.
    Term.objects.create(vocabulary=vocabulary, level="Model", code="apple", label="Apple")
    with pytest.raises(IntegrityError):
        Term.objects.create(
            vocabulary=vocabulary, level="Vendor", code="apple", label="Apple again"
        )


@pytest.mark.django_db
def test_an_edge_cannot_be_declared_twice():
    vocabulary = Vocabulary.objects.create(slug="v", name="V", levels=VALID)
    parent = Term.objects.create(vocabulary=vocabulary, level="Vendor", code="a", label="A")
    child = Term.objects.create(vocabulary=vocabulary, level="Model", code="b", label="B")
    TermEdge.objects.create(parent=parent, child=child)
    with pytest.raises(IntegrityError):
        TermEdge.objects.create(parent=parent, child=child)


@pytest.mark.django_db
def test_label_for_falls_back_to_the_untranslated_label():
    vocabulary = Vocabulary.objects.create(slug="v", name="V", levels=VALID)
    term = Term.objects.create(
        vocabulary=vocabulary,
        level="Color",
        code="chernyy",
        label="чёрный",
        labels={"en": "black"},
    )
    assert term.label_for("en") == "black"
    assert term.label_for("de") == "чёрный"
    assert term.label_for(None) == "чёрный"


@pytest.mark.django_db
def test_parent_level_answers_none_for_a_root():
    vocabulary = Vocabulary.objects.create(slug="v", name="V", levels=VALID)
    assert vocabulary.parent_level("Vendor") is None
    assert vocabulary.parent_level("Model") == "Vendor"
    assert vocabulary.level_names() == ["Vendor", "Model", "Color"]
    assert vocabulary.has_level("Color")
    assert not vocabulary.has_level("Nope")
