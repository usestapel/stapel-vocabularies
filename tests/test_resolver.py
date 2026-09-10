"""The two ``VocabularyResolver`` implementations (spec §3.1, §3.3).

The protocol is stapel-attributes'; this module answers it twice — from the
tables, and over comm for a service that has none. The tests that need the L1
dataclasses skip with a reason when the installed stapel-attributes predates
them, because a resolver test that silently disappears is worse than one that
says which release it is waiting for.

Everything the resolver answers, both implementations must answer the same
way: that is the whole point of having two.
"""
import pytest

from stapel_vocabularies.resolver import CommResolver, OrmResolver, register_orm_resolver

pytestmark = pytest.mark.django_db

vocab_protocol = pytest.importorskip(
    "stapel_attributes.vocabularies",
    reason="stapel-attributes >= 0.5 declares the VocabularyResolver protocol; "
    "install it to run the resolver contract tests",
)


@pytest.fixture(params=["orm", "comm"])
def resolver(request):
    """Both implementations, run through the same assertions."""
    return OrmResolver() if request.param == "orm" else CommResolver()


@pytest.fixture(autouse=True)
def _clear_registration():
    """The registry is process-wide; a test must not leak into the next one."""
    yield
    vocab_protocol.register_vocabulary_resolver(None)


# --- the protocol, answered twice -------------------------------------------


def test_both_resolvers_satisfy_the_protocol(resolver):
    assert isinstance(resolver, vocab_protocol.VocabularyResolver)


def test_describe_answers_the_level_chain(resolver, phones):
    info = resolver.describe("phones")
    assert info.slug == "phones"
    assert info.levels == (
        vocab_protocol.VocabularyLevel(name="Vendor", parent=None),
        vocab_protocol.VocabularyLevel(name="Model", parent="Vendor"),
        vocab_protocol.VocabularyLevel(name="Color", parent="Model"),
    )


def test_describe_of_an_unknown_vocabulary_is_none(resolver, phones):
    assert resolver.describe("nope") is None


def test_exists_is_scoped_to_the_level(resolver, phones):
    assert resolver.exists("phones", "Model", "iphone-10")
    assert not resolver.exists("phones", "Vendor", "iphone-10")
    assert not resolver.exists("phones", "Model", "nope")
    assert not resolver.exists("nope", "Model", "iphone-10")


def test_is_child_follows_the_edges(resolver, phones):
    assert resolver.is_child("phones", "Model", "iphone-10", "Vendor", "apple")
    assert not resolver.is_child("phones", "Model", "galaxy-s10", "Vendor", "apple")
    assert not resolver.is_child("phones", "Model", "iphone-10", "Vendor", "nokia")


def test_labels_omits_what_it_does_not_know(resolver, phones):
    assert resolver.labels("phones", "Model", ["iphone-10", "nope"]) == {
        "iphone-10": "iPhone 10"
    }
    assert resolver.labels("phones", "Model", []) == {}


# --- terms: the optional listing reader -------------------------------------


def test_terms_lists_a_level_in_the_vocabularys_own_order(resolver, makes):
    """The fixture's order, not the alphabet — `charlie` was written first.

    A browse expansion draws one tile per pair in the order it gets them, so
    "whatever order the catalogue defines" has to survive the read intact.
    """
    assert resolver.terms("makes", "Make") == [
        ("charlie", "Charlie"),
        ("alfa", "Alfa"),
        ("bravo", "Bravo"),
    ]


def test_terms_scopes_a_hierarchical_level_to_its_parent(resolver, makes):
    """Make -> Model: the second level is only answerable under a parent."""
    assert resolver.terms("makes", "Model", parent="alfa") == [
        ("alfa-one", "Alfa One"),
        ("alfa-two", "Alfa Two"),
    ]
    assert resolver.terms("makes", "Model", parent="bravo") == [
        ("bravo-one", "Bravo One")
    ]
    # Unscoped, the level is the whole level.
    assert len(resolver.terms("makes", "Model")) == 3


def test_a_parent_that_names_no_term_scopes_nothing(resolver, makes):
    """Never the whole level: an unscoped level under a parent that is not
    there is how a value from under the wrong parent reaches a tile."""
    assert resolver.terms("makes", "Model", parent="delta") == []


def test_a_parent_on_a_root_level_scopes_nothing(resolver, makes):
    """`Make` hangs off nothing, so no code can scope it — and answering the
    whole level would silently ignore what the caller asked for."""
    assert resolver.terms("makes", "Make", parent="alfa") == []


def test_terms_of_an_unknown_vocabulary_or_level_is_empty_not_a_raise(resolver, makes):
    """The consumer reads a raise as "no values" while logging a traceback per
    tree read. An empty list is the same outcome, honestly."""
    assert resolver.terms("nope", "Make") == []
    assert resolver.terms("makes", "Gone") == []


def test_terms_stops_at_the_hard_cap(resolver, makes, settings):
    """TERMS_LIMIT is a ceiling on the BROWSE read, not a page size: over it
    the first N come back rather than an error or the whole catalogue."""
    settings.STAPEL_VOCABULARIES = {"TERMS_LIMIT": 2}
    assert resolver.terms("makes", "Make") == [("charlie", "Charlie"), ("alfa", "Alfa")]


def test_an_explicit_limit_cannot_raise_the_cap(resolver, makes, settings):
    settings.STAPEL_VOCABULARIES = {"TERMS_LIMIT": 1}
    assert resolver.terms("makes", "Make", limit=50) == [("charlie", "Charlie")]


def test_the_cap_is_reported_once_per_level(makes, settings, caplog):
    """A capped level is a property of the catalogue, not of the request: a
    tree read draws it on every page view, and a line per view buries it."""
    import logging

    from stapel_vocabularies import resolver as resolver_module

    settings.STAPEL_VOCABULARIES = {"TERMS_LIMIT": 1}
    resolver_module._reported_caps.clear()
    orm = OrmResolver()
    with caplog.at_level(logging.WARNING, logger="stapel_vocabularies.resolver"):
        orm.terms("makes", "Make")
        orm.terms("makes", "Make")
    lines = [record for record in caplog.records if "TERMS_LIMIT" in record.getMessage()]
    assert len(lines) == 1


def test_an_explicit_smaller_limit_is_not_a_capped_level(makes, settings, caplog):
    """Asking for two of three is a caller paging, not a catalogue too big to
    browse — reporting it would train everyone to ignore the report."""
    import logging

    from stapel_vocabularies import resolver as resolver_module

    resolver_module._reported_caps.clear()
    orm = OrmResolver()
    with caplog.at_level(logging.WARNING, logger="stapel_vocabularies.resolver"):
        assert len(orm.terms("makes", "Make", limit=2)) == 2
    assert [r for r in caplog.records if "TERMS_LIMIT" in r.getMessage()] == []


def test_both_resolvers_answer_terms_identically(makes):
    """The seam's whole promise: a fleet cannot tell which side answered."""
    assert OrmResolver().terms("makes", "Model", parent="alfa") == CommResolver().terms(
        "makes", "Model", parent="alfa"
    )


# --- the describe cache -----------------------------------------------------


def test_the_orm_resolver_reuses_a_describe_until_the_revision_moves(phones):
    """Caching by revision, not by clock: a re-import invalidates immediately."""
    from stapel_vocabularies.loader import load_fixture

    resolver = OrmResolver()
    first = resolver.describe("phones")
    assert resolver.describe("phones") is first  # same object, rebuilt for nobody

    load_fixture(
        {
            "slug": "phones",
            "name": "Phones",
            "levels": [{"name": "Vendor"}, {"name": "Model", "parent": "Vendor"}],
            "terms": [["Vendor", "apple", "Apple", None]],
            "edges": [],
        },
        replace=True,
    )
    rebuilt = resolver.describe("phones")
    assert rebuilt is not first
    assert [level.name for level in rebuilt.levels] == ["Vendor", "Model"]


def test_the_orm_resolver_forgets_a_deleted_vocabulary(phones):
    resolver = OrmResolver()
    assert resolver.describe("phones") is not None
    phones.delete()
    assert resolver.describe("phones") is None


def test_the_comm_resolver_caches_and_the_event_invalidates(phones):
    """The remote cache is dropped by ``vocabulary.changed``, not by a timer."""
    from stapel_core.comm.actions import Event

    from stapel_vocabularies.loader import load_fixture

    resolver = CommResolver()
    calls = []
    original = resolver._call

    def counting(name, payload):
        calls.append(name)
        return original(name, payload)

    resolver._call = counting

    first = resolver.describe("phones")
    resolver.describe("phones")
    assert calls == ["vocabularies.describe"]  # the second read was cached

    load_fixture(
        {
            "slug": "phones",
            "name": "Phones",
            "levels": [{"name": "Vendor"}],
            "terms": [["Vendor", "nokia", "Nokia", None]],
            "edges": [],
        },
        replace=True,
    )
    rebuilt = resolver.describe("phones")
    assert len(calls) == 2
    assert rebuilt is not first
    assert [level.name for level in rebuilt.levels] == ["Vendor"]

    # A payload with no slug clears everything rather than guessing.
    resolver._on_changed(
        Event(event_type="vocabulary.changed", service="tests", payload={})
    )
    resolver.describe("phones")
    assert len(calls) == 3


def test_an_unchanged_revision_keeps_the_same_info_object(phones):
    """A re-fetch that finds the same revision must not rebuild the dataclass."""
    resolver = CommResolver()
    first = resolver.describe("phones")
    resolver._described.clear()  # expire the entry without changing anything
    resolver._described["phones"] = (phones.revision, first, 0.0)
    assert resolver.describe("phones") is first


# --- registration -----------------------------------------------------------


def test_register_orm_resolver_hands_the_instance_to_attributes():
    vocab_protocol.register_vocabulary_resolver(None)
    registered = register_orm_resolver()
    assert isinstance(registered, OrmResolver)
    assert vocab_protocol.get_vocabulary_resolver() is registered


def test_app_ready_registers_by_default():
    """What a host actually gets: the AppConfig did this at startup."""
    from django.apps import apps

    vocab_protocol.register_vocabulary_resolver(None)
    apps.get_app_config("vocabularies").ready()
    assert isinstance(vocab_protocol.get_vocabulary_resolver(), OrmResolver)


def test_the_flag_turns_registration_off(settings):
    from django.apps import apps

    vocab_protocol.register_vocabulary_resolver(None)
    settings.STAPEL_VOCABULARIES = {"REGISTER_RESOLVER": False}
    apps.get_app_config("vocabularies").ready()
    assert vocab_protocol.get_vocabulary_resolver() is None


def test_the_flag_reads_the_string_an_env_var_would_hand_back(settings):
    """`REGISTER_RESOLVER=false` must turn the seam OFF, not on.

    AppSettings returns environment values verbatim, and every non-empty
    string is truthy — so a deployment that meant to disable this would have
    enabled it. The coercion is the fix, and this is what pins it.
    """
    from django.apps import apps

    vocab_protocol.register_vocabulary_resolver(None)
    settings.STAPEL_VOCABULARIES = {"REGISTER_RESOLVER": "false"}
    apps.get_app_config("vocabularies").ready()
    assert vocab_protocol.get_vocabulary_resolver() is None


def test_the_comm_resolver_is_usable_from_the_attributes_setting(settings, phones):
    """The dotted-path route a service without the tables takes."""
    from django.utils.module_loading import import_string

    vocab_protocol.register_vocabulary_resolver(None)
    settings.STAPEL_ATTRIBUTES = {
        "VOCABULARY_RESOLVER": "stapel_vocabularies.resolver.CommResolver"
    }
    resolver_cls = import_string("stapel_vocabularies.resolver.CommResolver")
    assert resolver_cls is CommResolver
    assert vocab_protocol.get_vocabulary_resolver().describe("phones").slug == "phones"
