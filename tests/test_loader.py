"""Loading a fixture: one file, one transaction, one revision, one event.

A 15 000-term catalogue loaded term by term would issue 15 000 revisions and
emit 15 000 invalidations, and every consumer's cache would spend the import
thrashing. The batching is the point, and the properties below are what make
it safe to batch: the load is idempotent, ``--replace`` really is
authoritative, and a fixture that references a term it does not declare takes
nothing with it.
"""
import json

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from stapel_vocabularies.loader import FixtureError, load_fixture
from stapel_vocabularies.models import Term, TermEdge, Vocabulary

pytestmark = pytest.mark.django_db

BASE = {
    "slug": "phones",
    "name": "Phones",
    "source": "https://example.test/phone_catalog.xml",
    "levels": [{"name": "Vendor"}, {"name": "Model", "parent": "Vendor"}],
    "terms": [
        ["Vendor", "apple", "Apple", None],
        ["Vendor", "samsung", "Samsung", None],
        ["Model", "iphone-10", "iPhone 10", "10"],
        ["Model", "galaxy-s10", "Galaxy S10", None],
    ],
    "edges": [
        ["Vendor", "apple", "Model", "iphone-10"],
        ["Vendor", "samsung", "Model", "galaxy-s10"],
    ],
}


def fixture(**overrides):
    return {**json.loads(json.dumps(BASE)), **overrides}


def test_a_first_load_creates_everything():
    result = load_fixture(fixture())
    assert result.created
    assert result.terms_created == 4
    assert result.edges_created == 2
    vocabulary = Vocabulary.objects.get(slug="phones")
    assert vocabulary.term_count == 4
    assert vocabulary.source == "https://example.test/phone_catalog.xml"
    assert Term.objects.count() == 4
    assert TermEdge.objects.count() == 2


def test_one_file_is_exactly_one_revision_and_one_event(captured_events):
    """The whole reason the loader is not a loop over save()."""
    load_fixture(fixture())
    first = Vocabulary.objects.get(slug="phones").revision
    assert len(captured_events) == 1
    assert captured_events[0].payload == {"slug": "phones", "revision": first}

    load_fixture(fixture(name="Phones v2"))
    second = Vocabulary.objects.get(slug="phones").revision
    assert second == first + 1
    assert len(captured_events) == 2
    assert captured_events[1].payload == {"slug": "phones", "revision": second}


def test_reloading_the_same_file_changes_no_rows():
    load_fixture(fixture())
    ids = sorted(Term.objects.values_list("id", flat=True))
    result = load_fixture(fixture())
    assert (result.terms_created, result.terms_updated, result.edges_created) == (0, 0, 0)
    assert sorted(Term.objects.values_list("id", flat=True)) == ids


def test_a_changed_label_updates_the_term_in_place():
    load_fixture(fixture())
    term_id = Term.objects.get(code="iphone-10").id
    renamed = fixture()
    renamed["terms"][2] = ["Model", "iphone-10", "iPhone X", "10"]
    result = load_fixture(renamed)
    assert result.terms_updated == 1
    term = Term.objects.get(id=term_id)
    assert term.label == "iPhone X"


def test_without_replace_a_load_is_additive():
    load_fixture(fixture())
    smaller = fixture()
    smaller["terms"] = [["Vendor", "nokia", "Nokia", None]]
    smaller["edges"] = []
    load_fixture(smaller)
    assert Term.objects.filter(code="apple").exists()
    assert Term.objects.count() == 5
    assert TermEdge.objects.count() == 2


def test_replace_makes_the_file_authoritative():
    load_fixture(fixture())
    smaller = fixture()
    smaller["terms"] = [["Vendor", "apple", "Apple", None]]
    smaller["edges"] = []
    result = load_fixture(smaller, replace=True)
    assert result.terms_deleted == 3
    assert result.edges_deleted == 2
    assert list(Term.objects.values_list("code", flat=True)) == ["apple"]
    assert TermEdge.objects.count() == 0
    assert Vocabulary.objects.get(slug="phones").term_count == 1


def test_replace_rebuilds_the_edge_set_rather_than_diffing_it():
    """A branch the vendor dropped must stop being offered."""
    load_fixture(fixture())
    rewired = fixture()
    rewired["edges"] = [["Vendor", "samsung", "Model", "iphone-10"]]
    load_fixture(rewired, replace=True)
    assert list(
        TermEdge.objects.values_list("parent__code", "child__code")
    ) == [("samsung", "iphone-10")]


def test_an_edge_to_an_undeclared_term_rolls_the_whole_file_back():
    broken = fixture()
    broken["edges"].append(["Vendor", "apple", "Model", "nokia-3310"])
    with pytest.raises(FixtureError):
        load_fixture(broken)
    assert not Vocabulary.objects.exists()
    assert not Term.objects.exists()


def test_a_second_vocabulary_does_not_see_the_first_ones_terms():
    load_fixture(fixture())
    other = fixture(slug="laptops", name="Laptops")
    other["terms"] = [["Vendor", "apple", "Apple", None]]
    other["edges"] = []
    load_fixture(other, replace=True)
    assert Term.objects.filter(code="apple").count() == 2
    assert Vocabulary.objects.get(slug="phones").term_count == 4


@pytest.mark.parametrize(
    "broken, because",
    [
        ({"levels": [{"name": "Model", "parent": "Vendor"}]}, "forward parent reference"),
        ({"terms": [["Nope", "x", "X", None]]}, "term at an undeclared level"),
        ({"terms": [["Vendor", "", "X", None]]}, "empty code"),
        ({"terms": [["Vendor", "x"]]}, "a term row is at least [level, code, label]"),
        ({"edges": [["Vendor", "apple", "Nope", "x"]]}, "edge at an undeclared level"),
        ({"edges": [["Vendor", "apple", "Model"]]}, "an edge row is a 4-tuple"),
        ({"slug": ""}, "a vocabulary needs a slug"),
        (
            {
                "terms": [
                    ["Vendor", "apple", "Apple", None],
                    ["Vendor", "apple", "Apple Inc", None],
                ]
            },
            "a duplicate code is named here, not by an IntegrityError mid-insert",
        ),
    ],
)
def test_a_malformed_fixture_is_refused(broken, because):
    with pytest.raises(FixtureError):
        load_fixture(fixture(**broken))


def test_the_management_command_loads_files(tmp_path, capsys):
    path = tmp_path / "phones.json"
    path.write_text(json.dumps(BASE), encoding="utf-8")
    call_command("load_vocabulary", str(path))
    assert Term.objects.count() == 4
    assert "phones: created at revision" in capsys.readouterr().out


def test_the_management_command_passes_replace_through(tmp_path):
    path = tmp_path / "phones.json"
    path.write_text(json.dumps(BASE), encoding="utf-8")
    call_command("load_vocabulary", str(path))
    smaller = fixture()
    smaller["terms"] = [["Vendor", "apple", "Apple", None]]
    smaller["edges"] = []
    path.write_text(json.dumps(smaller), encoding="utf-8")
    call_command("load_vocabulary", str(path), "--replace")
    assert list(Term.objects.values_list("code", flat=True)) == ["apple"]


def test_the_management_command_reports_a_bad_fixture_as_a_command_error(tmp_path):
    path = tmp_path / "phones.json"
    path.write_text(json.dumps({"slug": "x"}), encoding="utf-8")
    with pytest.raises(CommandError):
        call_command("load_vocabulary", str(path))


def test_the_management_command_reports_a_missing_file(tmp_path):
    with pytest.raises(CommandError):
        call_command("load_vocabulary", str(tmp_path / "nope.json"))
