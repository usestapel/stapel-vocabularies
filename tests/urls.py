from django.urls import include, path

urlpatterns = [
    path("vocabularies/", include("stapel_vocabularies.urls")),
]
