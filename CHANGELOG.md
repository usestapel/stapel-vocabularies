# Changelog

All notable changes to stapel-vocabularies are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: pre-1.0 semver — **minor = breaking**, patch = additive/fixes.

## [0.1.5] — 2026-09-02

### Fixed — every picker was code-alphabetical, and nothing could fix it

`Term.sort` was assigned from fixture ROW order, and the fixture contract
canonicalizes row order as `(level index, code)` for reviewability
(stapel-tools VOC004) — one channel serving two rules, so a live stand's
RAM dropdown opened on «0.1 МБ» with «10 ГБ» before «2 ГБ», and an
importer that reordered rows to fix it broke the review gate. The term
row's optional 5th column (stapel-tools 0.62.1) now carries an explicit
integer rank; `_term_rows` prefers it over the row index. A 4-column
fixture loads byte-for-byte as before; a non-integer rank is refused by
name (`FixtureError`), not 26 000 rows in.

## [0.1.4] — 2026-09-02

Patch. Additive: the vector net under the term typeahead, off by default —
with `VECTOR_SIMILAR_FUNCTION` empty (the default) every answer and every
ETag is byte-identical to 0.1.3. No model, migration or fixture change.

### Added — «тимбирленд» finds Timberland when the deployment can afford it

The QUERY_EXPANDER seam (0.1.3) gave the term search the fleet's
deterministic normalization; a *phonetic* misspelling is the class no
deterministic table catches. Where the fleet runs stapel-search's vector
layer, this module can now stand on it, through the same
seam-by-comm-name discipline:

- **`vector.similar_labels`** — the consumer: when the first page of a
  `?q=` answer is thinner than `VECTOR_MIN_RESULTS`, the raw query goes to
  `VECTOR_SIMILAR_FUNCTION` (`search.similar`) and the labels an embedding
  space places next to it come back, floor already applied on the far
  side. Matching terms of the SAME vocabulary, level and parent scope are
  appended below the deterministic rows, graded `match: "vector"` (a new
  optional response field, absent on literal rows); `total` counts them.
  Every failure — provider down, layer disabled, comm error — costs
  recall, never the response.
- **`vector.label_corpus`** — the provider: the distinct labels of the
  levels matching `VECTOR_LABEL_LEVELS` (glob patterns — `brand*`,
  `marka*`, `Vendor`), for registration in
  `STAPEL_SEARCH["VECTOR_CORPORA"]` under `VECTOR_KIND`
  (`vocab_label`). Deliberately scoped: the typo problem lives in the
  levels people type toward, not in 800k catalogue rows nobody types.

## [0.1.3] — 2026-09-02

Patch. Additive: a new seam with a default that reproduces today's behavior
byte-for-byte. No model, migration or fixture-format change.

### Added

- **The term search matches variants, through the fleet's normalization
  layer — `QUERY_EXPANDER`.** On a classified stand, a buyer typing
  «тимберленд» into the composer's brand picker never saw the term
  "Timberland", and «айфон» never found "iPhone": the search matched one
  literal substring in one script, while the fleet's search library already
  owned the cross-script layer — folding, RU↔EN transliteration, curated
  alias groups — and used it for its own suggestions. Two surfaces, one
  vocabulary, two ideas of what a query means.

  The rule is ONE normalization layer, so this module grows no second copy
  and takes no dependency on the search library. Instead the term search
  now sends `?q=` through a configured callable
  `(query: str, language: str) -> Sequence[str]`
  (`STAPEL_VOCABULARIES["QUERY_EXPANDER"]`, an `import_strings` key) and
  ORs `label__icontains` across every variant it returns; a label starting
  with **any** variant ranks before a mid-label hit, then the level's own
  sort order and label, as before. `language` is the same negotiated value
  the labels resolve for — the best `Accept-Language` tag. A composite
  running stapel-search points the seam at
  `stapel_search.suggest.query_terms`; standalone, the default
  (`stapel_vocabularies.expand.literal`) expands to the literal query
  alone, which is exactly the old behavior.

  A picker runs behind somebody's keystrokes, so a dotted path that does
  not import — or an expander that raises — costs recall, never the
  response: the search logs and matches the literal query, and new system
  check `stapel_vocabularies.W003` names the misconfiguration at boot.

## [0.1.2] — 2026-09-01

Patch. Documentation and example data only — no model, migration, API,
converter or fixture-format change.

### Changed

- **Docs and examples are source-neutral.** README, MODULE.md, the fixture
  schema description, `convert_vocabulary`'s docstring and the `functions.py`
  comm examples named the external marketplace whose phone catalogue was the
  worked example. The worked example is now a generic `phone-models`
  vocabulary; the measurements it quotes (56 921 paths / 15 844 terms /
  39 749 edges) are unchanged, because they are what makes the terms-and-edges
  argument. `tests/test_convert.py` uses the same slug.
- Code assignment stays as it was, transliteration included: `Color=chernyy`
  is this module's own deterministic slug of a Cyrillic label, and the tests
  that pin it are the transliteration contract, not imported data.

## [0.1.1] — 2026-08-31

### Fixed

- **A re-imported vocabulary keys on source identity, not on the code.**
  `load_fixture` now matches a fixture row against the live term with the same
  `(level, external_id)` first, and falls back to `(level, code)` only for
  rows carrying no source id.

  The code is a transliterated slug of the **label**, so a source catalogue
  relabelling a term moves its code while the term stays the same term. Keyed
  on the code, a re-import read that as a new term plus a stale one: an
  additive load duplicated the value, and `--replace` deleted the row —
  taking its id and its edges — and inserted a fresh one, so every listing
  holding the old code pointed at nothing. Keyed on the source id it is one
  term whose code moved.

  Three consequences, each of them the point rather than a side effect:

  - Under `--replace` the stale delete now runs **before** the term writes: a
    rename may be moving onto a code a dropped term still holds, and
    `unique (vocabulary, level, code)` would refuse it.
  - A chain (`a→b` while `b→c`) or a swap of codes is parked on
    `__reimport-<id>` for one statement, so it never breaks the unique
    constraint mid-`bulk_update`.
  - What cannot be arranged is named instead of surfacing as an
    `IntegrityError` that identifies neither row — the loader's existing house
    rule: an additive load whose write needs a code the file does not declare,
    two live terms carrying one `external_id` in a level, and one file
    resolving two rows onto one term.

  `bulk_update` now writes `code` alongside `label`, `external_id` and `sort`.
  No schema change, no migration: `Term.external_id` already existed and the
  `(vocabulary, level, code)` constraint is untouched.

## [0.1.0] — 2026-08-30

First release. Slice S2 of the attributes-v2 wave (spec §3.3, §3.6): the
reference-vocabulary store behind stapel-attributes' `ref_select` and
`ref_hierarchical_select` types.

### Added

- **Models** — `Vocabulary` (`RevisionMixin`: slug, name, `levels` JSON,
  source, term_count), `Term` (unique on `(vocabulary, level, code)`, indexed
  on `(vocabulary, level, label)`, per-language `labels` overlay,
  `external_id`, `sort`) and `TermEdge` (unique on `(parent, child)`, indexed
  on `child`). Migration `0001_initial`.

  Levels are validated as a list of `{name, parent}` with unique names where a
  parent must be declared **before** the level that names it — the one rule
  that makes the level graph acyclic by construction rather than by a cycle
  detector.

  Terms and edges, not paths: a real phone catalogue is 56 921 distinct
  root-to-leaf paths and 15 844 distinct terms, so one `Color=chernyy` is
  shared by every model that comes in black.

- **Read surface** at `/vocabularies/api/v1/` (`ReadOnlyOrStaff`, anonymous,
  no `Set-Cookie`): the vocabulary catalogue, one vocabulary,
  `terms/` (`level` required, `parent` filtering through `TermEdge`, `q`
  `icontains` with prefix matches ranked first, `limit` ≤200 default 50,
  `offset`, `has_children`, `total`) and `terms/resolve/` (codes → labels,
  unknown omitted). `ETag` + `Cache-Control: public, max-age=…` derived from
  `Vocabulary.revision`; `Vary: Accept-Language`; a matching `If-None-Match`
  answers 304 without building the body. `Accept-Language` selects
  `Term.labels[lang]`.

  Errors: `vocabularies_vocabulary_not_found` (404),
  `vocabularies_level_not_found` (404), `vocabularies_bad_parent` (400).

- **comm** — Functions `vocabularies.resolve` (existence, labels and optional
  parentage for a batch of codes in ONE round trip, because validating a
  submitted listing otherwise costs three) and `vocabularies.describe`
  (`{slug, levels, revision}`); event `vocabulary.changed {slug, revision}`,
  emitted once per loaded file inside the load's transaction. JSON schemas in
  `schemas/functions/` and `schemas/emits/`.

- **Resolvers** — `resolver.OrmResolver` (registered by `AppConfig.ready()`
  under `STAPEL_VOCABULARIES["REGISTER_RESOLVER"]`, default on) and
  `resolver.CommResolver` (for a service with no vocabulary tables; the host
  puts its dotted path in `STAPEL_ATTRIBUTES["VOCABULARY_RESOLVER"]`). Both
  cache `describe` **by revision**, so a re-imported catalogue stops
  validating against the levels it used to have the moment the import commits;
  the comm side also subscribes to `vocabulary.changed`. stapel-attributes is
  imported lazily, so a floor violation is a system check and not an
  ImportError at startup.

- **`manage.py load_vocabulary <file.json> [--replace]`** — batched
  `bulk_create`/`bulk_update` upsert on `(vocabulary, level, code)`, one
  transaction, **one** revision increment and **one** `vocabulary.changed`
  event per file. `--replace` makes the file authoritative (edge set rebuilt,
  unmentioned terms deleted); without it the load is additive.

- **`manage.py convert_vocabulary`** over the Django-free `convert.py`:
  `nested_xml_to_fixture` (streams `iterparse` and drops every finished
  element, so a 40 MB catalogue is walked in constant document memory) and
  `csv_to_fixture`. `slug.py` derives a term code — Cyrillic transliterated,
  `[a-z0-9-]`, ≤128, duplicates numbered in label sort order.

- **`docs/vocabulary-fixture.schema.json`** — the fixture contract the
  importer (spec §4) writes to; `tests/test_convert.py` validates converter
  output against it.

- **System checks** — `W001` (the installed stapel-attributes has no resolver
  protocol, so every ref-typed feature will refuse to save while the term
  endpoints keep answering) and `W002` (the process that holds the tables is
  the one that declined to answer).

### Measured

- `phone_catalog.xml` (7.0 MB, Vendor 529 → Model 14 962 → MemorySize 260 →
  Color 17 → RamSize 76): **convert 0.5 s** → 15 844 terms, 39 749 edges,
  a 2.7 MB fixture, peak RSS 58 MB; **load 1.2 s** on SQLite; a second
  `--replace` load changes zero terms.

  The 39 749 is *distinct edges*; the spec's "160 k" counted path segments.
  Collapsing them is the point of the DAG.

- Synthetic phone-sized fixture (15 806 terms, 150 260 edges) loads in ~3 s
  against the spec's 60 s budget — `tests/test_performance.py`.

### Notes

- `vocabularies.describe` answers `{slug, levels, revision}`; the spec wrote
  `{slug, levels}`. Additive and load-bearing: without the revision a remote
  resolver could only expire its cache on a timer, and the spec requires
  caching by revision.
- `terms/resolve/` uses the first 200 codes named rather than erroring; the
  spec caps the parameter but registers no error key for exceeding it.
- A missing `level` answers `level_not_found` rather than a fourth key.
- `dedupe_codes` picks a suffix against the codes already assigned in the
  level, not a per-base counter. The real phone catalogue contains the
  collision that distinction exists for (`iPhone 10` / `iPhone-10` /
  `iPhone 10 2`), and a per-base counter produces a duplicate that only
  surfaces as an `IntegrityError` 12 000 rows into a bulk insert.

[0.1.0]: https://github.com/usestapel/stapel-vocabularies/releases/tag/v0.1.0
