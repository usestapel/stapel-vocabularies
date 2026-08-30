"""Catalogue -> fixture, and the fixture against its own schema (spec §3.6).

Two things are pinned here. First, that the converters read the two shapes the
vendor catalogues come in and produce a byte-stable file — a fixture is
reviewed as code, so re-converting an unchanged catalogue must produce an
unchanged diff. Second, that what they produce validates against
``docs/vocabulary-fixture.schema.json``: the schema is what the importer (spec
§4) writes to, and a schema nothing is checked against is a comment.
"""
import json
from pathlib import Path

import pytest

from stapel_vocabularies.convert import (
    ConvertError,
    csv_to_fixture,
    dump_fixture,
    nested_xml_to_fixture,
    write_fixture,
)

jsonschema = pytest.importorskip("jsonschema")

SCHEMA = json.loads(
    (Path(__file__).resolve().parent.parent / "docs" / "vocabulary-fixture.schema.json")
    .read_text(encoding="utf-8")
)

PHONES_XML = """<?xml version="1.0" ?>
<Phones>
  <Vendor name="Apple">
    <Model name="iPhone 10">
      <MemorySize name="64 ГБ">
        <Color name="чёрный"/>
      </MemorySize>
      <MemorySize name="256 ГБ">
        <Color name="белый"/>
      </MemorySize>
    </Model>
    <Model name="iPhone 11">
      <MemorySize name="64 ГБ">
        <Color name="чёрный"/>
      </MemorySize>
    </Model>
  </Vendor>
  <Vendor name="Samsung">
    <Model name="Galaxy S10">
      <MemorySize name="128 ГБ">
        <Color name="чёрный"/>
      </MemorySize>
    </Model>
  </Vendor>
</Phones>
"""


@pytest.fixture
def phones_xml(tmp_path):
    path = tmp_path / "phone_catalog.xml"
    path.write_text(PHONES_XML, encoding="utf-8")
    return path


def validate(fixture):
    jsonschema.validate(fixture, SCHEMA)
    return fixture


# --- nested XML -------------------------------------------------------------


def test_nested_xml_detects_the_levels_in_document_order(phones_xml):
    fixture = validate(nested_xml_to_fixture(phones_xml, "avito-phones"))
    assert fixture["levels"] == [
        {"name": "Vendor"},
        {"name": "Model", "parent": "Vendor"},
        {"name": "MemorySize", "parent": "Model"},
        {"name": "Color", "parent": "MemorySize"},
    ]


def test_a_term_is_shared_by_every_path_that_reaches_it(phones_xml):
    """The point of the DAG (D6): one 'чёрный', not one per path."""
    fixture = nested_xml_to_fixture(phones_xml, "avito-phones")
    colors = [row for row in fixture["terms"] if row[0] == "Color"]
    assert [row[1] for row in colors] == ["belyy", "chernyy"]
    # ...reached from two distinct memory sizes, though the document spells
    # (64 ГБ -> чёрный) twice. Edges are a SET: a catalogue that repeats a
    # pair contributes one edge, which is why 56 921 phone paths collapse to
    # 160 000 edges rather than multiplying.
    into_black = [
        row for row in fixture["edges"] if row[2] == "Color" and row[3] == "chernyy"
    ]
    assert into_black == [
        ["MemorySize", "128-gb", "Color", "chernyy"],
        ["MemorySize", "64-gb", "Color", "chernyy"],
    ]


def test_terms_are_sorted_by_level_then_code_and_edges_by_tuple(phones_xml):
    fixture = nested_xml_to_fixture(phones_xml, "avito-phones")
    order = [level["name"] for level in fixture["levels"]]
    keys = [(order.index(row[0]), row[1]) for row in fixture["terms"]]
    assert keys == sorted(keys)
    assert fixture["edges"] == sorted(fixture["edges"])


def test_conversion_is_byte_stable(phones_xml):
    once = dump_fixture(nested_xml_to_fixture(phones_xml, "avito-phones"))
    twice = dump_fixture(nested_xml_to_fixture(phones_xml, "avito-phones"))
    assert once == twice


def test_selected_levels_collapse_the_ones_left_out(phones_xml):
    """Asking for two of four levels gives edges between the two asked for.

    This is what the importer's inline threshold needs: a level that became an
    ordinary inline `select` must not leave a hole in the graph.
    """
    fixture = validate(
        nested_xml_to_fixture(phones_xml, "phones", levels=["Vendor", "Model"])
    )
    assert [level["name"] for level in fixture["levels"]] == ["Vendor", "Model"]
    assert ["Vendor", "apple", "Model", "iphone-10"] in fixture["edges"]
    assert all(row[0] in {"Vendor", "Model"} for row in fixture["terms"])


def test_external_ids_are_read_from_an_attribute_and_do_not_become_codes(tmp_path):
    path = tmp_path / "c.xml"
    path.write_text(
        '<Root><Vendor name="Apple" id="17"><Model name="iPhone 10" id="42"/></Vendor></Root>',
        encoding="utf-8",
    )
    fixture = validate(nested_xml_to_fixture(path, "c", id_attr="id"))
    assert ["Vendor", "apple", "Apple", "17"] in fixture["terms"]
    assert ["Model", "iphone-10", "iPhone 10", "42"] in fixture["terms"]


def test_a_level_nested_under_two_different_parents_is_refused(tmp_path):
    path = tmp_path / "c.xml"
    path.write_text(
        '<Root><A name="a"><C name="c1"/></A><B name="b"><C name="c2"/></B></Root>',
        encoding="utf-8",
    )
    with pytest.raises(ConvertError):
        nested_xml_to_fixture(path, "c")


def test_a_file_with_no_labelled_elements_is_refused(tmp_path):
    path = tmp_path / "c.xml"
    path.write_text("<Root><A/></Root>", encoding="utf-8")
    with pytest.raises(ConvertError):
        nested_xml_to_fixture(path, "c")


def test_the_parser_does_not_hold_the_document(tmp_path):
    """iterparse + drop: a big catalogue must not become a big tree.

    Measured on the tree the parser leaves behind rather than on RSS: the
    failure mode is elements accumulating under the root, and a document
    walked correctly leaves nothing to accumulate.
    """
    import tracemalloc

    path = tmp_path / "big.xml"
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("<Root>")
        for vendor in range(200):
            handle.write(f'<Vendor name="Vendor {vendor}">')
            for model in range(50):
                handle.write(f'<Model name="Model {vendor}-{model}"/>')
            handle.write("</Vendor>")
        handle.write("</Root>")
    assert path.stat().st_size > 250_000

    tracemalloc.start()
    fixture = nested_xml_to_fixture(path, "big")
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert len(fixture["terms"]) == 200 + 200 * 50
    # The result itself is ~10k terms and ~10k edges; the document is not held
    # on top of it. 20 MB is loose enough not to be flaky and tight enough to
    # fail if the whole tree were retained.
    assert peak < 20_000_000, peak


# --- CSV --------------------------------------------------------------------


def test_csv_columns_become_levels(tmp_path):
    path = tmp_path / "c.csv"
    path.write_text(
        "Vendor,Model,Color\nApple,iPhone 10,чёрный\nApple,iPhone 11,\nSamsung,Galaxy S10,чёрный\n",
        encoding="utf-8",
    )
    fixture = validate(csv_to_fixture(path, "phones", ["Vendor", "Model", "Color"]))
    assert fixture["levels"] == [
        {"name": "Vendor"},
        {"name": "Model", "parent": "Vendor"},
        {"name": "Color", "parent": "Model"},
    ]
    assert [row[1] for row in fixture["terms"] if row[0] == "Vendor"] == [
        "apple",
        "samsung",
    ]
    # The empty Color cell truncates that row's path — iPhone 11 gets no colour
    # edge, and no empty-labelled term is invented for it.
    assert ["Model", "iphone-11", "Color", ""] not in fixture["edges"]
    assert all(row[2] for row in fixture["terms"])


def test_csv_refuses_a_column_the_file_does_not_have(tmp_path):
    path = tmp_path / "c.csv"
    path.write_text("Vendor\nApple\n", encoding="utf-8")
    with pytest.raises(ConvertError):
        csv_to_fixture(path, "phones", ["Vendor", "Model"])


def test_csv_needs_at_least_one_column(tmp_path):
    path = tmp_path / "c.csv"
    path.write_text("Vendor\nApple\n", encoding="utf-8")
    with pytest.raises(ConvertError):
        csv_to_fixture(path, "phones", [])


# --- the written file -------------------------------------------------------


def test_the_written_file_is_json_one_row_per_line(tmp_path, phones_xml):
    fixture = nested_xml_to_fixture(phones_xml, "avito-phones")
    path = write_fixture(fixture, tmp_path / "out" / "phones.json")
    text = path.read_text(encoding="utf-8")
    assert json.loads(text) == fixture
    validate(json.loads(text))
    # One row per line is what makes a 15 000-term diff reviewable: every
    # term and every edge is its own line, and nothing else is.
    rows = [line for line in text.splitlines() if line.startswith("    [")]
    assert len(rows) == len(fixture["terms"]) + len(fixture["edges"])


def test_an_empty_edge_list_still_round_trips():
    fixture = {
        "slug": "s",
        "name": "S",
        "source": "",
        "levels": [{"name": "A"}],
        "terms": [["A", "x", "X", None]],
        "edges": [],
    }
    assert json.loads(dump_fixture(validate(fixture))) == fixture
