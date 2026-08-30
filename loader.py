"""Loading a vocabulary fixture (spec §3.3, §3.6).

This is data plumbing, not an admin edit: one file is one transaction, one
revision increment and one ``vocabulary.changed`` event, whether it carries
four terms or forty thousand. A per-term save would emit forty thousand
invalidations and issue forty thousand revisions, and every consumer's cache
would spend the import thrashing.

Terms are upserted on ``(vocabulary, level, code)`` in batches; edges are
inserted as a set difference (``--replace`` clears the vocabulary's edges
first). Nothing here is Django-management-command-shaped so the same function
can be called from a data migration or a test.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction

from .conf import number
from .events import publish_vocabulary_changed
from .models import Term, TermEdge, Vocabulary, validate_levels


class FixtureError(ValueError):
    """The fixture does not have the shape ``docs/vocabulary-fixture.schema.json`` describes."""


@dataclass
class LoadResult:
    """What one loaded file did."""

    slug: str
    revision: int
    created: bool
    terms_created: int
    terms_updated: int
    terms_deleted: int
    edges_created: int
    edges_deleted: int

    @property
    def term_count(self) -> int:
        return self.terms_created + self.terms_updated


def validate_fixture(fixture) -> None:
    """Structural validation of a parsed fixture.

    Deliberately not a jsonschema call: ``jsonschema`` is a test dependency,
    not a runtime one, and a loader that only validates where the library
    happens to be installed validates nowhere that matters. The committed
    schema stays the contract for the importer that WRITES fixtures; this is
    the reader's own gate, and ``tests/test_fixture_schema.py`` pins the two
    against each other.
    """
    if not isinstance(fixture, dict):
        raise FixtureError("fixture must be a JSON object")
    for key in ("slug", "name", "levels", "terms"):
        if key not in fixture:
            raise FixtureError(f"fixture is missing {key!r}")
    if not isinstance(fixture["slug"], str) or not fixture["slug"]:
        raise FixtureError("slug must be a non-empty string")
    if not isinstance(fixture["name"], str):
        raise FixtureError("name must be a string")
    try:
        validate_levels(fixture["levels"])
    except DjangoValidationError as exc:
        # One exception type leaves this module: a caller (the management
        # command, the importer) should not have to know that the level rule
        # happens to be shared with the model's clean().
        raise FixtureError(f"invalid levels: {exc.messages[0]}") from None

    level_names = {level["name"] for level in fixture["levels"]}
    declared: set = set()
    for index, row in enumerate(fixture["terms"]):
        if not isinstance(row, list) or not 3 <= len(row) <= 4:
            raise FixtureError(
                f"terms[{index}] must be [level, code, label, external_id?]"
            )
        level, code, label = row[0], row[1], row[2]
        if level not in level_names:
            raise FixtureError(f"terms[{index}] names unknown level {level!r}")
        if not isinstance(code, str) or not code:
            raise FixtureError(f"terms[{index}].code must be a non-empty string")
        if not isinstance(label, str):
            raise FixtureError(f"terms[{index}].label must be a string")
        # Said here rather than left to the unique constraint: a converter bug
        # that hands two labels the same code surfaces as an IntegrityError
        # 12 000 rows into a bulk_create, naming neither of them.
        if (level, code) in declared:
            raise FixtureError(f"terms[{index}] declares {level}:{code} twice")
        declared.add((level, code))

    for index, row in enumerate(fixture.get("edges") or []):
        if not isinstance(row, list) or len(row) != 4:
            raise FixtureError(
                f"edges[{index}] must be [parent_level, parent_code, child_level, child_code]"
            )
        for name in (row[0], row[2]):
            if name not in level_names:
                raise FixtureError(f"edges[{index}] names unknown level {name!r}")


def _term_rows(fixture) -> List[Tuple[str, str, str, str, int]]:
    """``(level, code, label, external_id, sort)`` in fixture order."""
    rows = []
    for order, row in enumerate(fixture["terms"]):
        level, code, label = row[0], row[1], row[2]
        external_id = row[3] if len(row) > 3 and row[3] else ""
        rows.append((level, code, label, str(external_id), order))
    return rows


@transaction.atomic
def load_fixture(fixture, replace: bool = False, batch_size: Optional[int] = None) -> LoadResult:
    """Apply one parsed fixture. One transaction, one revision, one event.

    ``replace=True`` makes the file authoritative: terms it does not mention
    are deleted (their edges go with them) and the vocabulary's whole edge set
    is rebuilt. Without it the load is additive — the shape a second catalogue
    contributing to the same vocabulary needs.
    """
    validate_fixture(fixture)
    batch = batch_size or number("LOAD_BATCH_SIZE")
    slug = fixture["slug"]

    vocabulary = Vocabulary.objects.filter(slug=slug).first()
    created = vocabulary is None
    if created:
        # The create IS this file's single revision increment; the closing
        # save() below is then scoped with update_fields and does not bump.
        vocabulary = Vocabulary.objects.create(
            slug=slug,
            name=fixture["name"],
            levels=fixture["levels"],
            source=fixture.get("source", "") or "",
        )
    else:
        vocabulary.name = fixture["name"]
        vocabulary.levels = fixture["levels"]
        vocabulary.source = fixture.get("source", "") or ""

    rows = _term_rows(fixture)
    existing: Dict[Tuple[str, str], dict] = {
        (row["level"], row["code"]): row
        for row in Term.objects.filter(vocabulary=vocabulary).values(
            "id", "level", "code", "label", "external_id", "sort"
        )
    }

    to_create: List[Term] = []
    to_update: List[Term] = []
    seen: set = set()
    for level, code, label, external_id, sort in rows:
        key = (level, code)
        seen.add(key)
        current = existing.get(key)
        if current is None:
            to_create.append(
                Term(
                    vocabulary=vocabulary,
                    level=level,
                    code=code,
                    label=label,
                    external_id=external_id,
                    sort=sort,
                )
            )
        elif (
            current["label"] != label
            or current["external_id"] != external_id
            or current["sort"] != sort
        ):
            to_update.append(
                Term(
                    id=current["id"],
                    vocabulary=vocabulary,
                    level=level,
                    code=code,
                    label=label,
                    external_id=external_id,
                    sort=sort,
                )
            )

    if to_create:
        Term.objects.bulk_create(to_create, batch_size=batch)
    if to_update:
        Term.objects.bulk_update(
            to_update, ["label", "external_id", "sort"], batch_size=batch
        )

    terms_deleted = 0
    edges_deleted = 0
    if replace:
        # Edges first, and the WHOLE set rather than a diff: a re-imported
        # catalogue that dropped a branch must stop offering it, and "which
        # edges disappeared" is not answerable from the file alone. Doing it
        # before the stale terms also keeps the two counts honest — otherwise
        # the terms' cascade would silently take the edges and report zero.
        edges_deleted = (
            TermEdge.objects.filter(parent__vocabulary=vocabulary)
            .delete()[1]
            .get("vocabularies.TermEdge", 0)
        )
        present: set = set()

        stale = [row["id"] for key, row in existing.items() if key not in seen]
        for start in range(0, len(stale), batch):
            _, per_model = Term.objects.filter(
                id__in=stale[start:start + batch]
            ).delete()
            terms_deleted += per_model.get("vocabularies.Term", 0)
    else:
        present = set(
            TermEdge.objects.filter(parent__vocabulary=vocabulary).values_list(
                "parent_id", "child_id"
            )
        )

    ids: Dict[Tuple[str, str], int] = {
        (level, code): pk
        for pk, level, code in Term.objects.filter(vocabulary=vocabulary).values_list(
            "id", "level", "code"
        )
    }

    wanted: List[TermEdge] = []
    for parent_level, parent_code, child_level, child_code in fixture.get("edges") or []:
        parent_id = ids.get((parent_level, parent_code))
        child_id = ids.get((child_level, child_code))
        if parent_id is None or child_id is None:
            raise FixtureError(
                f"edge {parent_level}:{parent_code} -> {child_level}:{child_code} "
                "refers to a term the fixture does not declare"
            )
        if (parent_id, child_id) in present:
            continue
        present.add((parent_id, child_id))
        wanted.append(TermEdge(parent_id=parent_id, child_id=child_id))

    if wanted:
        TermEdge.objects.bulk_create(wanted, batch_size=batch)

    vocabulary.term_count = len(ids)
    fields = ["name", "levels", "source", "term_count"]
    if not created:
        # The one increment for this file. A fresh vocabulary already spent it
        # on the create above, so its closing save stays scoped and quiet.
        fields.append("revision")
    vocabulary.save(update_fields=fields)
    vocabulary.refresh_from_db(fields=["revision"])

    publish_vocabulary_changed(vocabulary.slug, vocabulary.revision)

    return LoadResult(
        slug=slug,
        revision=vocabulary.revision,
        created=created,
        terms_created=len(to_create),
        terms_updated=len(to_update),
        terms_deleted=terms_deleted,
        edges_created=len(wanted),
        edges_deleted=edges_deleted,
    )


def load_files(paths: Iterable, replace: bool = False, batch_size: Optional[int] = None):
    """``load_fixture`` per file — each in its own transaction."""
    import json
    from pathlib import Path

    results = []
    for path in paths:
        fixture = json.loads(Path(path).read_text(encoding="utf-8"))
        results.append(load_fixture(fixture, replace=replace, batch_size=batch_size))
    return results


__all__ = [
    "FixtureError",
    "LoadResult",
    "load_files",
    "load_fixture",
    "validate_fixture",
]
