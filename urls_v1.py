"""v1 URL set for stapel-vocabularies (api-versioning.md §2, §6).

No global prefix here — the root ``urls.py`` mounts this under ``api/v1/`` and
the host mounts that under ``vocabularies/``:

    path("vocabularies/", include("stapel_vocabularies.urls"))
    # -> /vocabularies/api/v1/vocabularies/...
"""
from typing import NamedTuple

from django.urls import path

from .views import (
    TermListView,
    TermResolveView,
    VocabularyDetailView,
    VocabularyListView,
)

urlpatterns = [
    path("vocabularies/", VocabularyListView.as_view(), name="vocabularies-list"),
    path(
        "vocabularies/<str:slug>/",
        VocabularyDetailView.as_view(),
        name="vocabularies-detail",
    ),
    # `resolve/` is declared BEFORE the terms listing it looks like a child of:
    # both are `terms/...` and the router takes the first match, so the order
    # is what keeps the two apart rather than a lookahead in the pattern.
    path(
        "vocabularies/<str:slug>/terms/resolve/",
        TermResolveView.as_view(),
        name="vocabularies-terms-resolve",
    ),
    path(
        "vocabularies/<str:slug>/terms/",
        TermListView.as_view(),
        name="vocabularies-terms",
    ),
]


class GateEntry(NamedTuple):
    """One gated URL block: which flags gate which url patterns (capability-config.md §2 p.2).

    ``flags`` compose with OR — the block is mounted while ANY flag is on, and
    disappears only when ALL of them are off. Empty flags = always on.
    """

    name: str
    flags: tuple
    patterns: tuple


#: Gate registry (capability-config.md §2 p.2): this module's only axis
#: (REGISTER_RESOLVER) decides who ANSWERS resolver questions in-process, not
#: which endpoints exist, so the read surface is one always-on block.
GATE_REGISTRY: dict = {
    "vocabularies.api": GateEntry("vocabularies.api", (), tuple(urlpatterns)),
}
