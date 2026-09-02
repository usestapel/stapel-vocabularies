# MODULE.md — stapel-vocabularies

Agent-facing map of this module: what it owns, where the seams are, and which
mechanisms already exist so a change does not become a second one.

**Package** `stapel_vocabularies` · **app label** `vocabularies` ·
**URL prefix** `/vocabularies/api/v1/` · **settings namespace**
`STAPEL_VOCABULARIES` (see CONFIG.MD).

---

## 1. What this module owns

A **reference vocabulary**: a catalogue too large to be inlined as feature
options. Three tables and one shape:

```
Vocabulary(RevisionMixin)  slug (unique, 64), name (200), levels JSON, source (255), term_count
Term                       vocabulary FK, level (64), code (128), label (255),
                           labels JSON {lang: str}, external_id (64), sort
                           unique (vocabulary, level, code); index (vocabulary, level, label)
TermEdge                   parent FK Term, child FK Term
                           unique (parent, child); index (child)
```

`levels` is `[{"name": ..., "parent": ...|absent}]`, root first. **A level's
parent must be declared before it.** That one rule is the whole acyclicity
argument — a level can only point backwards, so no chain of parents can return
to where it started. `validate_levels()` in `models.py` is the only place it
lives; `Vocabulary.clean()` and the loader both call it.

**Levels + terms + edges, not paths.** A real phone catalogue is 56 921
distinct root-to-leaf paths and 15 844 distinct terms. Storing terms and edges
is what makes `Color=chernyy` one row shared by every model that comes in
black — 17 colours to translate rather than 56 921 path nodes, and a facet on
a colour code that is answerable at all.

`Vocabulary.revision` is the cache key of the whole thing: the HTTP `ETag`,
both resolvers' `describe` cache, and the `vocabulary.changed` payload all
carry it. A load bumps it exactly once per file.

## 2. What it does NOT own

- **Feature definitions.** A feature's `optionsRef` names a vocabulary slug;
  that is the entire coupling. This module imports neither stapel-categories
  nor its models, and is not imported by them.
- **Fetching catalogues.** `Vocabulary.source` is provenance, not a
  downloader. Nothing here makes a network call.
- **Vendor-specific parsing.** `convert.py` reads two generic shapes;
  anything shaped like one particular vendor's export (its modification
  attributes, say) lives in the catalogue importer in stapel-tools.
- **Writes over HTTP.** The read surface has no writer. Loading is
  `manage.py load_vocabulary`, an operator action against a reviewed file.

## 3. Extension points

| Seam | Kind | Where | What it changes |
|---|---|---|---|
| `STAPEL_VOCABULARIES["REGISTER_RESOLVER"]` | boolean axis | `apps.py` | whether this process answers vocabulary questions in-process (`OrmResolver`) or not at all (the host points stapel-attributes at `CommResolver`) |
| `STAPEL_ATTRIBUTES["VOCABULARY_RESOLVER"]` | dotted path (stapel-attributes' seam) | host settings | where a service WITHOUT the tables resolves: `stapel_vocabularies.resolver.CommResolver` |
| `STAPEL_VOCABULARIES["QUERY_EXPANDER"]` | dotted path | `conf.py` / `expand.py` | the match variants a term search ORs together — `(query, language) -> Sequence[str]`, literal query included. Default: this module's identity expansion; a fleet running stapel-search points it at `stapel_search.suggest.query_terms`, the fleet's ONE cross-script normalization layer |
| `response_serializer_class` on each view | class override | `views.py` | the shape of a read response — subclass the view, set the attribute, remount the URL (`SerializerSeamMixin`) |
| `vocabulary.changed` | comm event | `events.py` | how a consumer learns a catalogue was re-imported |
| `vocabularies.resolve` / `vocabularies.describe` | comm functions | `functions.py` | how a service without the tables asks |

There is deliberately **no** parser registry and **no** pluggable code
generator. A term code is a persisted listing value: making the slugger
swappable would make the same catalogue produce different stored data in two
deployments.

## 4. The read surface

`ReadOnlyOrStaff`; there is no writer, so a staff POST is a 405 rather than a
permission question.

| Method | Path | Parameters | Answer |
|---|---|---|---|
| GET | `vocabularies/` | — | `[{slug, name, levels, term_count, revision}]` |
| GET | `vocabularies/{slug}/` | — | the same, one |
| GET | `vocabularies/{slug}/terms/` | `level` (required), `parent`, `q`, `limit` ≤200 (default 50), `offset` | `{results: [{code, label, level, has_children}], total}` |
| GET | `vocabularies/{slug}/terms/resolve/` | `level` (required), `codes` (csv, ≤200) | `{code: label}`, unknown codes omitted |

Behaviour worth knowing before changing it:

- **`q` ranks prefix matches first**, then `sort, label`. That ordering is
  what a typeahead is; a plain `icontains` order puts "Ultra Pro Max" above
  "Pro 001" for the query `pro`.
- **`parent` is a code at the level ABOVE `level`**, resolved through
  `TermEdge`. A code that names no such term — including any code at all when
  `level` is a root level — is `bad_parent` (400), not an empty page: an empty
  page would look like "this vendor has no models".
- **`total`** counts the whole filtered set, before `limit`/`offset`.
- **`ETag`** is a digest of the vocabulary's revision plus every parameter
  that shapes the body, the negotiated languages included; a matching
  `If-None-Match` answers 304 without building the body. `Cache-Control:
  public, max-age=CACHE_MAX_AGE`, `Vary: Accept-Language`.
- **No `Set-Cookie` on an anonymous read.** Pinned by
  `tests/test_public_read.py`: a cookie makes the whole surface uncacheable at
  the edge and starts a session per crawler.
- **`Accept-Language`** picks `Term.labels[lang]`, falling back to
  `Term.label`. Tags are tried best-quality-first, with the base tag after the
  full one (`en-GB` then `en`).

Errors: `vocabularies_vocabulary_not_found` (404),
`vocabularies_level_not_found` (404 — also the answer to a *missing* `level`,
because "the level named nothing" is not a distinction a client can act on
differently), `vocabularies_bad_parent` (400). All in `errors.py` and
`docs/errors.json`.

## 5. Resolvers

`resolver.py` implements stapel-attributes' `VocabularyResolver` twice:

- **`OrmResolver`** — same process as the tables. Registered by
  `AppConfig.ready()` under `REGISTER_RESOLVER`.
- **`CommResolver`** — over `stapel_core.comm`, for a service that validates
  listings but holds no catalogues.

Both cache `describe` **by revision**, never by clock alone: the level list is
read on every ref-typed config validation, and a re-imported catalogue must
stop validating against the levels it used to have the moment the import
commits. The ORM side revalidates with one `values_list("revision")`; the comm
side gets the revision in the `describe` answer (an additive field, see §8)
and subscribes to `vocabulary.changed` to drop its entry.

stapel-attributes is imported **lazily**, inside the methods that need its
dataclasses. Importing this module therefore costs nothing and does not turn a
dependency-floor violation into an ImportError at Django startup — system
check `stapel_vocabularies.W001` reports that deployment instead, naming the
release to install.

## 6. Loading

```
manage.py load_vocabulary <file.json> [...] [--replace] [--batch-size N]
manage.py convert_vocabulary <catalogue> --slug S --out F [--format nested-xml|csv] ...
```

`loader.load_fixture` is the callable; the command is a thin shell so a data
migration can use it directly.

**One file is one transaction, one revision increment and one
`vocabulary.changed` event** — whatever its size. A loop over
`Term.objects.create()` would spend 15 000 revisions and emit 15 000
invalidations for one catalogue. The single increment is arranged carefully:
for a NEW vocabulary the `create()` is that increment and the closing
`save(update_fields=...)` deliberately omits `"revision"`; for an existing one
the closing save is the increment.

- Terms are upserted on **source identity first**: `(level, external_id)` when
  the fixture row carries one, `(level, code)` otherwise. `bulk_create` for the
  new, `bulk_update` for the changed, nothing touched for the identical.
  The code is a slug of the *label*, so a source catalogue relabelling a term
  moves its code while the term stays the same term — keyed on the code, a
  re-import reads that as a new term plus a stale one (additively a duplicate
  value; under `--replace` the row is deleted, taking its id and its edges,
  and a fresh one inserted). Keyed on the source id it is one term whose code
  moved, and stored listing values keep pointing at a live row.
- Because a code can move, the stale delete under `--replace` runs **before**
  the writes (a rename may be taking a dropped term's code), and a chain or a
  swap of codes is parked on `__reimport-<id>` for one statement rather than
  breaking `unique (vocabulary, level, code)` mid-`bulk_update`. What cannot
  be arranged is named, not left to an `IntegrityError`: an additive load whose
  rename needs a code the file does not declare, two live terms carrying one
  `external_id`, and one file giving one term two rows.
- `--replace` makes the file authoritative: the vocabulary's whole edge set is
  rebuilt and terms the file does not mention are deleted. Without it the load
  is additive, which is what a second catalogue contributing to one vocabulary
  needs.
- `validate_fixture` refuses a duplicate `(level, code)` by name rather than
  letting the unique constraint raise an `IntegrityError` 12 000 rows into a
  bulk insert.

Measured: the real `phone_catalog.xml` (15 844 terms, 39 749 edges) converts
in 0.5 s and loads in 1.2 s; the synthetic 15 806-term / 150 260-edge fixture
in `tests/test_performance.py` loads in ~3 s against the 60 s budget.

## 7. Converters and codes

`convert.py` is **Django-free** — the catalogue importer in stapel-tools
calls it directly.

- `nested_xml_to_fixture` drives `iterparse` and removes every finished
  element from its parent, so a 40 MB catalogue is walked in constant document
  memory. What is held is the *result* (distinct terms and edges), which is
  what the fixture is. `tests/test_convert.py` measures this rather than
  asserting it in a comment.
- Levels auto-detect in document order, or are named explicitly — naming a
  subset collapses the levels left out transitively, which is what the
  importer's inline threshold needs.
- A level nested under two different parents is refused: that is not a DAG of
  levels.
- `csv_to_fixture` takes one column per level; an empty cell truncates that
  row's path rather than inventing an empty-labelled term.

`slug.py` derives the code. **The table is frozen** — a code is stored inside
saved listings, so changing how one is derived renames data.

- Cyrillic transliterates (`django.utils.text.slugify` drops it entirely,
  which would collapse every Russian colour onto the empty string);
- `[a-z0-9-]`, ≤128 chars, digest-suffixed when truncated;
- a label with nothing sluggable gets `t-<8 hex>` rather than an empty code;
- duplicates are numbered `-2`, `-3` **in label sort order**, and the suffix is
  chosen against the codes already assigned in that level rather than a
  per-base counter. The real phone catalogue contains the collision that
  distinction exists for (`iPhone 10` / `iPhone-10` / `iPhone 10 2`).

## 8. Deviations from the spec (§3.3, §3.6)

- `vocabularies.describe` answers `{slug, levels, revision}` — the spec wrote
  `{slug, levels}`. The extra field is additive and load-bearing: without it
  `CommResolver` could only expire a `describe` on a timer, and the spec also
  requires both resolvers to cache **by revision**.
- `terms/resolve/` silently uses the first 200 codes named rather than
  rejecting a longer list. The spec caps the parameter but registers no error
  key for exceeding it, and inventing a fourth key for a client that asked for
  too much at once is worse than answering the 200 it is allowed.
- A **missing** `level` answers `level_not_found` (404) rather than a distinct
  400, for the same reason: the spec's error surface is three keys and this
  case is one of them read literally.

## 9. Where things live

```
models.py        Vocabulary / Term / TermEdge, validate_levels
views.py         four reads, ETag/Cache-Control, Accept-Language
urls.py/_v1.py   /vocabularies/api/v1/, GATE_REGISTRY
serializers.py   response shapes (SerializerSeamMixin seams)
errors.py        the three i18n keys
functions.py     vocabularies.resolve / vocabularies.describe (+ schemas/functions/)
events.py        vocabulary.changed (+ schemas/emits/)
resolver.py      OrmResolver, CommResolver, register_orm_resolver
loader.py        load_fixture / load_files / validate_fixture
convert.py       nested_xml_to_fixture, csv_to_fixture, dump_fixture   (Django-free)
slug.py          slugify_term, dedupe_codes                            (Django-free)
checks.py        W001 (no protocol), W002 (tables here, resolver off), W003 (expander)
conf.py          STAPEL_VOCABULARIES + flag()/number() env coercion, query_expander()
expand.py        literal — the default (identity) query expander      (Django-free)
docs/            contract artifacts + vocabulary-fixture.schema.json
```
