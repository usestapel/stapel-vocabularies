"""The consumer rung: stapel-categories draws children through ``terms()``.

Every other test in this suite asks whether this module answers correctly.
This one asks whether the answer REACHES the thing that needed it — the
question the six leaves on a client stand were a live no to: their
``children_expand_by`` was set, their feature's ``optionsRef`` was right, and
the tree drew nothing, because the registered resolver had no ``terms``.

The check runs out of process (``tests/branching_harness.py`` says why) and
compares two answers over the same rows: a resolver with only the four
protocol methods, and this release's ``OrmResolver``.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

pytest.importorskip(
    "stapel_categories",
    reason="stapel-categories >= 0.22 is the consumer of the optional terms() "
    "reader; install it to run the branching integration check",
)


@pytest.fixture(scope="module")
def answers():
    result = subprocess.run(
        [sys.executable, "-m", "stapel_vocabularies.tests.branching_harness"],
        cwd=str(REPO),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_a_resolver_without_terms_draws_no_children(answers):
    """The RED half, and the defect this release closes: the expansion is
    configured, the vocabulary is there, and the node has no children because
    nothing could list a level."""
    assert answers["without_terms"] == []


def test_terms_turns_an_expanded_category_into_a_branch(answers):
    """The GREEN half: one virtual child per term, each carrying the filter
    the node already answers — and in the vocabulary's own order, which is
    what a catalogue means by "its brands"."""
    assert answers["with_terms"] == [
        {
            "name": "Charlie",
            "value": "charlie",
            "virtual": True,
            "filter": {"make_ref": "charlie"},
        },
        {
            "name": "Alfa",
            "value": "alfa",
            "virtual": True,
            "filter": {"make_ref": "alfa"},
        },
        {
            "name": "Bravo",
            "value": "bravo",
            "virtual": True,
            "filter": {"make_ref": "bravo"},
        },
    ]


def test_a_term_carrying_a_bag_still_draws_the_same_child(answers):
    """0.4.0's own regression, across the seam it could have broken.

    `charlie` carries `extra` in the harness fixture. The installed
    stapel-categories is a 0.3.0-era reader that takes these rows apart
    positionally, so if `terms()` had grown a third element to carry the bag,
    this node would draw wrong — or raise. It draws exactly what it drew
    before, and the bag reaches a renderer through `terms_with_extra()`
    instead.
    """
    charlie = answers["with_terms"][0]
    assert charlie == {
        "name": "Charlie",
        "value": "charlie",
        "virtual": True,
        "filter": {"make_ref": "charlie"},
    }
