"""Catalogue -> vocabulary fixture (spec §3.6). Django-free.

Two shapes cover the vendor catalogues this module was built for:

* ``nested_xml_to_fixture`` — elements nested by level, each carrying its
  label in an attribute::

      <Vendor name="Apple"><Model name="iPhone 10">
        <MemorySize name="64 ГБ"/></Model></Vendor>

* ``csv_to_fixture`` — one row per leaf path, one column per level.

Both are streaming readers: the XML side drives ``iterparse`` and drops every
finished element out of the tree, so a 40 MB catalogue is walked in constant
document memory (what is held is the *result* — the distinct terms and edges —
which is what the fixture is).

Nothing here imports Django: the importer (spec §4) is a plain console script
and calls these functions directly.
"""
from __future__ import annotations

import csv
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .slug import dedupe_codes

#: One collected edge, before codes are assigned.
LabelEdge = Tuple[str, str, str, str]  # (parent_level, parent_label, child_level, child_label)


class ConvertError(ValueError):
    """The source file does not have the shape the converter was told to read."""


def build_fixture(
    slug: str,
    name: str,
    levels: Sequence[dict],
    terms: Dict[str, Dict[str, Optional[str]]],
    edges: Iterable[LabelEdge],
    source: str = "",
) -> dict:
    """Assemble the fixture from collected labels.

    ``terms`` is ``{level: {label: external_id|None}}``; ``edges`` are label
    tuples. Codes are assigned per level by ``dedupe_codes`` (label sort
    order), so the output is byte-stable for a given input.
    """
    level_names = [level["name"] for level in levels]
    level_index = {name_: i for i, name_ in enumerate(level_names)}
    codes: Dict[str, Dict[str, str]] = {
        level_name: dedupe_codes(terms.get(level_name, {}).keys())
        for level_name in level_names
    }

    term_rows: List[list] = []
    for level_name in level_names:
        for label, external_id in terms.get(level_name, {}).items():
            term_rows.append(
                [level_name, codes[level_name][label], label, external_id or None]
            )
    term_rows.sort(key=lambda row: (level_index[row[0]], row[1]))

    edge_rows: List[list] = []
    for parent_level, parent_label, child_level, child_label in edges:
        try:
            edge_rows.append(
                [
                    parent_level,
                    codes[parent_level][parent_label],
                    child_level,
                    codes[child_level][child_label],
                ]
            )
        except KeyError as exc:  # a level the caller did not ask for
            raise ConvertError(f"edge refers to an unknown term: {exc}") from None
    edge_rows.sort(key=lambda row: tuple(row))

    return {
        "slug": slug,
        "name": name,
        "source": source,
        "levels": [dict(level) for level in levels],
        "terms": term_rows,
        "edges": edge_rows,
    }


def nested_xml_to_fixture(
    path,
    slug: str,
    levels: Optional[Sequence[str]] = None,
    name_attr: str = "name",
    id_attr: Optional[str] = None,
    name: Optional[str] = None,
    source: str = "",
) -> dict:
    """Read a catalogue of nested label-bearing elements into a fixture.

    ``levels`` names the element tags to treat as levels, root first; omit it
    to auto-detect them in document order (the order the file first nests
    them). Every level's parent is the level element enclosing it, and it must
    be the same throughout the file — a catalogue that nests ``Color`` under
    two different levels is not a DAG of levels and is refused.
    """
    wanted = list(levels) if levels else None
    order: List[str] = list(wanted) if wanted else []
    parents: Dict[str, Optional[str]] = {}
    terms: Dict[str, Dict[str, Optional[str]]] = {}
    edges: set = set()

    element_stack: List[ET.Element] = []
    # (level, label, depth) of the enclosing level elements. The depth is what
    # pops the stack — matching on the tag alone would mis-nest a catalogue
    # that repeats a tag, and the label is gone once the element is cleared.
    level_stack: List[Tuple[str, str, int]] = []

    for event, elem in ET.iterparse(str(path), events=("start", "end")):
        if event == "start":
            element_stack.append(elem)
            tag = elem.tag
            if wanted is not None and tag not in wanted:
                continue
            label = elem.get(name_attr)
            if label is None:
                continue
            label = label.strip()
            if not label:
                continue
            if wanted is None and tag not in order:
                order.append(tag)
            parent = level_stack[-1] if level_stack else None
            parent_level = parent[0] if parent else None
            if tag in parents and parents[tag] != parent_level:
                raise ConvertError(
                    f"level {tag!r} is nested under both {parents[tag]!r} and "
                    f"{parent_level!r}; a level has exactly one parent level"
                )
            parents[tag] = parent_level
            bucket = terms.setdefault(tag, {})
            external_id = elem.get(id_attr) if id_attr else None
            if label not in bucket or (external_id and not bucket[label]):
                bucket[label] = external_id
            if parent is not None:
                edges.add((parent[0], parent[1], tag, label))
            level_stack.append((tag, label, len(element_stack)))
        else:
            depth = len(element_stack)
            element_stack.pop()
            if level_stack and level_stack[-1][2] == depth:
                level_stack.pop()
            # Drop the finished element so the document never accumulates.
            elem.clear()
            if element_stack:
                element_stack[-1].remove(elem)

    if not order:
        raise ConvertError(
            f"no level elements with a {name_attr!r} attribute found in {path}"
        )
    level_defs = [
        {"name": tag} if not parents.get(tag) else {"name": tag, "parent": parents[tag]}
        for tag in order
    ]
    return build_fixture(
        slug, name or slug, level_defs, terms, sorted(edges), source=source
    )


def csv_to_fixture(
    path,
    slug: str,
    level_columns: Sequence[str],
    name: Optional[str] = None,
    source: str = "",
    delimiter: str = ",",
    encoding: str = "utf-8",
) -> dict:
    """Read a one-row-per-path CSV into a fixture.

    ``level_columns`` names the columns, root level first; the column name is
    the level name. An empty cell truncates that row's path — the levels to
    its right are simply not stated for that row, which is how a catalogue
    expresses "this vendor has models but this model has no listed colours".
    """
    columns = list(level_columns)
    if not columns:
        raise ConvertError("level_columns must name at least one column")

    terms: Dict[str, Dict[str, Optional[str]]] = {name_: {} for name_ in columns}
    edges: set = set()

    with open(path, newline="", encoding=encoding) as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        missing = [c for c in columns if c not in (reader.fieldnames or [])]
        if missing:
            raise ConvertError(f"columns not present in {path}: {missing}")
        for row in reader:
            previous: Optional[Tuple[str, str]] = None
            for column in columns:
                label = (row.get(column) or "").strip()
                if not label:
                    break
                terms[column].setdefault(label, None)
                if previous is not None:
                    edges.add((previous[0], previous[1], column, label))
                previous = (column, label)

    level_defs = [{"name": columns[0]}] + [
        {"name": column, "parent": columns[index]}
        for index, column in enumerate(columns[1:])
    ]
    return build_fixture(
        slug, name or slug, level_defs, terms, sorted(edges), source=source
    )


def dump_fixture(fixture: dict) -> str:
    """Serialize a fixture: one term and one edge per line.

    ``json.dumps(indent=2)`` would spread a 160 000-edge catalogue over a
    million lines and make a review diff unreadable; a single line makes it
    undiffable. One row per line is what a reviewed fixture wants.
    """
    def rows(items: Sequence[Sequence]) -> str:
        if not items:
            return "[]"
        body = ",\n".join(
            "    " + json.dumps(item, ensure_ascii=False) for item in items
        )
        return "[\n" + body + "\n  ]"

    parts = [
        "{",
        f'  "slug": {json.dumps(fixture["slug"], ensure_ascii=False)},',
        f'  "name": {json.dumps(fixture["name"], ensure_ascii=False)},',
        f'  "source": {json.dumps(fixture.get("source", ""), ensure_ascii=False)},',
        f'  "levels": {json.dumps(fixture["levels"], ensure_ascii=False)},',
        f'  "terms": {rows(fixture["terms"])},',
        f'  "edges": {rows(fixture["edges"])}',
        "}",
    ]
    return "\n".join(parts) + "\n"


def write_fixture(fixture: dict, path) -> Path:
    """Write ``dump_fixture`` to *path* (parents created)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(dump_fixture(fixture), encoding="utf-8")
    return target


__all__ = [
    "ConvertError",
    "build_fixture",
    "csv_to_fixture",
    "dump_fixture",
    "nested_xml_to_fixture",
    "write_fixture",
]
