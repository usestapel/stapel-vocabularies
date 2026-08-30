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
