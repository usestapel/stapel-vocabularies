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


def test_an_explicit_sort_rank_outranks_row_order():
    """The optional 5th column (stapel-tools 0.62.1's fixture contract).

    Row order is canonical (level, code) for reviewability — VOC004 — and
    this loader turns row order into ``Term.sort``, so without the column
    every picker was doomed to code-alphabetical order: a live stand's RAM
    dropdown opened on «0.1 МБ» and put «10 ГБ» before «2 ГБ». A row that
    states its rank keeps it; a row that does not keeps the historical
    row-order behavior, so 4-column fixtures load byte-for-byte the same.
    """
    ranked = fixture(terms=[
        ["Vendor", "apple", "Apple", None],
        ["Vendor", "samsung", "Samsung", None],
        ["Model", "iphone-10", "iPhone 10", "10", 2],
        ["Model", "galaxy-s10", "Galaxy S10", None, 1],
    ])
    load_fixture(ranked)
    models = list(
        Term.objects.filter(level="Model").order_by("sort", "label")
        .values_list("label", "sort")
    )
    assert models == [("Galaxy S10", 1), ("iPhone 10", 2)]
    vendors = dict(
        Term.objects.filter(level="Vendor").values_list("label", "sort")
    )
    # Unranked rows keep the row-order sort they always had.
    assert vendors == {"Apple": 0, "Samsung": 1}


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


# ---------------------------------------------------------------------------
# Source identity: a re-import keys on external_id, not on the code
# ---------------------------------------------------------------------------
#
# The code is a transliterated slug of the LABEL, so a source catalogue
# relabelling a term moves its code while the term stays the same term. Keyed
# on the code that reads as "a new term, and a stale one": additively it
# duplicates the value, and under --replace it deletes the row — taking its id
# and its edges — and inserts a fresh one.


def test_a_relabelled_term_keeps_its_row_when_it_carries_an_external_id():
    load_fixture(fixture())
    term_id = Term.objects.get(code="iphone-10").id
    edge_ids = set(TermEdge.objects.values_list("id", flat=True))

    moved = fixture()
    moved["terms"][2] = ["Model", "iphone-x", "iPhone X", "10"]
    moved["edges"][0] = ["Vendor", "apple", "Model", "iphone-x"]
    result = load_fixture(moved)

    assert result.terms_created == 0
    assert result.terms_updated == 1
    assert Term.objects.filter(level="Model", external_id="10").count() == 1
    term = Term.objects.get(id=term_id)          # the SAME row
    assert (term.code, term.label) == ("iphone-x", "iPhone X")
    assert not Term.objects.filter(code="iphone-10").exists()
    # Its edge was neither dropped nor rebuilt (additive load).
    assert set(TermEdge.objects.values_list("id", flat=True)) == edge_ids


def test_a_relabelled_term_under_replace_keeps_its_id():
    load_fixture(fixture())
    term_id = Term.objects.get(code="iphone-10").id
    moved = fixture()
    moved["terms"][2] = ["Model", "iphone-x", "iPhone X", "10"]
    moved["edges"][0] = ["Vendor", "apple", "Model", "iphone-x"]

    result = load_fixture(moved, replace=True)

    assert result.terms_deleted == 0             # nothing went stale
    assert Term.objects.get(id=term_id).code == "iphone-x"
    assert Term.objects.count() == 4


def test_a_term_without_an_external_id_still_matches_on_its_code():
    load_fixture(fixture())
    term_id = Term.objects.get(code="apple").id
    edited = fixture()
    edited["terms"][0] = ["Vendor", "apple", "Apple Inc.", None]
    result = load_fixture(edited)
    assert result.terms_updated == 1
    assert Term.objects.get(id=term_id).label == "Apple Inc."
    assert Term.objects.count() == 4


def test_a_relabel_without_an_external_id_is_still_a_new_term():
    """No source id, no identity — the code is all the term has."""
    load_fixture(fixture())
    moved = fixture()
    moved["terms"][0] = ["Vendor", "apple-inc", "Apple Inc.", None]
    moved["edges"][0] = ["Vendor", "apple-inc", "Model", "iphone-10"]
    load_fixture(moved, replace=True)
    assert not Term.objects.filter(code="apple").exists()
    assert Term.objects.filter(code="apple-inc").exists()


def test_two_terms_swapping_codes_do_not_break_the_unique_constraint():
    """A→B while B→A: parked on temporary codes, then written."""
    swap = fixture()
    swap["terms"] = [
        ["Vendor", "alpha", "Alpha", "1"],
        ["Vendor", "beta", "Beta", "2"],
    ]
    swap["edges"] = []
    load_fixture(swap)
    ids = dict(Term.objects.values_list("external_id", "id"))

    swapped = fixture()
    swapped["terms"] = [
        ["Vendor", "beta", "Beta", "1"],
        ["Vendor", "alpha", "Alpha", "2"],
    ]
    swapped["edges"] = []
    load_fixture(swapped, replace=True)

    assert Term.objects.get(id=ids["1"]).code == "beta"
    assert Term.objects.get(id=ids["2"]).code == "alpha"
    assert Term.objects.count() == 2


def test_a_rename_onto_a_dropped_terms_code_lands_under_replace():
    load_fixture(fixture())
    moved = fixture()
    # "galaxy-s10" is dropped from the file; iphone-10 (external_id 10) moves
    # onto its code. The stale delete must run before the rename.
    moved["terms"] = [
        ["Vendor", "apple", "Apple", None],
        ["Vendor", "samsung", "Samsung", None],
        ["Model", "galaxy-s10", "Galaxy S10 (was iPhone)", "10"],
    ]
    moved["edges"] = [["Vendor", "apple", "Model", "galaxy-s10"]]
    result = load_fixture(moved, replace=True)
    assert result.terms_deleted == 1
    assert Term.objects.get(level="Model").external_id == "10"
    assert Term.objects.get(level="Model").code == "galaxy-s10"


def test_a_rename_blocked_by_an_undeclared_term_is_named_not_an_integrity_error():
    load_fixture(fixture())
    moved = fixture()
    moved["terms"] = [["Model", "galaxy-s10", "Galaxy S10 (was iPhone)", "10"]]
    moved["edges"] = []
    with pytest.raises(FixtureError) as exc:
        load_fixture(moved)                      # additive: nothing is dropped
    assert "galaxy-s10" in str(exc.value)
    assert "--replace" in str(exc.value)
    assert Term.objects.get(code="iphone-10").label == "iPhone 10"


def test_two_live_terms_carrying_one_external_id_are_refused():
    load_fixture(fixture())
    Term.objects.filter(code="galaxy-s10").update(external_id="10")
    with pytest.raises(FixtureError) as exc:
        load_fixture(fixture())
    assert "external_id" in str(exc.value)
    assert "merge or clear" in str(exc.value)


def test_one_file_may_not_give_one_term_two_rows():
    load_fixture(fixture())
    doubled = fixture()
    doubled["terms"].append(["Model", "iphone-ten", "iPhone Ten", "10"])
    doubled["edges"] = []
    with pytest.raises(FixtureError) as exc:
        load_fixture(doubled)
    assert "one live term" in str(exc.value)


def test_a_code_match_belonging_to_another_source_node_is_not_hijacked():
    """Fixture id X on a code held by a term already carrying id Y."""
    load_fixture(fixture())
    other = fixture()
    other["terms"] = [["Model", "iphone-10", "Something else", "999"]]
    other["edges"] = []
    with pytest.raises(FixtureError):
        load_fixture(other)                      # additive: the code is held
    assert Term.objects.get(code="iphone-10").external_id == "10"


def test_the_rename_is_idempotent_on_a_second_load():
    load_fixture(fixture())
    moved = fixture()
    moved["terms"][2] = ["Model", "iphone-x", "iPhone X", "10"]
    moved["edges"][0] = ["Vendor", "apple", "Model", "iphone-x"]
    load_fixture(moved)
    result = load_fixture(moved)
    assert (result.terms_created, result.terms_updated) == (0, 0)
