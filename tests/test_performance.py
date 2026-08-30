"""The load has to finish (spec §3.3: < 60 s for the phone catalogue).

A phone catalogue is ~15 000 terms and ~160 000 edges. That is the size the
loader was designed around, and "it works on four terms" says nothing about
it: the failure this test exists for is a per-row save or a per-edge existence
query, both of which pass every other test in this suite and turn a 20-second
import into an hour.

Synthetic, not the real 7 MB file: the shape (terms, edge fan-out, batching)
is what costs time, and a repo does not carry a vendor catalogue. The real
measurement against ``phone_catalog.xml`` is recorded in the CHANGELOG.
"""
import time

import pytest

from stapel_vocabularies.loader import load_fixture
from stapel_vocabularies.models import Term, TermEdge

pytestmark = [pytest.mark.django_db, pytest.mark.slow]

#: Roughly the phone catalogue: 529 vendors, 14 962 models, a few hundred
#: memory sizes and colours, ~160 000 edges.
VENDORS = 529
MODELS = 15_000
MEMORY = 260
COLORS = 17
BUDGET_SECONDS = 60


def build_fixture():
    terms = [["Vendor", f"vendor-{i}", f"Vendor {i}", None] for i in range(VENDORS)]
    terms += [["Model", f"model-{i}", f"Model {i}", None] for i in range(MODELS)]
    terms += [["MemorySize", f"mem-{i}", f"{i} GB", None] for i in range(MEMORY)]
    terms += [["Color", f"color-{i}", f"Color {i}", None] for i in range(COLORS)]

    edges = [
        ["Vendor", f"vendor-{i % VENDORS}", "Model", f"model-{i}"]
        for i in range(MODELS)
    ]
    # Each model carries ~9 memory sizes and each memory size ~1 colour, which
    # is the fan-out that makes 15 000 terms into 160 000 edges.
    edges += [
        ["Model", f"model-{i}", "MemorySize", f"mem-{(i * 7 + j) % MEMORY}"]
        for i in range(MODELS)
        for j in range(9)
    ]
    edges += [
        ["MemorySize", f"mem-{i}", "Color", f"color-{i % COLORS}"]
        for i in range(MEMORY)
    ]
    return {
        "slug": "perf",
        "name": "Perf",
        "levels": [
            {"name": "Vendor"},
            {"name": "Model", "parent": "Vendor"},
            {"name": "MemorySize", "parent": "Model"},
            {"name": "Color", "parent": "MemorySize"},
        ],
        "terms": terms,
        "edges": edges,
    }


def test_a_phone_sized_catalogue_loads_inside_the_budget(capsys):
    fixture = build_fixture()
    assert len(fixture["terms"]) > 15_000
    assert len(fixture["edges"]) > 150_000

    started = time.monotonic()
    result = load_fixture(fixture, replace=True)
    elapsed = time.monotonic() - started

    assert Term.objects.count() == len(fixture["terms"])
    assert TermEdge.objects.count() == len(fixture["edges"])
    assert result.terms_created == len(fixture["terms"])
    with capsys.disabled():
        print(
            f"\nload_vocabulary: {len(fixture['terms'])} terms, "
            f"{len(fixture['edges'])} edges in {elapsed:.1f}s"
        )
    assert elapsed < BUDGET_SECONDS, f"{elapsed:.1f}s exceeds the {BUDGET_SECONDS}s budget"
