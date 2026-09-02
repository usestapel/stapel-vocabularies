"""Loading a vocabulary fixture (spec §3.3, §3.6).

This is data plumbing, not an admin edit: one file is one transaction, one
revision increment and one ``vocabulary.changed`` event, whether it carries
four terms or forty thousand. A per-term save would emit forty thousand
invalidations and issue forty thousand revisions, and every consumer's cache
would spend the import thrashing.

Terms are upserted on **source identity first** — ``(level, external_id)``
when the fixture row carries one, ``(level, code)`` otherwise — in batches;
edges are inserted as a set difference (``--replace`` clears the vocabulary's
edges first). The code is a transliterated slug of the *label*, so a source
catalogue relabelling a term moves the code while the term stays the same
term: keyed on the code a re-import would duplicate it (additive) or delete
and re-insert it (``--replace``), and stored listing values would point at a
row that no longer exists. Nothing here is Django-management-command-shaped so
the same function can be called from a data migration or a test.
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
        if not isinstance(row, list) or not 3 <= len(row) <= 5:
            raise FixtureError(
                f"terms[{index}] must be [level, code, label, external_id?, sort?]"
            )
        if len(row) > 4 and not isinstance(row[4], int):
            raise FixtureError(f"terms[{index}].sort must be an integer rank")
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
    """``(level, code, label, external_id, sort)`` in fixture order.

    ``sort`` prefers the row's own 5th column (the optional rank the fixture
    contract grew in stapel-tools 0.62.1) over the row index. Row ORDER is
    canonical ``(level, code)`` for reviewability, so with no explicit rank
    every picker was code-alphabetical forever — a live stand's RAM dropdown
    opened on «0.1 МБ» with «10 ГБ» before «2 ГБ». Rows without the column
    keep the row-index sort they always had.
    """
    rows = []
    for order, row in enumerate(fixture["terms"]):
        level, code, label = row[0], row[1], row[2]
        external_id = row[3] if len(row) > 3 and row[3] else ""
        sort = row[4] if len(row) > 4 else order
        rows.append((level, code, label, str(external_id), sort))
    return rows


def _match_terms(rows, existing) -> Dict[Tuple[str, str], dict]:
    """Map each fixture row's ``(level, code)`` to the live term it IS.

    Identity precedence, the same rule the category loader uses:

    1. ``(level, external_id)`` when the row carries a source id — the term is
       updated in place, **code included**.
    2. ``(level, code)`` otherwise.

    The code is a transliterated slug of the *label* (``slug.py``), so a source
    catalogue relabelling a term moves its code while the term stays the same
    term. Keyed on the code, a re-import reads that as a new term plus a stale
    one: additively it duplicates the value, and under ``--replace`` it deletes
    the row (taking its edges and its id) and inserts a fresh one. Keyed on the
    source id it is one term whose code moved.
    """
    by_code: Dict[Tuple[str, str], dict] = {}
    by_ext: Dict[Tuple[str, str], dict] = {}
    for row in existing:
        by_code[(row["level"], row["code"])] = row
        if row["external_id"]:
            key = (row["level"], row["external_id"])
            if key in by_ext:
                raise FixtureError(
                    f"two live terms in level {row['level']!r} carry external_id "
                    f"{row['external_id']!r} ({by_ext[key]['code']!r}, "
                    f"{row['code']!r}) — merge or clear them before re-importing"
                )
            by_ext[key] = row

    matched: Dict[Tuple[str, str], dict] = {}
    claimed: Dict[int, Tuple[str, str]] = {}
    for level, code, label, external_id, _sort in rows:
        current = by_ext.get((level, external_id)) if external_id else None
        if current is None:
            current = by_code.get((level, code))
            # A code match whose term belongs to a DIFFERENT source id is left
            # alone: the file is not talking about that term. It falls through
            # as a create, and the unique constraint is freed by the stale
            # delete (--replace) or reported by the additive guard below.
            if (
                current is not None
                and external_id
                and current["external_id"]
                and current["external_id"] != external_id
            ):
                current = None
        if current is None:
            continue
        if current["id"] in claimed:
            other = claimed[current["id"]]
            raise FixtureError(
                f"terms {other[0]}:{other[1]} and {level}:{code} both resolve to "
                f"one live term ({current['code']!r}) — the file gives one term "
                "two rows"
            )
        claimed[current["id"]] = (level, code)
        matched[(level, code)] = current
    return matched


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
    existing = list(
        Term.objects.filter(vocabulary=vocabulary).values(
            "id", "level", "code", "label", "external_id", "sort"
        )
    )
    matched = _match_terms(rows, existing)

    to_create: List[Term] = []
    to_update: List[Term] = []
    renamed: List[Term] = []
    for level, code, label, external_id, sort in rows:
        current = matched.get((level, code))
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
            continue
        if (
            current["code"] != code
            or current["label"] != label
            or current["external_id"] != external_id
            or current["sort"] != sort
        ):
            term = Term(
                id=current["id"],
                vocabulary=vocabulary,
                level=level,
                code=code,
                label=label,
                external_id=external_id,
                sort=sort,
            )
            to_update.append(term)
            if current["code"] != code:
                renamed.append(term)

    claimed = {row["id"] for row in matched.values()}
    stale = [row["id"] for row in existing if row["id"] not in claimed]

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

        # Stale terms go BEFORE the writes below, not after: a renamed term
        # may be moving onto a code a dropped term still holds, and
        # (vocabulary, level, code) is unique. Deleting first frees it.
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
        # Additive load: nothing is deleted, so a term the file does not
        # declare still holds its code, and a write moving onto that code
        # cannot land. Said here rather than left to the IntegrityError, which
        # would name neither term (the loader's existing house rule).
        stale_ids = set(stale)
        held = {
            (row["level"], row["code"])
            for row in existing if row["id"] in stale_ids
        }
        for term in renamed + to_create:
            if (term.level, term.code) in held:
                raise FixtureError(
                    f"term {term.level}:{term.code} would take a code still held "
                    "by a term this file does not declare (external_id "
                    f"{term.external_id!r}) — re-run with --replace, or drop "
                    "that term first"
                )

    if renamed:
        # A rename chain (a→b while b→c) or a swap would break the unique
        # constraint mid-statement, so park every moving term on a code
        # nothing else can hold, then write the final ones.
        Term.objects.bulk_update(
            [Term(id=t.id, code=f"__reimport-{t.id}") for t in renamed],
            ["code"], batch_size=batch,
        )

    if to_update:
        Term.objects.bulk_update(
            to_update, ["code", "label", "external_id", "sort"], batch_size=batch
        )
    if to_create:
        Term.objects.bulk_create(to_create, batch_size=batch)

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
