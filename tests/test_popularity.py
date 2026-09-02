"""The popular band: what a market leader puts at the top of a dictionary.

Alphabetical is not an order, it is the absence of one. A live stand's
`Vendor` level holds 529 brands, and the first page of a strictly
alphabetical listing is `3Q, 4Good, 8848, A1, Aceline, Acer, AEG,
AGGRESSOR, AGM, AGmobile, AIEK, Aimoto` — twelve rows, none of which anyone
has ever typed into a phone-listing form, while the two brands that carry
most of the catalogue's volume sit hundreds of rows down. Every marketplace
that sells phones opens that control on a short band of recommended
options and the alphabet underneath it.

`Term.sort` cannot carry that: it is the fixture row's explicit rank
(0.1.5), the curated order WITHIN a band, and one channel cannot serve two
rules — that is the defect 0.1.5 fixed and this module is not about to
reintroduce it one field over. So the band is its own signal,
`Term.popularity`, derived from observed listing counts a host service
holds (`ranking.apply_popularity`) or shipped curated in a fixture's 6th
column until there is data.

What is pinned here is the ordering, the band boundary ON THE WIRE (a
frontend draws a separator; it must not have to guess where it falls), and
the fact that a typeahead's prefix rank still outranks the whole band.
"""
import pytest
from django.test import override_settings

from stapel_vocabularies.loader import load_fixture
from stapel_vocabularies.models import Term, Vocabulary

pytestmark = pytest.mark.django_db

BASE = "/vocabularies/api/v1"


@pytest.fixture
def handsets(db):
    """The 529-vendor level in miniature, with the same trap in it.

    Forty `A..`-labelled nobodies sort ahead of every brand a person types,
    exactly as `3Q / 4Good / 8848 / A1` do on the stand. `Ultra A1 Max` is
    the ranking trap `many_models` uses: it matches `q=A1` as a substring
    but not as a prefix, so it is what a popular band would wrongly hoist
    over a prefix hit.
    """
    load_fixture(
        {
            "slug": "handsets",
            "name": "Handsets",
            "source": "test",
            "levels": [{"name": "Vendor"}, {"name": "Model", "parent": "Vendor"}],
            "terms": [
                ["Vendor", f"a{index:02d}", f"A{index:02d} Telecom", None]
                for index in range(40)
            ]
            + [
                ["Vendor", "apple", "Apple", None],
                ["Vendor", "samsung", "Samsung", None],
                ["Vendor", "sony", "Sony", None],
                ["Vendor", "ultra-a1", "Ultra A1 Max", None],
                ["Model", "galaxy-s10", "Galaxy S10", None],
            ],
            "edges": [["Vendor", "samsung", "Model", "galaxy-s10"]],
        },
        replace=True,
    )
    return Vocabulary.objects.get(slug="handsets")


def _codes(client, query):
    return [row["code"] for row in client.get(f"{BASE}{query}").json()["results"]]


# --- the defect -------------------------------------------------------------


def test_the_alphabetical_head_buries_the_brands_people_type(
    anonymous_client, handsets
):
    """Defect A, and the fix, in one test.

    Before: twelve rows of nobody. After one push of observed counts: the
    two brands that carry the volume lead, and the alphabet follows them.
    """
    from stapel_vocabularies.ranking import apply_popularity

    head = _codes(anonymous_client, "/vocabularies/handsets/terms/?level=Vendor&limit=12")
    assert "samsung" not in head
    assert "apple" not in head

    apply_popularity(handsets, "Vendor", {"samsung": 9_000, "apple": 7_000})

    head = _codes(anonymous_client, "/vocabularies/handsets/terms/?level=Vendor&limit=12")
    assert head[:2] == ["samsung", "apple"]
    assert head[2] == "a00"


def test_the_band_keeps_its_own_curated_order_inside_itself(
    anonymous_client, handsets
):
    """`-popularity` inside the band, `sort, label` under it — two rules,
    two channels, neither borrowing the other's."""
    from stapel_vocabularies.ranking import apply_popularity

    apply_popularity(handsets, "Vendor", {"apple": 10, "samsung": 900, "sony": 5})
    head = _codes(anonymous_client, "/vocabularies/handsets/terms/?level=Vendor&limit=5")
    assert head[:3] == ["samsung", "apple", "sony"]


# --- the band boundary on the wire ------------------------------------------


def _page(client, query):
    return client.get(f"{BASE}{query}").json()


def test_popular_count_matches_the_leading_run_on_the_first_page(
    anonymous_client, handsets
):
    from stapel_vocabularies.ranking import apply_popularity

    apply_popularity(
        handsets,
        "Vendor",
        {"samsung": 9, "apple": 8, "sony": 7, "ultra-a1": 6, "a39": 5},
    )
    body = _page(anonymous_client, "/vocabularies/handsets/terms/?level=Vendor&limit=3")
    assert [row["band"] for row in body["results"]] == ["popular"] * 3
    assert body["popular_count"] == 3


def test_popular_count_stops_at_the_boundary_a_page_straddles(
    anonymous_client, handsets
):
    from stapel_vocabularies.ranking import apply_popularity

    apply_popularity(
        handsets,
        "Vendor",
        {"samsung": 9, "apple": 8, "sony": 7, "ultra-a1": 6, "a39": 5},
    )
    body = _page(
        anonymous_client,
        "/vocabularies/handsets/terms/?level=Vendor&limit=10&offset=3",
    )
    bands = [row["band"] for row in body["results"]]
    assert bands[:2] == ["popular", "popular"]
    assert bands[2] == "all"
    assert body["popular_count"] == 2


def test_a_page_entirely_past_the_band_declares_no_band(anonymous_client, handsets):
    from stapel_vocabularies.ranking import apply_popularity

    apply_popularity(handsets, "Vendor", {"samsung": 9, "apple": 8})
    body = _page(
        anonymous_client,
        "/vocabularies/handsets/terms/?level=Vendor&limit=10&offset=20",
    )
    assert body["popular_count"] == 0
    assert {row["band"] for row in body["results"]} == {"all"}


def test_no_popular_term_at_all_is_a_band_of_zero(anonymous_client, handsets):
    body = _page(anonymous_client, "/vocabularies/handsets/terms/?level=Vendor&limit=5")
    assert body["popular_count"] == 0
    assert {row["band"] for row in body["results"]} == {"all"}


def test_the_rendered_band_is_capped_by_popular_band_size(
    anonymous_client, handsets
):
    """A curated fixture can promote more rows than the frontend's band
    holds; the wire caps what it calls `popular` regardless."""
    from stapel_vocabularies.ranking import apply_popularity

    apply_popularity(
        handsets,
        "Vendor",
        {"samsung": 9, "apple": 8, "sony": 7, "ultra-a1": 6, "a39": 5},
        band_size=5,
    )
    with override_settings(STAPEL_VOCABULARIES={"POPULAR_BAND_SIZE": 2}):
        body = _page(
            anonymous_client, "/vocabularies/handsets/terms/?level=Vendor&limit=5"
        )
    assert body["popular_count"] == 2
    assert [row["band"] for row in body["results"]][:3] == ["popular", "popular", "all"]


def test_the_band_size_moves_the_etag(anonymous_client, handsets):
    """It changes the response bytes, so it must change the validator."""
    from stapel_vocabularies.ranking import apply_popularity

    apply_popularity(handsets, "Vendor", {"samsung": 9, "apple": 8})
    twelve = anonymous_client.get(f"{BASE}/vocabularies/handsets/terms/?level=Vendor")
    with override_settings(STAPEL_VOCABULARIES={"POPULAR_BAND_SIZE": 1}):
        one = anonymous_client.get(f"{BASE}/vocabularies/handsets/terms/?level=Vendor")
    assert twelve["ETag"] != one["ETag"]


def test_a_popularity_push_moves_the_etag(anonymous_client, handsets):
    """The order of the answer changed; a cached client must be told."""
    from stapel_vocabularies.ranking import apply_popularity

    before = anonymous_client.get(f"{BASE}/vocabularies/handsets/terms/?level=Vendor")
    apply_popularity(handsets, "Vendor", {"samsung": 9})
    after = anonymous_client.get(f"{BASE}/vocabularies/handsets/terms/?level=Vendor")
    assert after["ETag"] != before["ETag"]


# --- the typeahead still wins ----------------------------------------------


def test_a_prefix_hit_outranks_the_whole_popular_band(anonymous_client, handsets):
    """What you typed, at the front of a label, first — the band never
    displaces that. `Ultra A1 Max` is popular AND matches `A1`, but only as
    a substring, so it stays under every `A1..` prefix hit."""
    from stapel_vocabularies.ranking import apply_popularity

    apply_popularity(handsets, "Vendor", {"ultra-a1": 9_000})
    codes = _codes(
        anonymous_client, "/vocabularies/handsets/terms/?level=Vendor&q=A1&limit=20"
    )
    assert codes[0] == "a10"
    assert codes[-1] == "ultra-a1"


def test_the_band_still_orders_within_one_prefix_rank(anonymous_client, handsets):
    from stapel_vocabularies.ranking import apply_popularity

    apply_popularity(handsets, "Vendor", {"a19": 9_000})
    codes = _codes(
        anonymous_client, "/vocabularies/handsets/terms/?level=Vendor&q=A1&limit=20"
    )
    assert codes[0] == "a19"


# --- apply_popularity -------------------------------------------------------


def test_apply_popularity_ranks_by_observed_count_and_reports_how_many(handsets):
    from stapel_vocabularies.ranking import apply_popularity

    assert apply_popularity(handsets, "Vendor", {"apple": 10, "samsung": 900}) == 2
    ranked = dict(
        Term.objects.filter(level="Vendor", popularity__gt=0).values_list(
            "code", "popularity"
        )
    )
    assert ranked["samsung"] > ranked["apple"] > 0
    assert set(ranked) == {"samsung", "apple"}


def test_apply_popularity_is_idempotent(handsets):
    from stapel_vocabularies.ranking import apply_popularity

    counts = {"apple": 10, "samsung": 900, "sony": 3}
    apply_popularity(handsets, "Vendor", counts)
    first = sorted(Term.objects.values_list("code", "popularity"))
    handsets.refresh_from_db()
    revision = handsets.revision

    assert apply_popularity(handsets, "Vendor", dict(counts)) == 3
    assert sorted(Term.objects.values_list("code", "popularity")) == first
    handsets.refresh_from_db()
    # Nothing changed, so nothing downstream is invalidated either.
    assert handsets.revision == revision


def test_a_term_whose_count_dropped_is_demoted(handsets):
    from stapel_vocabularies.ranking import apply_popularity

    apply_popularity(handsets, "Vendor", {"apple": 10, "samsung": 900})
    assert Term.objects.get(code="samsung").popularity > 0

    apply_popularity(handsets, "Vendor", {"apple": 10})
    assert Term.objects.get(code="samsung").popularity == 0
    assert Term.objects.get(code="apple").popularity > 0


def test_the_band_is_capped_at_band_size(handsets):
    from stapel_vocabularies.ranking import apply_popularity

    counts = {f"a{index:02d}": 1000 - index for index in range(40)}
    assert apply_popularity(handsets, "Vendor", counts) == 12
    assert Term.objects.filter(popularity__gt=0).count() == 12
    assert Term.objects.get(code="a00").popularity > 0
    assert Term.objects.get(code="a12").popularity == 0


def test_a_count_for_a_code_that_is_not_a_term_ranks_nothing(handsets):
    from stapel_vocabularies.ranking import apply_popularity

    assert apply_popularity(handsets, "Vendor", {"nokia": 9_000}) == 0
    assert Term.objects.filter(popularity__gt=0).count() == 0


def test_popularity_is_scoped_to_one_level(handsets):
    from stapel_vocabularies.ranking import apply_popularity

    apply_popularity(handsets, "Model", {"galaxy-s10": 5})
    apply_popularity(handsets, "Vendor", {"samsung": 5})
    # Ranking Vendors did not zero the Model band.
    assert Term.objects.get(code="galaxy-s10").popularity > 0


def test_an_empty_count_map_demotes_the_whole_level(handsets):
    from stapel_vocabularies.ranking import apply_popularity

    apply_popularity(handsets, "Vendor", {"samsung": 5})
    assert apply_popularity(handsets, "Vendor", {}) == 0
    assert Term.objects.filter(level="Vendor", popularity__gt=0).count() == 0


def test_apply_popularity_takes_a_slug_as_well_as_an_instance(handsets):
    from stapel_vocabularies.ranking import apply_popularity

    assert apply_popularity("handsets", "Vendor", {"samsung": 5}) == 1


def test_apply_popularity_does_not_scale_with_the_level(handsets):
    """Not one UPDATE per term: one CASE over the band, one demotion.

    Measured as INVARIANCE rather than as a magic number — what matters is
    that a nightly push against a 15 000-model level costs what a push
    against a 44-vendor level costs. A statement per term would pass every
    other test in this file and turn the job into an outage.
    """
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from stapel_vocabularies.ranking import apply_popularity

    counts = {f"a{index:02d}": 1000 - index for index in range(40)}
    with CaptureQueriesContext(connection) as small:
        apply_popularity(handsets, "Vendor", counts)

    Term.objects.bulk_create(
        [
            Term(
                vocabulary=handsets,
                level="Vendor",
                code=f"filler-{index}",
                label=f"Filler {index:03d}",
            )
            for index in range(400)
        ]
    )
    with CaptureQueriesContext(connection) as large:
        apply_popularity(handsets, "Vendor", {**counts, "a00": 1})

    assert len(large.captured_queries) == len(small.captured_queries)
    assert len(small.captured_queries) < 15


# --- the curated fallback in a fixture --------------------------------------


def test_a_fixture_can_ship_a_curated_rank(db):
    load_fixture(
        {
            "slug": "curated",
            "name": "Curated",
            "levels": [{"name": "brand"}],
            "terms": [
                ["brand", "aceline", "Aceline", None, 0],
                ["brand", "apple", "Apple", None, 0, 90],
                ["brand", "samsung", "Samsung", None, 0, 100],
            ],
            "edges": [],
        }
    )
    assert dict(
        Term.objects.filter(vocabulary__slug="curated").values_list(
            "code", "popularity"
        )
    ) == {"aceline": 0, "apple": 90, "samsung": 100}


def test_a_row_without_the_column_keeps_a_pushed_rank(handsets):
    """Observed counts outlive a catalogue re-import.

    `popularity` is derived from a host's live listing volume, not from the
    file; a 5-column row is silent about it, and silence must not mean
    zero. Otherwise every nightly count push would be erased by the next
    catalogue import.
    """
    from stapel_vocabularies.ranking import apply_popularity

    apply_popularity(handsets, "Vendor", {"samsung": 900})
    load_fixture(
        {
            "slug": "handsets",
            "name": "Handsets",
            "levels": [{"name": "Vendor"}],
            "terms": [["Vendor", "samsung", "Samsung Electronics", None]],
            "edges": [],
        }
    )
    term = Term.objects.get(code="samsung")
    assert term.label == "Samsung Electronics"
    assert term.popularity > 0


def test_a_stated_rank_overwrites_a_pushed_one(handsets):
    from stapel_vocabularies.ranking import apply_popularity

    apply_popularity(handsets, "Vendor", {"samsung": 900})
    load_fixture(
        {
            "slug": "handsets",
            "name": "Handsets",
            "levels": [{"name": "Vendor"}],
            "terms": [["Vendor", "samsung", "Samsung", None, 0, 3]],
            "edges": [],
        }
    )
    assert Term.objects.get(code="samsung").popularity == 3


def test_a_non_integer_popularity_column_is_refused_by_name(db):
    from stapel_vocabularies.loader import FixtureError, validate_fixture

    with pytest.raises(FixtureError) as caught:
        validate_fixture(
            {
                "slug": "bad",
                "name": "Bad",
                "levels": [{"name": "brand"}],
                "terms": [["brand", "apple", "Apple", None, 0, "very"]],
            }
        )
    assert "popularity" in str(caught.value)


def test_a_seventh_column_is_refused(db):
    from stapel_vocabularies.loader import FixtureError, validate_fixture

    with pytest.raises(FixtureError):
        validate_fixture(
            {
                "slug": "bad",
                "name": "Bad",
                "levels": [{"name": "brand"}],
                "terms": [["brand", "apple", "Apple", None, 0, 1, 1]],
            }
        )


# --- the comm push ----------------------------------------------------------


def test_set_popularity_over_comm_ranks_the_level(handsets):
    from stapel_core.comm import call

    answer = call(
        "vocabularies.set_popularity",
        {
            "vocabulary": "handsets",
            "level": "Vendor",
            "counts": {"samsung": 900, "apple": 700},
        },
    )
    assert answer["ranked"] == 2
    assert answer["revision"] > handsets.revision
    assert Term.objects.get(code="samsung").popularity > 0


def test_set_popularity_of_an_unknown_vocabulary_is_none(handsets):
    from stapel_core.comm import call

    assert (
        call(
            "vocabularies.set_popularity",
            {"vocabulary": "nope", "level": "Vendor", "counts": {"a": 1}},
        )
        is None
    )


def test_set_popularity_of_an_unknown_level_is_none(handsets):
    from stapel_core.comm import call

    assert (
        call(
            "vocabularies.set_popularity",
            {"vocabulary": "handsets", "level": "Nope", "counts": {"a": 1}},
        )
        is None
    )
