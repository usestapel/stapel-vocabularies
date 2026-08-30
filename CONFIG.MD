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

Booleans and integers are read through `conf.flag()` / `conf.number()`, which
coerce the string an environment variable hands back — `REGISTER_RESOLVER=false`
turns the seam off, rather than being a truthy non-empty string.

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
