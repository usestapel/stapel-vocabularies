def pytest_configure(config):
    from django.conf import settings
    if not settings.configured:
        # Single source of truth for this block lives in _codegen_settings.py so
        # the test harness and the contract-emission harness (make contract) can
        # never drift (contract-pipeline.md §3).
        from stapel_vocabularies._codegen_settings import settings_kwargs

        settings.configure(**settings_kwargs())
        import django
        django.setup()

        from stapel_core.comm.schemas import autoload_schemas
        autoload_schemas()


import pytest  # noqa: E402


@pytest.fixture
def api_client():
    from rest_framework.test import APIClient
    return APIClient()


@pytest.fixture
def anonymous_client(api_client):
    """No credentials whatsoever — the caller this surface mostly serves."""
    return api_client


@pytest.fixture
def staff_user(db):
    from django.contrib.auth import get_user_model
    return get_user_model().objects.create_user(
        username="ops", email="ops@example.com", password="x", is_staff=True
    )


@pytest.fixture
def captured_events():
    """Every ``vocabulary.changed`` emitted during the test, in order.

    Delivery is synchronous with OUTBOX disabled, so the list is populated by
    the time the load returns.
    """
    from stapel_core.comm import action_registry, subscribe_action

    collected = []

    def _handler(event):
        collected.append(event)

    subscribe_action("vocabulary.changed", _handler)
    try:
        yield collected
    finally:
        handlers = action_registry._subscribers.get("vocabulary.changed", [])
        if _handler in handlers:
            handlers.remove(_handler)


@pytest.fixture
def phones(db):
    """A small four-level vocabulary, the phone catalogue in miniature."""
    from stapel_vocabularies.loader import load_fixture

    load_fixture(
        {
            "slug": "phones",
            "name": "Phones",
            "source": "test",
            "levels": [
                {"name": "Vendor"},
                {"name": "Model", "parent": "Vendor"},
                {"name": "Color", "parent": "Model"},
            ],
            "terms": [
                ["Vendor", "apple", "Apple", None],
                ["Vendor", "samsung", "Samsung", None],
                ["Model", "iphone-10", "iPhone 10", "10"],
                ["Model", "iphone-11", "iPhone 11", "11"],
                ["Model", "galaxy-s10", "Galaxy S10", None],
                ["Color", "chernyy", "чёрный", None],
            ],
            "edges": [
                ["Vendor", "apple", "Model", "iphone-10"],
                ["Vendor", "apple", "Model", "iphone-11"],
                ["Vendor", "samsung", "Model", "galaxy-s10"],
                ["Model", "iphone-10", "Color", "chernyy"],
            ],
        }
    )
    from stapel_vocabularies.models import Vocabulary

    return Vocabulary.objects.get(slug="phones")
