"""The vector net under the term typeahead.

The QUERY_EXPANDER seam (0.1.3) gives the term search the fleet's
deterministic normalization — folding, transliteration, curated aliases.
What determinism cannot give it is «тимбирленд»: a phonetic spelling no
table maps onto «Timberland». For that, the deployment that runs
stapel-search can point ``VECTOR_SIMILAR_FUNCTION`` at ``search.similar``
and the typeahead asks the embedding space — only when its own answer came
back thin, only appending below its own rows, and costing nothing at all
while the setting is empty (the default).

The comm end is MOCKED: what is under test here is the gate (thin results
only), the scope (a label the vector store loves is still dropped unless it
is a term of THIS vocabulary and level), and the off state (byte-identical,
no call).
"""
import pytest
from django.test import override_settings
from stapel_core.comm import register_function
from stapel_core.comm.registry import function_registry

pytestmark = pytest.mark.django_db

BASE = "/vocabularies/api/v1"

VECTOR_ON = {"VECTOR_SIMILAR_FUNCTION": "search.similar"}


@pytest.fixture
def timberland(db):
    from stapel_vocabularies.loader import load_fixture
    from stapel_vocabularies.models import Vocabulary

    load_fixture(
        {
            "slug": "shoes",
            "name": "Shoes",
            "source": "test",
            "levels": [{"name": "brand"}],
            "terms": [
                ["brand", "timberland", "Timberland", None],
                ["brand", "salomon", "Salomon", None],
            ],
            "edges": [],
        }
    )
    return Vocabulary.objects.get(slug="shoes")


@pytest.fixture
def similar_provider():
    """A stand-in ``search.similar``, scripted per test, recording calls."""
    calls: list[dict] = []
    answer: dict = {"results": [], "degraded": []}

    def _provider(payload):
        calls.append(payload)
        return dict(answer)

    register_function("search.similar", _provider)

    class Handle:
        payloads = calls

        @staticmethod
        def answers_with(*labels, degraded=()):
            answer["results"] = [
                {"key": f"k{i}", "text": label, "payload": {}, "similarity": 0.9 - i / 100}
                for i, label in enumerate(labels)
            ]
            answer["degraded"] = list(degraded)

    try:
        yield Handle
    finally:
        function_registry._providers.pop("search.similar", None)


def test_off_by_default_and_silent(anonymous_client, timberland, similar_provider):
    resp = anonymous_client.get(f"{BASE}/vocabularies/shoes/terms/?level=brand&q=тимбирленд")
    assert resp.status_code == 200
    assert resp.json() == {"results": [], "total": 0, "popular_count": 0}
    assert similar_provider.payloads == []


def test_a_thin_answer_asks_the_vector_and_appends(
    anonymous_client, timberland, similar_provider
):
    similar_provider.answers_with("Timberland")
    with override_settings(STAPEL_VOCABULARIES=VECTOR_ON):
        resp = anonymous_client.get(
            f"{BASE}/vocabularies/shoes/terms/?level=brand&q=тимбирленд"
        )
    body = resp.json()
    assert body["results"] == [
        {
            "code": "timberland",
            "label": "Timberland",
            "level": "brand",
            "has_children": False,
            # An appended "did you mean" row is never the popular band,
            # whatever the term's own popularity: a recommended band that
            # only appears when the literal search failed is not one.
            "band": "all",
            "match": "vector",
        }
    ]
    assert body["total"] == 1
    assert body["popular_count"] == 0
    assert similar_provider.payloads[0]["kind"] == "vocab_label"
    assert similar_provider.payloads[0]["q"] == "тимбирленд"


def test_a_rich_answer_never_pays_for_a_vector(
    anonymous_client, timberland, similar_provider
):
    with override_settings(
        STAPEL_VOCABULARIES={**VECTOR_ON, "VECTOR_MIN_RESULTS": 1}
    ):
        resp = anonymous_client.get(f"{BASE}/vocabularies/shoes/terms/?level=brand&q=timber")
    assert [row["code"] for row in resp.json()["results"]] == ["timberland"]
    assert similar_provider.payloads == []


def test_a_label_outside_this_scope_is_dropped(
    anonymous_client, timberland, similar_provider
):
    """The vector store spans every corpus; the page spans one level of one
    vocabulary. A neighbour that is not a term HERE is not a row."""
    similar_provider.answers_with("iPhone 11", "Timberland")
    with override_settings(STAPEL_VOCABULARIES=VECTOR_ON):
        resp = anonymous_client.get(
            f"{BASE}/vocabularies/shoes/terms/?level=brand&q=тимбирленд"
        )
    assert [row["label"] for row in resp.json()["results"]] == ["Timberland"]


def test_a_row_already_on_the_page_is_not_appended_twice(
    anonymous_client, timberland, similar_provider
):
    similar_provider.answers_with("Timberland")
    with override_settings(
        STAPEL_VOCABULARIES={**VECTOR_ON, "VECTOR_MIN_RESULTS": 5}
    ):
        resp = anonymous_client.get(f"{BASE}/vocabularies/shoes/terms/?level=brand&q=timber")
    body = resp.json()
    assert [row["label"] for row in body["results"]] == ["Timberland"]
    assert "match" not in body["results"][0]
    assert body["total"] == 1


def test_a_comm_failure_costs_recall_never_the_response(
    anonymous_client, timberland
):
    def _broken(payload):
        raise RuntimeError("agent down")

    register_function("search.similar", _broken)
    try:
        with override_settings(STAPEL_VOCABULARIES=VECTOR_ON):
            resp = anonymous_client.get(
                f"{BASE}/vocabularies/shoes/terms/?level=brand&q=тимбирленд"
            )
        assert resp.status_code == 200
        assert resp.json() == {"results": [], "total": 0, "popular_count": 0}
    finally:
        function_registry._providers.pop("search.similar", None)


def test_paging_past_the_first_page_never_asks(
    anonymous_client, timberland, similar_provider
):
    similar_provider.answers_with("Timberland")
    with override_settings(STAPEL_VOCABULARIES=VECTOR_ON):
        resp = anonymous_client.get(
            f"{BASE}/vocabularies/shoes/terms/?level=brand&q=тимбирленд&offset=10"
        )
    assert resp.json()["results"] == []
    assert similar_provider.payloads == []


# --- the corpus provider ----------------------------------------------------


def test_label_corpus_yields_distinct_labels_of_matching_levels(
    timberland, phones
):
    from stapel_vocabularies.vector import label_corpus

    with override_settings(
        STAPEL_VOCABULARIES={"VECTOR_LABEL_LEVELS": ["brand", "Vendor"]}
    ):
        entries = list(label_corpus())
    labels = sorted(entry["text"] for entry in entries)
    assert labels == ["Apple", "Salomon", "Samsung", "Timberland"]
    assert all(entry["key"] and entry["payload"] == {} for entry in entries)


def test_label_corpus_patterns_glob(timberland, phones):
    from stapel_vocabularies.vector import label_corpus

    with override_settings(STAPEL_VOCABULARIES={"VECTOR_LABEL_LEVELS": ["bran*"]}):
        labels = sorted(entry["text"] for entry in label_corpus())
    assert labels == ["Salomon", "Timberland"]


def test_label_corpus_is_empty_when_unconfigured(timberland):
    from stapel_vocabularies.vector import label_corpus

    assert list(label_corpus()) == []
