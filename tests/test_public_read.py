"""The public-read posture of the vocabularies, as a contract.

A term list is the *navigation* of a composer and a filter panel: it is asked
for on a keystroke, by a client that has not authenticated yet and may never.
That anonymous reads work is not an accident of which permission class
happened to be typed on four views — it is what the surface is for, and
without this file swapping ``ReadOnlyOrStaff`` for ``IsStaffUser`` would leave
the rest of the suite green while every typeahead on the internet turned into
a 401.

Three things pinned here:

* **Reads are open with no credentials at all.**
* **Writes are not a thing this surface does** — a vocabulary is loaded by an
  operator command, so every mutating verb is refused.
* **The read costs no cookie.** A ``Set-Cookie`` would make these responses
  uncacheable at the edge and start a session per crawler — on the one
  surface whose whole economy is the shared cache in front of it.
"""
import pytest
from stapel_core.django.api.permissions import ReadOnlyOrStaff

from stapel_vocabularies.views import (
    TermListView,
    TermResolveView,
    VocabularyDetailView,
    VocabularyListView,
)

pytestmark = pytest.mark.django_db

BASE = "/vocabularies/api/v1"

PATHS = [
    "/vocabularies/",
    "/vocabularies/phones/",
    "/vocabularies/phones/terms/?level=Vendor",
    "/vocabularies/phones/terms/resolve/?level=Vendor&codes=apple",
]


def test_every_read_view_is_open_to_anyone():
    """The four lines the whole public surface rests on.

    Named so a regression to a staff-only permission fails a test that says
    why, rather than a pile of tests that say ``401 != 200``.
    """
    for view in (
        VocabularyListView,
        VocabularyDetailView,
        TermListView,
        TermResolveView,
    ):
        assert view.permission_classes == [ReadOnlyOrStaff], view.__name__


@pytest.mark.parametrize("path", PATHS)
def test_a_stranger_can_read(anonymous_client, phones, path):
    assert anonymous_client.get(f"{BASE}{path}").status_code == 200


@pytest.mark.parametrize("path", PATHS)
def test_no_read_sets_a_cookie(anonymous_client, phones, path):
    resp = anonymous_client.get(f"{BASE}{path}")
    assert list(resp.cookies.keys()) == []


@pytest.mark.parametrize("verb", ["post", "put", "patch", "delete"])
def test_the_surface_writes_nothing(anonymous_client, phones, verb):
    """Loading is `manage.py load_vocabulary`, not an HTTP write."""
    resp = getattr(anonymous_client, verb)(f"{BASE}/vocabularies/")
    assert resp.status_code in (401, 403, 405), resp.status_code


def test_a_staff_write_is_refused_too_because_there_is_no_writer(
    api_client, staff_user, phones
):
    api_client.force_authenticate(user=staff_user)
    resp = api_client.post(f"{BASE}/vocabularies/", {"slug": "x"}, format="json")
    assert resp.status_code == 405
