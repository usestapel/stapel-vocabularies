"""Admin for reference vocabularies — read-oriented on purpose.

A vocabulary is loaded from a reviewed fixture (``manage.py
load_vocabulary``), not typed in: 15 000 phone models are data plumbing, and a
hand-edited term is a code that the next import silently reverts. What the
admin is for is *looking*: which catalogues are loaded, at what revision, how
many terms, and finding one term by label.
"""
from django.contrib import admin

from .models import Term, TermEdge, Vocabulary


@admin.register(Vocabulary)
class VocabularyAdmin(admin.ModelAdmin):
    list_display = ("slug", "name", "term_count", "revision", "source")
    search_fields = ("slug", "name")
    readonly_fields = ("term_count", "revision")


@admin.register(Term)
class TermAdmin(admin.ModelAdmin):
    list_display = ("code", "label", "level", "vocabulary", "sort", "popularity")
    list_filter = ("vocabulary", "level")
    search_fields = ("code", "label", "external_id")
    raw_id_fields = ("vocabulary",)


@admin.register(TermEdge)
class TermEdgeAdmin(admin.ModelAdmin):
    list_display = ("parent", "child")
    raw_id_fields = ("parent", "child")
