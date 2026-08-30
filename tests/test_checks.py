"""The two boot-time warnings (checks.py).

Both cover the same failure shape and it is this module's worst one: a
deployment where the term endpoints answer perfectly while every ref-typed
feature in it refuses to save. Nothing in a request path reports that, so boot
is where it gets said.
"""
import builtins

import pytest


def _ids(messages):
    return [message.id for message in messages]


def test_no_warning_in_a_correctly_wired_deployment():
    from stapel_vocabularies.checks import (
        check_resolver_protocol_available,
        check_resolver_registered_where_tables_live,
    )

    assert check_resolver_protocol_available(None) == []
    assert check_resolver_registered_where_tables_live(None) == []


def test_w001_when_the_protocol_module_is_missing(monkeypatch):
    """The floor violation, seen from a running process."""
    from stapel_vocabularies.checks import check_resolver_protocol_available

    real_import = builtins.__import__

    def refuse(name, *args, **kwargs):
        if name == "stapel_attributes.vocabularies":
            raise ImportError("no module named stapel_attributes.vocabularies")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)
    messages = check_resolver_protocol_available(None)
    assert _ids(messages) == ["stapel_vocabularies.W001"]
    assert "ref_select" in messages[0].msg


def test_w001_is_silent_where_the_host_said_it_does_not_resolve(settings, monkeypatch):
    """Opting out is a decision, not a defect."""
    from stapel_vocabularies.checks import check_resolver_protocol_available

    settings.STAPEL_VOCABULARIES = {"REGISTER_RESOLVER": False}
    real_import = builtins.__import__

    def refuse(name, *args, **kwargs):
        if name == "stapel_attributes.vocabularies":
            raise ImportError("nope")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)
    assert check_resolver_protocol_available(None) == []


def test_w002_when_the_process_holding_the_tables_declines_to_answer(settings):
    from stapel_vocabularies.checks import check_resolver_registered_where_tables_live

    settings.STAPEL_VOCABULARIES = {"REGISTER_RESOLVER": False}
    messages = check_resolver_registered_where_tables_live(None)
    assert _ids(messages) == ["stapel_vocabularies.W002"]


def test_w002_is_silent_where_this_module_serves_no_urls(settings):
    """A loader box or a migration runner owns no surface and is not reported."""
    from stapel_vocabularies.checks import check_resolver_registered_where_tables_live

    settings.STAPEL_VOCABULARIES = {"REGISTER_RESOLVER": False}
    settings.ROOT_URLCONF = "stapel_vocabularies.tests.urls_unmounted"
    assert check_resolver_registered_where_tables_live(None) == []


def test_both_checks_are_registered_with_django():
    from django.core.checks import registry

    names = {check.__name__ for check in registry.registry.get_checks()}
    assert "check_resolver_protocol_available" in names
    assert "check_resolver_registered_where_tables_live" in names


@pytest.mark.parametrize("value, expected", [
    ("true", True), ("TRUE", True), ("1", True), ("yes", True), ("on", True),
    ("false", False), ("0", False), ("no", False), ("", False),
    (True, True), (False, False),
])
def test_the_boolean_reader_understands_both_spellings(settings, value, expected):
    from stapel_vocabularies.conf import flag

    settings.STAPEL_VOCABULARIES = {"REGISTER_RESOLVER": value}
    assert flag("REGISTER_RESOLVER") is expected


def test_the_integer_reader_understands_a_string(settings):
    from stapel_vocabularies.conf import number

    settings.STAPEL_VOCABULARIES = {"CACHE_MAX_AGE": "60"}
    assert number("CACHE_MAX_AGE") == 60
