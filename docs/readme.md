## What this is

Some option lists are too big to be options. A phone catalogue has 14 962
models; a car catalogue has 107 049 modifications. Inlining those into a
category's feature schema is not a tuning question — the schema is fetched on
every form render, and it would be megabytes.

**stapel-vocabularies** is where they live instead.

- **Levels, terms and edges — not paths.** `Vendor → Model → MemorySize →
  Color` is 56 921 distinct paths in a real phone catalogue and only
  15 844 distinct terms. Storing the terms and the parent/child edges between
  them means one `Color=chernyy` shared by every model that comes in black:
  17 colours to translate instead of 56 921 path nodes, and a facet on a
  colour code that is answerable at all.
- **A read surface built for a typeahead.** `terms/?level=Model&parent=apple&q=pro`
  answers a page with `total` and `has_children`, prefix matches ranked first.
  Anonymous, `ETag`'d on the vocabulary's revision, `Cache-Control:
  public`, and no `Set-Cookie` — so the shared cache in front of it works and
  a crawler does not start a session per request.
- **Two resolvers, one protocol.** `ref_select` / `ref_hierarchical_select` in
  [stapel-attributes](https://github.com/usestapel/stapel-attributes) validate
  values through a `VocabularyResolver`. `OrmResolver` answers from these
  tables and is registered at startup; `CommResolver` answers the same
  questions over the bus, for a service that validates listings but holds no
  catalogues. Both cache `describe` **by revision**, so a re-imported
  catalogue stops validating against the levels it used to have the moment the
  import commits.
- **Loading is data plumbing, not an admin screen.** `manage.py
  load_vocabulary phones.json` is one transaction, one revision increment and
  one `vocabulary.changed` event for the whole file, whatever its size. The
  real phone catalogue — 15 844 terms, 39 749 edges — loads in ~1.2 s.
- **Converters that do not read the file into memory.** A vendor's nested XML
  or a one-path-per-row CSV becomes a reviewable fixture, streamed through
  `iterparse`, with codes assigned deterministically (Cyrillic transliterated,
  collisions numbered in label sort order) so re-converting an unchanged
  catalogue produces an unchanged diff.

Alpha. See [MODULE.md](https://github.com/usestapel/stapel-vocabularies/blob/main/MODULE.md)
for the agent-facing map of seams.

## Quick start

```bash
pip install stapel-vocabularies
```

```python
# settings.py
INSTALLED_APPS = [..., "stapel_vocabularies"]

# urls.py
path("vocabularies/", include("stapel_vocabularies.urls"))   # -> /vocabularies/api/v1/...
```

```bash
python manage.py convert_vocabulary phone_catalog.xml \
    --slug phone-models --name "Phone models" --out fixtures/phone-models.json
python manage.py load_vocabulary fixtures/phone-models.json --replace
```

A feature then points at it instead of carrying options:

```json
{"type": "ref_select", "optionsRef": {"vocabulary": "phone-models",
                                      "level": "Model",
                                      "parentFeature": "vendor"}}
```

## API

| Method | Path | What |
|---|---|---|
| GET | `/vocabularies/api/v1/vocabularies/` | every vocabulary: `{slug, name, levels, term_count, revision}` |
| GET | `/vocabularies/api/v1/vocabularies/{slug}/` | one of them |
| GET | `/vocabularies/api/v1/vocabularies/{slug}/terms/` | `?level=` (required), `?parent=`, `?q=`, `?limit=` (≤200, default 50), `?offset=` → `{results: [{code, label, level, has_children}], total}` |
| GET | `/vocabularies/api/v1/vocabularies/{slug}/terms/resolve/` | `?level=&codes=a,b,c` (≤200) → `{code: label}`, unknown codes omitted |

`Accept-Language` selects a translated label where the term carries one; the
response `Vary`s on it and the `ETag` covers it.

## The fixture format

One file per vocabulary, byte-stable, reviewed as code
([schema](https://github.com/usestapel/stapel-vocabularies/blob/main/docs/vocabulary-fixture.schema.json)):

```json
{ "slug": "phone-models", "name": "Phone models", "source": "https://…/phone_catalog.xml",
  "levels": [{"name": "Vendor"}, {"name": "Model", "parent": "Vendor"}],
  "terms": [["Vendor", "apple", "Apple", null], ["Model", "iphone-10", "iPhone 10", null]],
  "edges": [["Vendor", "apple", "Model", "iphone-10"]] }
```

A level's `parent` must be declared before it. That single rule is the whole
acyclicity argument: a level can only point backwards, so no chain of parents
can return to where it started.
