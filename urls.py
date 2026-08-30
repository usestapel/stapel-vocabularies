"""Root URLconf for stapel-vocabularies — v1 canon mount (api-versioning.md §2, §6).

Canon: ``/<mod>/api/v1/...``. The host mounts
``include('stapel_vocabularies.urls')`` under ``vocabularies/``; this module
contributes the ``api/v1/`` prefix. The actual URL set lives in ``urls_v1.py``;
``GATE_REGISTRY`` is re-exported here.
"""
from django.urls import include, path

from stapel_vocabularies.urls_v1 import GATE_REGISTRY  # noqa: F401  (re-export)

urlpatterns = [
    path('api/v1/', include('stapel_vocabularies.urls_v1')),
]
