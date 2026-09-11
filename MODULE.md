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
                           labels JSON {lang: str}, external_id (64), sort, popularity,
                           extra JSON {source-owned attrs}
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

**`sort` and `popularity` are two rules, not one column with a mood.** `sort`
is the curated rank WITHIN a band (the fixture row's 5th column, 0.1.5).
`popularity` is which band and where in it: > 0 = the short recommended band a
level opens on, highest first; 0 = the alphabet under it. A dictionary sorted
purely by name opens on whatever the alphabet put first — a live stand's
529-vendor level opens on «3Q, 4Good, 8848, A1, Aceline» while the two brands
carrying the volume sit hundreds of rows down — and every market leader
answers that with a band. 0.1.5 is the release that learned what happens when
one column carries two rules; `popularity` exists so that lesson is not
re-learned one field over.

`Vocabulary.revision` is the cache key of the whole thing: the HTTP `ETag`,
both resolvers' `describe` cache, and the `vocabulary.changed` payload all
carry it. A load bumps it exactly once per file, and so does a
`set_popularity` push that actually moved something.

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
| `vocabularies.resolve` / `vocabularies.describe` | comm functions | `functions.py` | how a service without the tables asks about codes it already has |
| `vocabularies.match` | comm function | `functions.py` | how a caller with no code at all resolves one free-text guess — scored, thresholded, refusable |
| `vocabularies.children` | comm function | `functions.py` | how a caller with no code and no person to show a list to asks what the choices under one term ARE — a page, with `truncated`, so a caller reasoning about the set cannot mistake a cut-short page for a complete one |
| `vocabularies.terms` | comm function | `functions.py` / `resolver.py` | how a caller that RENDERS a level gets it whole — `(code, label)` pairs in the level's own order, capped at `TERMS_LIMIT`. What `CommResolver.terms` is, and therefore what a category expanded by a `ref_select` feature draws its children from. Its `extras` key (`{code: {...}}`, only the terms that have one) carries the source catalogue's own per-term attributes — the swatch behind a colour facet |
| `Term.extra` / `terms_with_extra()` | JSON column + resolver method | `models.py` / `resolver.py` / the fixture's 7th column | what a renderer knows about a term beyond its label — `{"hue": "#1a1a1a"}` on a colour. Owned by the SOURCE catalogue, merged key by key on load, read by nothing in this module |
| `vocabularies.set_popularity` | comm function | `functions.py` / `ranking.py` | how the host that owns the listings pushes the observed counts the popular band is built from |
| `STAPEL_VOCABULARIES["POPULAR_BAND_SIZE"]` | integer | `conf.py` | how many terms of a level may sit in the popular band, on the write side and on the wire |
| `STAPEL_VOCABULARIES["MATCH_MIN_SCORE"]` | float | `conf.py` | the floor `vocabularies.match` refuses below (default 0.8) |
| `STAPEL_VOCABULARIES["TERMS_LIMIT"]` | integer | `conf.py` | how many terms of one level the browse read (`terms()` / `vocabularies.terms`) may hand back at once (default 2000) — a level bigger than this is not a browse level |

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
| GET | `vocabularies/{slug}/terms/` | `level` (required), `parent`, `q`, `limit` ≤200 (default 50), `offset` | `{results: [{code, label, level, has_children, band, extra?}], total, popular_count}` |
| GET | `vocabularies/{slug}/terms/resolve/` | `level` (required), `codes` (csv, ≤200) | `{code: label}`, unknown codes omitted |

Behaviour worth knowing before changing it:

- **Ordering is `prefix_rank?, popular_band, -popularity, sort, label`.**
  `q` ranks prefix matches first and stays the OUTERMOST key — that ordering
  is what a typeahead is, and a plain `icontains` order puts "Ultra Pro Max"
  above "Pro 001" for the query `pro`. Under it comes the popular band, and
  under that the level's own curated rank and its alphabet. With nothing
  promoted the whole expression collapses to the historical `sort, label`.
- **The band boundary is explicit on the wire.** Every row carries `band`
  (`"popular"` / `"all"`) and the page carries `popular_count`, the number of
  LEADING rows in the band — the separator goes after index
  `popular_count - 1`, and `0` means this page has no band (past the
  boundary, nothing promoted, or a `q` search whose top hit is a plain prefix
  match). A frontend never scans for the change. Vector-appended rows are
  always `"all"`: a recommended band that appears only when the literal
  search failed is not a recommended band.
- **`POPULAR_BAND_SIZE` (12) caps the band twice**: how many
  `ranking.apply_popularity` promotes, and how many leading rows the listing
  is willing to CALL popular. It shapes the body, so it is in the ETag.
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

### `terms()` — the reader the protocol does not declare

`VocabularyResolver` is four questions about ONE code, deliberately: listing
belongs to the HTTP surface a typeahead talks to. But a category whose
`children_expand_by` names a `ref_select` feature has no code to ask about —
**its children ARE the level** — and stapel-categories (>= 0.22) reads that
through an OPTIONAL `terms(vocabulary, level)` on whatever resolver is
registered, drawing no virtual children when the resolver has no such method.
Six leaves on a live stand were exactly that: expansion configured, vocabulary
loaded, feature right, and an empty tree, because up to 0.2.1 this module
offered only `describe` / `exists` / `is_child` / `labels`.

Both resolvers answer it now, and both go through `level_terms()` so the two
cannot disagree about what a category's children are:

```
terms(vocabulary, level, *, parent=None, limit=None) -> [(code, label)]
```

- **Order is the level's own** (`Term.Meta.ordering`: popular band, curated
  `sort`, then label) — a deployment that promoted nothing gets the alphabet.
- **`parent` is a bare CODE** at the level above, not a `{level, code}` pair:
  the browser holds the code it descended through, and the level chain says
  the rest. A parent naming no term — and any parent on a root level — scopes
  **nothing**, never the whole level.
- **`[]`, never a raise**, for an unknown vocabulary or level. The consumer
  reads a raise as "no values" while logging a traceback per tree read, so the
  empty list is the same outcome told honestly. (`level_terms()` keeps the
  `None` distinction; `terms()` is where it is flattened.)
- **Capped at `TERMS_LIMIT`** (2000, `conf.py`), reported once per level. A
  level larger than the cap **is not a browse level** — put a coarser level
  above it and expand the category by that one.

`vocabularies.terms` is the same read over comm, and is **not**
`vocabularies.children` with a bigger number: `children` is a typeahead-sized
page (`MAX_PAGE_SIZE`, 200) for a caller reasoning ABOUT a set; this is the
level itself for a caller RENDERING it. One cap cannot serve a dropdown and a
tile grid, and the smaller of the two would win silently.

`tests/test_branching.py` pins the whole rung across the two libraries, out of
process (`tests/branching_harness.py` says why): the same category, once
against a resolver with only the four protocol methods (no children — the
live defect) and once against `OrmResolver` (one virtual child per term).

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
functions.py     vocabularies.resolve / describe / children / terms / match /
                 set_popularity (+ schemas/functions/)
events.py        vocabulary.changed (+ schemas/emits/)
resolver.py      OrmResolver, CommResolver, level_terms, register_orm_resolver
loader.py        load_fixture / load_files / validate_fixture
convert.py       nested_xml_to_fixture, csv_to_fixture, dump_fixture   (Django-free)
slug.py          slugify_term, dedupe_codes                            (Django-free)
checks.py        W001 (no protocol), W002 (tables here, resolver off), W003 (expander)
ranking.py       apply_popularity — observed counts -> the popular band
conf.py          STAPEL_VOCABULARIES + flag()/number()/real() env coercion, query_expander()
expand.py        literal — the default (identity) query expander      (Django-free)
docs/            contract artifacts + vocabulary-fixture.schema.json
```

## 10. The popular band, and matching a guess

Two answers to the same complaint from a live stand: *the dictionary is
alphabetical, and I cannot get a name into it.*

**The band (`ranking.py`, `Term.popularity`).** A curated band ages — rank
twelve brands by hand and eighteen months later the shortcut points at last
year's market. The rank that does not age is the one the listings state, so
the band is built from OBSERVED counts:

```python
from stapel_vocabularies.ranking import apply_popularity
apply_popularity("phone-models", "Vendor", {"samsung": 41_233, "apple": 38_902})
```

and over the bus that is `vocabularies.set_popularity {vocabulary, level,
counts} -> {ranked, revision} | null`. This module holds no listings and must
not learn to: what "live" means (published, unexpired, this region) is the
host's knowledge, so the counts are pushed, never pulled. Two properties are
load-bearing and both are pinned by tests — the call is **idempotent**
(unchanged counts write nothing, bump no revision, emit no
`vocabulary.changed`, so a nightly job does not kill every ETag for nothing)
and **bounded in queries** (one `CASE` over the band, one demotion; a
statement per term would make the same job an outage on a 15 000-term level).
Everything outside the band is demoted to 0 — a band nothing ever leaves is a
curated band with extra steps.

Until a deployment has counts, a fixture's optional 6th column carries a
curated rank. A row that OMITS the column leaves the live term's popularity
alone rather than zeroing it, which is what keeps a nightly push from being
erased by the next catalogue import.

**The source's own attributes (`Term.extra`, 0.4.0).** A term is identity plus
a label, and for one whole class of level that is not enough to render it: a
facet over `Color` wants to draw a swatch, and the only thing in the world
that knows «чёрный» is `#1a1a1a` is the catalogue the term came from. An
importer measuring 78 colour terms across five vocabularies had nowhere to put
one. So a term row grew an optional **7th column**, an object, merged into
`Term.extra` key by key on load — `["Color", "chernyy", "чёрный", null, 0, 0,
{"hue": "#1a1a1a"}]`.

It is a bag the SOURCE owns, and three things follow. It is never part of
identity: that stays `(vocabulary, level, code)`, and nothing in this module
reads a key out of it — a consumer that finds no key it wants renders the term
without it. It is never a second place for a label: `label` / `labels` answer
that question, once. And it MERGES rather than replaces, because an additive
load is two catalogues contributing to one vocabulary, and the file that knows
a hue is not the file that knows a vendor's own shade code; a row that omits
the column (or states `{}`) changes nothing, the same rule `popularity` states
one column to the left.

It reaches a reader three ways, and the shapes are deliberately not the same:

- REST — `extra` on a term of `…/terms/?level=`, **omitted** when the term
  carries none rather than `{}` on every row of a 2000-term page.
- comm — `extras` on `vocabularies.terms`, `{code: {...}}`, its **own key**
  and not a third element of each `[code, label]` row. A 0.3.0
  `CommResolver.terms` unpacks those rows positionally, so widening them
  would have made this release break every service still on the last one by
  existing. Nobody is broken by a key they do not read.
- in-process — **`terms_with_extra(vocabulary, level, *, parent, limit)`** on
  both resolvers, `[(code, label, extra)]`. `terms()` keeps its pair, for the
  same reason: stapel-categories takes it apart positionally too.

**The match (`vocabularies.match`).** `resolve` answers about codes a caller
already has. A composer holding a string out of a photo or a language model
has none, and it writes whatever comes back into a listing with no human in
between — so a near-miss is not a worse result, it is wrong data nobody
looked at. The answer is two-shaped, with no room for a maybe:

```
{vocabulary, level, text, parent?, min_score?}
  -> {matched: true,  code, label, score, method: "exact"|"prefix"|"vector"}
  -> {matched: false, reason: "no_confident_match"|"unknown_vocabulary"|"unknown_level"}
```

Three rungs: **exact** (1.0 — the folded label, the code, or the code
`slug.slugify_term` would mint from the text, which is how «Самсунг» reaches
`samsung` with no embedding and no bill), **unique prefix** (0.9 — two
candidates is a different question, not a weaker match), then the **vector**
seam carrying the similarity the far side stated, verbatim. A neighbour
returned WITHOUT a score is refused: a confidence nobody measured is not a
confidence, and inventing one here is the defect the Function exists to
close. `MATCH_MIN_SCORE` (0.8) is the floor; `min_score` overrides per call.
Nothing in the vector path may raise past the Function boundary — an
unconfigured seam, a provider that is down and a low score are one prompt
"no".
