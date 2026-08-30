"""Canonical-prefix URLconf for contract emission (contract-pipeline.md §2).

Reproduces the documented host mount, so drf-spectacular emits
``/vocabularies/api/v1/...`` paths — the same ``<mod>/api/v1/`` shape every
other module uses.
"""
from django.urls import include, path

urlpatterns = [
    path("vocabularies/", include("stapel_vocabularies.urls")),
]
