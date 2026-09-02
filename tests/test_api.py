"""The read surface (spec §3.3).

This is a typeahead behind somebody's keystrokes and a cascade in a filter
panel, so what is pinned here is the shape of the answer, the ranking a
typeahead lives on, the parent filter a cascade lives on, and the caching that
keeps both cheap.
"""
import pytest

from stapel_vocabularies.loader import load_fixture
from stapel_vocabularies.models import Term

pytestmark = pytest.mark.django_db

BASE = "/vocabularies/api/v1"


@pytest.fixture
def many_models(phones):
    """Enough Apple models to page through, with a deliberate ranking trap."""
    load_fixture(
        {
            "slug": "phones",
            "name": "Phones",
            "levels": [
                {"name": "Vendor"},
                {"name": "Model", "parent": "Vendor"},
                {"name": "Color", "parent": "Model"},
            ],
            "terms": [["Vendor", "apple", "Apple", None]]
            + [
                ["Model", f"pro-{index}", f"Pro {index:03d}", None]
                for index in range(60)
            ]
            + [["Model", "not-a-pro", "Ultra Pro Max", None]],
            "edges": [
                ["Vendor", "apple", "Model", f"pro-{index}"] for index in range(60)
            ]
            + [["Vendor", "apple", "Model", "not-a-pro"]],
        },
        # Authoritative, so the counts below describe this fixture alone
        # rather than this fixture plus whatever `phones` had left behind.
        replace=True,
    )
    phones.refresh_from_db()
    return phones


# --- the catalogue ----------------------------------------------------------


def test_list_answers_every_vocabulary(anonymous_client, phones):
    resp = anonymous_client.get(f"{BASE}/vocabularies/")
    assert resp.status_code == 200, resp.content
    assert resp.json() == [
        {
            "slug": "phones",
            "name": "Phones",
            "levels": [
                {"name": "Vendor", "parent": None},
                {"name": "Model", "parent": "Vendor"},
                {"name": "Color", "parent": "Model"},
            ],
            "term_count": 6,
            "revision": phones.revision,
        }
    ]


def test_detail_answers_one(anonymous_client, phones):
    resp = anonymous_client.get(f"{BASE}/vocabularies/phones/")
    assert resp.status_code == 200
    assert resp.json()["slug"] == "phones"


def test_an_unknown_slug_is_a_404_with_a_registered_key(anonymous_client, phones):
    resp = anonymous_client.get(f"{BASE}/vocabularies/nope/")
    assert resp.status_code == 404
    assert resp.json()["localizable_error"] == "error.404.vocabularies_vocabulary_not_found"


# --- terms ------------------------------------------------------------------


def test_terms_of_a_level(anonymous_client, phones):
    resp = anonymous_client.get(f"{BASE}/vocabularies/phones/terms/?level=Vendor")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    # Nothing is promoted here, so both rows are the alphabet and there is
    # no band to draw a separator after.
    assert body["popular_count"] == 0
    assert body["results"] == [
        {
            "code": "apple",
            "label": "Apple",
            "level": "Vendor",
            "has_children": True,
            "band": "all",
        },
        {
            "code": "samsung",
            "label": "Samsung",
            "level": "Vendor",
            "has_children": True,
            "band": "all",
        },
    ]


def test_has_children_is_false_at_a_leaf(anonymous_client, phones):
    resp = anonymous_client.get(f"{BASE}/vocabularies/phones/terms/?level=Color")
    assert [row["has_children"] for row in resp.json()["results"]] == [False]


def test_a_missing_level_is_the_same_404_as_an_unknown_one(anonymous_client, phones):
    """Both spellings of "that level is not here" answer one key."""
    missing = anonymous_client.get(f"{BASE}/vocabularies/phones/terms/")
    unknown = anonymous_client.get(f"{BASE}/vocabularies/phones/terms/?level=Nope")
    for resp in (missing, unknown):
        assert resp.status_code == 404
        assert resp.json()["localizable_error"] == "error.404.vocabularies_level_not_found"


def test_parent_restricts_the_page_to_that_terms_children(anonymous_client, phones):
    resp = anonymous_client.get(
        f"{BASE}/vocabularies/phones/terms/?level=Model&parent=apple"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert [row["code"] for row in body["results"]] == ["iphone-10", "iphone-11"]


def test_an_unknown_parent_code_is_a_400(anonymous_client, phones):
    resp = anonymous_client.get(
        f"{BASE}/vocabularies/phones/terms/?level=Model&parent=nokia"
    )
    assert resp.status_code == 400
    assert resp.json()["localizable_error"] == "error.400.vocabularies_bad_parent"


def test_a_parent_on_a_root_level_is_a_400(anonymous_client, phones):
    """A root level has no parent level, so no code could be its parent."""
    resp = anonymous_client.get(
        f"{BASE}/vocabularies/phones/terms/?level=Vendor&parent=apple"
    )
    assert resp.status_code == 400
    assert resp.json()["localizable_error"] == "error.400.vocabularies_bad_parent"


def test_q_matches_anywhere_but_ranks_prefixes_first(anonymous_client, many_models):
    """The ranking a typeahead lives on: what you typed at the front, first."""
    resp = anonymous_client.get(
        f"{BASE}/vocabularies/phones/terms/?level=Model&q=pro&limit=5"
    )
    body = resp.json()
    assert body["total"] == 61
    labels = [row["label"] for row in body["results"]]
    assert labels[0].startswith("Pro ")
    assert "Ultra Pro Max" not in labels


def test_a_substring_only_match_is_still_returned(anonymous_client, many_models):
    resp = anonymous_client.get(
        f"{BASE}/vocabularies/phones/terms/?level=Model&q=ultra"
    )
    assert [row["label"] for row in resp.json()["results"]] == ["Ultra Pro Max"]


def test_limit_defaults_to_50_and_is_capped_at_200(anonymous_client, many_models):
    default = anonymous_client.get(f"{BASE}/vocabularies/phones/terms/?level=Model")
    assert len(default.json()["results"]) == 50
    assert default.json()["total"] == 61

    capped = anonymous_client.get(
        f"{BASE}/vocabularies/phones/terms/?level=Model&limit=9000"
    )
    assert len(capped.json()["results"]) == 61  # all there is, but the cap held

    Term.objects.bulk_create(
        [
            Term(
                vocabulary_id=many_models.id,
                level="Model",
                code=f"filler-{index}",
                label=f"Filler {index:03d}",
            )
            for index in range(250)
        ]
    )
    over = anonymous_client.get(
        f"{BASE}/vocabularies/phones/terms/?level=Model&limit=9000"
    )
    assert len(over.json()["results"]) == 200


def test_offset_pages_and_total_counts_the_whole_set(anonymous_client, many_models):
    first = anonymous_client.get(
        f"{BASE}/vocabularies/phones/terms/?level=Model&limit=10"
    ).json()
    second = anonymous_client.get(
        f"{BASE}/vocabularies/phones/terms/?level=Model&limit=10&offset=10"
    ).json()
    assert first["total"] == second["total"] == 61
    assert {row["code"] for row in first["results"]}.isdisjoint(
        row["code"] for row in second["results"]
    )


def test_nonsense_paging_falls_back_to_the_defaults(anonymous_client, many_models):
    resp = anonymous_client.get(
        f"{BASE}/vocabularies/phones/terms/?level=Model&limit=abc&offset=-5"
    )
    assert resp.status_code == 200
    assert len(resp.json()["results"]) == 50


# --- resolve ----------------------------------------------------------------


def test_resolve_maps_codes_to_labels_and_omits_the_unknown(anonymous_client, phones):
    resp = anonymous_client.get(
        f"{BASE}/vocabularies/phones/terms/resolve/?level=Model&codes=iphone-10,nope,galaxy-s10"
    )
    assert resp.status_code == 200
    assert resp.json() == {"iphone-10": "iPhone 10", "galaxy-s10": "Galaxy S10"}


def test_resolve_needs_a_known_level(anonymous_client, phones):
    resp = anonymous_client.get(
        f"{BASE}/vocabularies/phones/terms/resolve/?level=Nope&codes=x"
    )
    assert resp.status_code == 404
    assert resp.json()["localizable_error"] == "error.404.vocabularies_level_not_found"


def test_resolve_is_capped_at_200_codes(anonymous_client, many_models):
    codes = ",".join(f"pro-{index}" for index in range(60)) + "," + ",".join(
        f"unknown-{index}" for index in range(300)
    )
    resp = anonymous_client.get(
        f"{BASE}/vocabularies/phones/terms/resolve/?level=Model&codes={codes}"
    )
    assert resp.status_code == 200
    # The first 200 named codes are looked up; the 60 real ones are among them.
    assert len(resp.json()) == 60


def test_resolve_of_an_unknown_vocabulary_is_a_404(anonymous_client, phones):
    resp = anonymous_client.get(
        f"{BASE}/vocabularies/nope/terms/resolve/?level=Model&codes=x"
    )
    assert resp.status_code == 404
    assert resp.json()["localizable_error"] == "error.404.vocabularies_vocabulary_not_found"


# --- Accept-Language --------------------------------------------------------


def test_accept_language_picks_a_translated_label(anonymous_client, phones):
    Term.objects.filter(code="chernyy").update(labels={"en": "black", "de": "schwarz"})
    resp = anonymous_client.get(
        f"{BASE}/vocabularies/phones/terms/?level=Color", HTTP_ACCEPT_LANGUAGE="en-GB,en;q=0.9"
    )
    assert resp.json()["results"][0]["label"] == "black"


def test_the_untranslated_label_is_the_fallback(anonymous_client, phones):
    Term.objects.filter(code="chernyy").update(labels={"en": "black"})
    resp = anonymous_client.get(
        f"{BASE}/vocabularies/phones/terms/?level=Color", HTTP_ACCEPT_LANGUAGE="fr"
    )
    assert resp.json()["results"][0]["label"] == "чёрный"


def test_quality_values_order_the_preference(anonymous_client, phones):
    Term.objects.filter(code="chernyy").update(labels={"en": "black", "de": "schwarz"})
    resp = anonymous_client.get(
        f"{BASE}/vocabularies/phones/terms/?level=Color",
        HTTP_ACCEPT_LANGUAGE="en;q=0.2, de;q=0.9",
    )
    assert resp.json()["results"][0]["label"] == "schwarz"


def test_resolve_honours_accept_language_too(anonymous_client, phones):
    Term.objects.filter(code="chernyy").update(labels={"en": "black"})
    resp = anonymous_client.get(
        f"{BASE}/vocabularies/phones/terms/resolve/?level=Color&codes=chernyy",
        HTTP_ACCEPT_LANGUAGE="en",
    )
    assert resp.json() == {"chernyy": "black"}


def test_the_language_parser_drops_the_wildcard():
    from stapel_vocabularies.views import parse_accept_language

    assert parse_accept_language("*") == []
    assert parse_accept_language("") == []
    assert parse_accept_language(None) == []
    assert parse_accept_language("ru-RU,ru;q=0.9,en;q=0.8") == ["ru-RU", "ru", "en"]


# --- caching ----------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/vocabularies/",
        "/vocabularies/phones/",
        "/vocabularies/phones/terms/?level=Vendor",
        "/vocabularies/phones/terms/resolve/?level=Vendor&codes=apple",
    ],
)
def test_every_read_carries_an_etag_and_a_public_max_age(anonymous_client, phones, path):
    resp = anonymous_client.get(f"{BASE}{path}")
    assert resp.status_code == 200
    assert resp["ETag"]
    assert resp["Cache-Control"] == "public, max-age=300"
    assert "Accept-Language" in resp["Vary"]


def test_a_matching_if_none_match_answers_304(anonymous_client, phones):
    first = anonymous_client.get(f"{BASE}/vocabularies/phones/terms/?level=Vendor")
    again = anonymous_client.get(
        f"{BASE}/vocabularies/phones/terms/?level=Vendor",
        HTTP_IF_NONE_MATCH=first["ETag"],
    )
    assert again.status_code == 304
    assert again["ETag"] == first["ETag"]


def test_a_weak_validator_still_matches(anonymous_client, phones):
    first = anonymous_client.get(f"{BASE}/vocabularies/phones/")
    again = anonymous_client.get(
        f"{BASE}/vocabularies/phones/", HTTP_IF_NONE_MATCH=f'W/{first["ETag"]}'
    )
    assert again.status_code == 304


def test_a_reload_changes_the_etag(anonymous_client, phones):
    before = anonymous_client.get(f"{BASE}/vocabularies/phones/terms/?level=Vendor")
    load_fixture(
        {
            "slug": "phones",
            "name": "Phones",
            "levels": [{"name": "Vendor"}],
            "terms": [["Vendor", "nokia", "Nokia", None]],
            "edges": [],
        }
    )
    after = anonymous_client.get(f"{BASE}/vocabularies/phones/terms/?level=Vendor")
    assert after["ETag"] != before["ETag"]
    assert after.status_code == 200


def test_a_different_query_is_a_different_etag(anonymous_client, phones):
    vendors = anonymous_client.get(f"{BASE}/vocabularies/phones/terms/?level=Vendor")
    models = anonymous_client.get(f"{BASE}/vocabularies/phones/terms/?level=Model")
    assert vendors["ETag"] != models["ETag"]


def test_a_different_language_is_a_different_etag(anonymous_client, phones):
    """The labels in the body depend on it, so the validator must too."""
    plain = anonymous_client.get(f"{BASE}/vocabularies/phones/terms/?level=Color")
    english = anonymous_client.get(
        f"{BASE}/vocabularies/phones/terms/?level=Color", HTTP_ACCEPT_LANGUAGE="en"
    )
    assert plain["ETag"] != english["ETag"]
