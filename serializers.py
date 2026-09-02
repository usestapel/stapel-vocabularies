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


__all__ = [
    "LevelSerializer",
    "TermPageSerializer",
    "TermSerializer",
    "VocabularySerializer",
]
