"""Reference vocabularies: levels + terms + edges (spec §3.3, §3.6, D6).

A vocabulary is a **DAG of levels**, not a tree of paths. ``Vendor -> Model ->
MemorySize -> Color`` in a phone catalogue is 56 921 distinct paths but only
15 844 distinct terms: one ``Color=chernyy`` shared by every model that comes
in black. That is what makes a facet over a colour code answerable at all, and
what makes 17 colours translatable instead of 56 921 path nodes.

``Vocabulary.revision`` (``RevisionMixin``) is the cache key of the whole
thing: HTTP ``ETag``, the resolvers' ``describe`` cache and the
``vocabulary.changed`` event all carry it, and a load bumps it exactly once
per file.
"""
from django.core.exceptions import ValidationError
from django.db import models
from stapel_core.django.models import RevisionMixin

#: Keys a level object may carry. A typo (``"parrent"``) would otherwise be
#: stored silently and read back as a root level.
LEVEL_KEYS = frozenset({"name", "parent"})


def validate_levels(levels):
    """Validate a ``Vocabulary.levels`` value; raise ``ValidationError``.

    The shape is ``[{"name": str, "parent": str|None}, ...]``, root first:

    * names are non-empty, <= 64 chars and unique within the vocabulary;
    * a ``parent`` must name a level declared EARLIER in the list.

    The second rule is the whole acyclicity argument: a level can only point
    backwards, so no chain of parents can return to where it started. There is
    no cycle detector here because there is nothing a cycle could be built out
    of.
    """
    if not isinstance(levels, list):
        raise ValidationError({"levels": "levels must be a list of {name, parent} objects"})
    if not levels:
        raise ValidationError({"levels": "a vocabulary needs at least one level"})

    seen = []
    for index, level in enumerate(levels):
        if not isinstance(level, dict):
            raise ValidationError({"levels": f"levels[{index}] must be an object"})
        unknown = set(level) - LEVEL_KEYS
        if unknown:
            raise ValidationError(
                {"levels": f"levels[{index}] has unknown keys: {sorted(unknown)}"}
            )
        name = level.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValidationError({"levels": f"levels[{index}].name must be a non-empty string"})
        if len(name) > 64:
            raise ValidationError({"levels": f"levels[{index}].name is longer than 64 characters"})
        if name in seen:
            raise ValidationError({"levels": f"duplicate level name {name!r}"})
        parent = level.get("parent")
        if parent is not None:
            if not isinstance(parent, str):
                raise ValidationError({"levels": f"levels[{index}].parent must be a string or null"})
            if parent not in seen:
                raise ValidationError(
                    {
                        "levels": (
                            f"levels[{index}].parent {parent!r} must name a level "
                            "declared before it"
                        )
                    }
                )
        seen.append(name)


class Vocabulary(RevisionMixin):
    """One reference catalogue: its levels, and the revision everything caches by."""

    slug = models.CharField(max_length=64, unique=True, db_index=True)
    name = models.CharField(max_length=200)
    #: ``[{"name": "Vendor"}, {"name": "Model", "parent": "Vendor"}, ...]``
    levels = models.JSONField(default=list)
    #: Where the catalogue came from (a URL, a filename) — provenance, not a fetcher.
    source = models.CharField(max_length=255, blank=True, default="")
    term_count = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name_plural = "vocabularies"
        ordering = ["slug"]
        indexes = [
            models.Index(fields=["revision"], name="voc_vocab_revision_idx"),
        ]

    def __str__(self):
        return f"{self.name} ({self.slug})"

    def clean(self):
        validate_levels(self.levels)

    # --- level helpers (the resolvers and the views read levels through these)

    def level_names(self):
        return [level["name"] for level in self.levels or []]

    def has_level(self, name) -> bool:
        return name in self.level_names()

    def parent_level(self, name):
        """The level ``name`` hangs off, or ``None`` for a root level."""
        for level in self.levels or []:
            if level["name"] == name:
                return level.get("parent")
        return None


class Term(models.Model):
    """One value at one level. Identity is ``(vocabulary, level, code)``."""

    vocabulary = models.ForeignKey(
        Vocabulary, on_delete=models.CASCADE, related_name="terms"
    )
    level = models.CharField(max_length=64)
    #: Transliterated slug of the label (``slug.py``) — the value a listing stores.
    code = models.CharField(max_length=128)
    label = models.CharField(max_length=255)
    #: ``{lang: label}`` overlays; the ``label`` column is the fallback.
    labels = models.JSONField(default=dict, blank=True)
    #: The source catalogue's own id, when it has one. Never the code.
    external_id = models.CharField(max_length=64, blank=True, default="")
    sort = models.IntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["vocabulary", "level", "code"], name="voc_term_unique"
            ),
        ]
        indexes = [
            models.Index(fields=["vocabulary", "level", "label"], name="voc_term_lbl_idx"),
        ]
        ordering = ["sort", "label"]

    def __str__(self):
        return f"{self.level}:{self.code}"

    def label_for(self, language=None) -> str:
        """The label in *language* if this term carries one, else ``label``."""
        if language and isinstance(self.labels, dict):
            translated = self.labels.get(language)
            if translated:
                return translated
        return self.label


class TermEdge(models.Model):
    """A parent/child pair between two terms of adjacent levels."""

    parent = models.ForeignKey(
        Term, on_delete=models.CASCADE, related_name="child_edges"
    )
    child = models.ForeignKey(
        Term, on_delete=models.CASCADE, related_name="parent_edges"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["parent", "child"], name="voc_edge_unique"),
        ]
        indexes = [
            models.Index(fields=["child"], name="voc_edge_child_idx"),
        ]

    def __str__(self):
        return f"{self.parent_id} -> {self.child_id}"


__all__ = ["LEVEL_KEYS", "Term", "TermEdge", "Vocabulary", "validate_levels"]
