"""Response serializers for the public vocabulary reads.

Read-only surface: these exist to give drf-spectacular a real component to
emit (``docs/schema.json`` is a contract, not a description) and to give a
host one place to override the shape per the ``SerializerSeamMixin``
convention.
"""
from rest_framework import serializers


class LevelSerializer(serializers.Serializer):
    """One level of a vocabulary."""

    name = serializers.CharField(max_length=64)
    parent = serializers.CharField(
        allow_null=True,
        required=False,
        help_text="Name of the level this one hangs off; null for a root level.",
    )


class VocabularySerializer(serializers.Serializer):
    """A vocabulary as the catalogue endpoints render it."""

    slug = serializers.CharField(max_length=64)
    name = serializers.CharField(max_length=200)
    levels = LevelSerializer(many=True)
    term_count = serializers.IntegerField()
    revision = serializers.IntegerField(
        help_text="Cache key of the whole vocabulary; also the ETag and the "
        "vocabulary.changed payload."
    )


class TermSerializer(serializers.Serializer):
    """One term of one level."""

    code = serializers.CharField(max_length=128)
    label = serializers.CharField(
        max_length=255,
        help_text="Resolved for the request's Accept-Language when the term "
        "carries a translation, otherwise the term's own label.",
    )
    level = serializers.CharField(max_length=64)
    has_children = serializers.BooleanField(
        help_text="Whether this term has any child term — what tells a "
        "cascading control whether to ask for the next level."
    )
    band = serializers.ChoiceField(
        choices=["popular", "all"],
        help_text="Which band this row is in: `popular` for the short "
        "recommended band the level opens on, `all` for the alphabet under "
        "it. Rows are ordered popular-band-first, so a control renders the "
        "separator from `popular_count` rather than by scanning for the "
        "change.",
    )
    match = serializers.CharField(
        required=False,
        help_text="Present (value `vector`) only on rows the similarity net "
        "appended under a thin deterministic answer — a 'did you mean' row. "
        "Absent on every literal match.",
    )


class TermPageSerializer(serializers.Serializer):
    """One page of terms plus the size of the whole filtered set."""

    results = TermSerializer(many=True)
    total = serializers.IntegerField(
        help_text="Number of terms matching level/parent/q, before limit and "
        "offset — plus any vector-appended rows, so it never claims fewer "
        "rows than the page shows."
    )
    popular_count = serializers.IntegerField(
        help_text="How many LEADING rows of `results` are in the popular "
        "band. The separator goes after index popular_count - 1; 0 means "
        "this page has no popular band (a page past the boundary, a level "
        "nobody has ranked, or a `q` search whose top hit is a plain "
        "prefix match)."
    )


__all__ = [
    "LevelSerializer",
    "TermPageSerializer",
    "TermSerializer",
    "VocabularySerializer",
]
