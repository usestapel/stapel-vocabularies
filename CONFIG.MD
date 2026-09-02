# CONFIG.MD — stapel-vocabularies

Config registry for **stapel-vocabularies** (`static-scaffold-and-config.md`
§2). One row per configuration key the module reads, its **source** (`env` =
the process environment / the `STAPEL_VOCABULARIES` settings dict), what it is
for, whether it is required, and its default.

Every key is read through `vocabularies_settings`
(`stapel_vocabularies.conf.AppSettings`, namespace `STAPEL_VOCABULARIES`).
Resolution order per key: `settings.STAPEL_VOCABULARIES` dict → a flat Django
setting of the same name → environment variable → the default below.
`REGISTER_RESOLVER` is the one CTO-facing config axis (capability-config.md
§16); everything else is a tuning knob.

`QUERY_EXPANDER` is the exception to the environment step: it is an
`import_strings` key (it names the code the process runs), so it resolves
via the `STAPEL_VOCABULARIES` dict or a flat Django setting only — a
same-named environment variable is deliberately ignored.

Booleans, integers and fractions are read through `conf.flag()` /
`conf.number()` / `conf.real()`, which coerce the string an environment
variable hands back — `REGISTER_RESOLVER=false` turns the seam off, rather
than being a truthy non-empty string, and `MATCH_MIN_SCORE=0.75` is compared
as a number rather than raising a TypeError against a similarity score.

This module holds **no credentials**: it stores public catalogue data and
fetches nothing. There is no `vault` row and there should never be one.

## stapel-vocabularies

| Key | Source | Purpose | Required | Default |
|-----|--------|---------|----------|---------|
| REGISTER_RESOLVER | env | axis: whether `AppConfig.ready()` registers `OrmResolver` with stapel-attributes, so ref-typed features resolve in this process. Turn off only in a service that holds no vocabulary tables and points `STAPEL_ATTRIBUTES["VOCABULARY_RESOLVER"]` at `CommResolver` — system check `stapel_vocabularies.W002` warns when the process that owns the tables is the one declining to answer. | no | true |
| CACHE_MAX_AGE | env | `Cache-Control: public, max-age=…` on every read (seconds). The `ETag` carries the vocabulary's revision, so a stale edge cache is corrected by a conditional request rather than by waiting this out. | no | 300 |
| RESOLVER_CACHE_TTL_SECONDS | env | ceiling on a `CommResolver` `describe` that no `vocabulary.changed` invalidated. The event is the normal invalidation path; this bounds a deployment the event does not reach. | no | 60 |
| MAX_PAGE_SIZE | env | hard ceiling on `?limit=` for the term listing, and on how many codes one `terms/resolve/` call may name. A page of terms is a typeahead, not an export. | no | 200 |
| DEFAULT_PAGE_SIZE | env | page size when the client does not ask. | no | 50 |
| LOAD_BATCH_SIZE | env | rows per `bulk_create`/`bulk_update` batch in `load_vocabulary`. Lower it for a database with a tight parameter limit; it does not change what the load does, only how it is cut up. | no | 2000 |
| POPULAR_BAND_SIZE | env | how many terms of one level may sit in the **popular band** — the short recommended set a picker opens on instead of whatever the alphabet put first. It is both how many `ranking.apply_popularity` promotes and how many leading rows the term listing is willing to label `band: "popular"`, so a curated fixture that promoted forty terms still cannot hand a frontend a forty-row band. Twelve is a dropdown's first screenful; a shortcut nobody can take in at a glance is just a second alphabet. Folded into the term listing's ETag, because it changes the response bytes. | no | 12 |
| QUERY_EXPANDER | settings | seam: dotted path to the callable `(query: str, language: str) -> Sequence[str]` that turns one typed `?q=` into the match variants the term search ORs together, the literal query included; `language` is the best `Accept-Language` tag (empty when none). The fleet keeps ONE cross-script normalization layer (folding, RU↔EN transliteration, alias groups) and it lives in stapel-search — a composite that runs it points this at `stapel_search.suggest.query_terms`, so «тимберленд» finds "Timberland" in a brand picker with the same variants the suggest matcher uses. The default is this module's own identity expansion, so a standalone install matches exactly the literal substring. An `import_strings` key: resolved lazily via `import_string`, cached, and **never read from the environment** — it names the code the process runs. A path that does not import degrades to literal matching with a log line; system check `stapel_vocabularies.W003` reports it at boot. | no | `stapel_vocabularies.expand.literal` |
| VECTOR_SIMILAR_FUNCTION | env | the vector net under the typeahead (`vector.py`): comm name of the similarity Function asked when the first page of a `?q=` answer is thinner than `VECTOR_MIN_RESULTS`. Empty (the default) means OFF — no call, no appended rows, byte-identical answers and ETags. A fleet running stapel-search's vector layer sets `search.similar`; appended rows carry `match: "vector"` and re-impose this module's scope (vocabulary, level, parent) on whatever the embedding space liked. | no | `""` |
| VECTOR_KIND | env | the corpus kind this module's labels live under on the search side (`STAPEL_SEARCH["VECTOR_CORPORA"]`). | no | `vocab_label` |
| VECTOR_MIN_RESULTS | env | below this many deterministic hits (first page, `offset=0` only) the vector net is consulted. | no | 3 |
| VECTOR_LABEL_LEVELS | settings | glob patterns of level names `vector.label_corpus` embeds (`brand*`, `brend*`, `make*`, `marka*`, `Vendor`) — where the typo problem lives. Empty by default: embedding is a deployment decision with a bill, priced by `manage.py search_vector_index --estimate` on the search side. | no | `()` |
| MATCH_MIN_SCORE | env | floor under the `vocabularies.match` Function: a candidate scoring below it is refused (`matched: false`) rather than returned as a guess. The caller is a composer in another service writing the answer straight into a listing, so the asymmetry is the whole calibration — a refusal costs one clarifying question, a false positive costs wrong data in a published listing. 0.8 sits above the "shares a substring" band a similarity layer answers a bad transliteration with (a live stand's «айфон» returns MyPhone / Fairphone / Elephone, all wrong) and above the far side's own floor, which plainly passed those, while leaving room for a true neighbour. A per-call `min_score` overrides it; a deployment that has logged its own scores should tune it. Exact hits score 1.0 and a unique prefix hit 0.9, so a floor above those disables those rungs too. | no | 0.8 |
