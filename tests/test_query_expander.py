"""The QUERY_EXPANDER seam (spec: one normalization layer, owned elsewhere).

A classified stand taught us the shape of this defect: a buyer types
«тимберленд» into a brand picker and the term "Timberland" never appears,
because the term search matches one literal substring in one script. The
fleet already owns the cross-script layer — folding, transliteration, alias
groups — in its search library, and the rule is ONE such layer: this module
must consume it through a seam, never grow a second copy, and never depend on
the search library to stand alone.

So what is pinned here is the seam itself, with a fake on the far side:

* a term search matches EVERY variant the configured expander returns;
* a prefix hit on ANY variant still outranks a mid-label hit;
* the default is the literal query — a standalone install behaves exactly
  as it did before the seam existed;
* a config typo degrades to literal matching with a warning, because a
  picker behind somebody's keystrokes must not 500 over a dotted path.

The real expander (``stapel_search.suggest.query_terms``) is deliberately
NOT imported anywhere in this repo: the contract is the seam.
"""
import pytest

from stapel_vocabularies.loader import load_fixture

pytestmark = pytest.mark.django_db

BASE = "/vocabularies/api/v1"

HERE = "stapel_vocabularies.tests.test_query_expander"

#: The miniature of what the fleet's search library knows and this module
#: must not: that two scripts spell one brand.
ALIASES = {
    "тимберленд": "timberland",
    "айфон": "iphone",
}


def transliterating(query, language):
    """A fake of the fleet expander: the literal query plus its alias."""
    alias = ALIASES.get(query.casefold())
    return (query, alias) if alias else (query,)


#: ``(query, language)`` pairs the recording fake was asked to expand.
recorded = []


def recording(query, language):
    recorded.append((query, language))
    return (query,)


@pytest.fixture
def brands(db):
    """One level, with a ranking trap: the mid-label hit sorts first."""
    load_fixture(
        {
            "slug": "brands",
            "name": "Brands",
            "levels": [{"name": "Brand"}],
            "terms": [
                # Fixture order is the sort order, so without the
                # variant-aware prefix rank this row would top the page.
                ["Brand", "pro-timberland", "Pro Timberland", None],
                ["Brand", "timberland", "Timberland", None],
            ],
            "edges": [],
        }
    )


def _search(client, query, **extra):
    return client.get(
        f"{BASE}/vocabularies/brands/terms/",
        {"level": "Brand", "q": query},
        **extra,
    )


def test_a_cyrillic_query_finds_the_latin_term_through_the_expander(
    settings, anonymous_client, brands
):
    """The live defect, closed: «тимберленд» finds "Timberland"."""
    settings.STAPEL_VOCABULARIES = {"QUERY_EXPANDER": f"{HERE}.transliterating"}
    body = _search(anonymous_client, "тимберленд").json()
    assert body["total"] == 2
    assert {row["label"] for row in body["results"]} == {
        "Timberland",
        "Pro Timberland",
    }


def test_a_prefix_hit_on_a_variant_outranks_a_mid_label_hit(
    settings, anonymous_client, brands
):
    """The typeahead ranking survives expansion: a label starting with ANY
    variant sorts before one merely containing it — against the fixture
    order, which puts the mid-label hit first."""
    settings.STAPEL_VOCABULARIES = {"QUERY_EXPANDER": f"{HERE}.transliterating"}
    body = _search(anonymous_client, "тимберленд").json()
    assert [row["label"] for row in body["results"]] == [
        "Timberland",
        "Pro Timberland",
    ]


def test_the_default_expander_is_this_modules_literal_one(settings, anonymous_client, brands):
    """Standalone floor: no configuration, exactly yesterday's matching."""
    from stapel_vocabularies import expand
    from stapel_vocabularies.conf import vocabularies_settings

    assert vocabularies_settings.QUERY_EXPANDER is expand.literal
    assert expand.literal("тимберленд", "ru") == ("тимберленд",)
    assert _search(anonymous_client, "тимберленд").json()["total"] == 0
    assert _search(anonymous_client, "timber").json()["total"] == 2


def test_a_broken_dotted_path_degrades_to_the_literal_query(
    settings, anonymous_client, brands, caplog
):
    """A config typo must cost recall, not the picker: literal matching
    keeps working and the degradation is said out loud."""
    settings.STAPEL_VOCABULARIES = {
        "QUERY_EXPANDER": "stapel_vocabularies.expand.no_such_expander"
    }
    with caplog.at_level("WARNING"):
        latin = _search(anonymous_client, "timber")
        cyrillic = _search(anonymous_client, "тимберленд")
    assert latin.status_code == 200
    assert latin.json()["total"] == 2
    assert cyrillic.json()["total"] == 0
    assert any("QUERY_EXPANDER" in record.message for record in caplog.records)


def test_an_expander_that_raises_degrades_the_same_way(
    settings, anonymous_client, brands, caplog
):
    settings.STAPEL_VOCABULARIES = {"QUERY_EXPANDER": f"{HERE}.exploding"}
    with caplog.at_level("WARNING"):
        resp = _search(anonymous_client, "timber")
    assert resp.status_code == 200
    assert resp.json()["total"] == 2
    assert any("QUERY_EXPANDER" in record.message for record in caplog.records)


def exploding(query, language):
    raise RuntimeError("the alias table is on fire")


def test_the_expander_receives_the_negotiated_language(
    settings, anonymous_client, brands
):
    """The language argument is the label-resolution language: the best tag
    of ``Accept-Language``, empty when the client states none."""
    # Through the same import the dotted path resolves to: pytest and
    # import_string may load this file as two distinct module objects, and
    # the recording fake appends to the canonical one's list.
    from importlib import import_module

    canon = import_module(HERE)
    settings.STAPEL_VOCABULARIES = {"QUERY_EXPANDER": f"{HERE}.recording"}
    canon.recorded.clear()
    _search(anonymous_client, "timber", HTTP_ACCEPT_LANGUAGE="ru-RU,ru;q=0.9,en;q=0.8")
    _search(anonymous_client, "timber")
    assert canon.recorded == [("timber", "ru-RU"), ("timber", "")]
    canon.recorded.clear()


def test_duplicate_variants_do_not_duplicate_rows_or_break_ranking(
    settings, anonymous_client, brands
):
    """An alias group happily returns the query twice; the page must not."""
    settings.STAPEL_VOCABULARIES = {"QUERY_EXPANDER": f"{HERE}.stuttering"}
    body = _search(anonymous_client, "timberland").json()
    assert body["total"] == 2
    assert [row["label"] for row in body["results"]] == [
        "Timberland",
        "Pro Timberland",
    ]


def stuttering(query, language):
    return (query, query.upper(), query)
