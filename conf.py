"""Settings namespace for stapel-vocabularies.

Everything is read through ``vocabularies_settings`` (lazily, at call time).
Resolution order per key: ``settings.STAPEL_VOCABULARIES`` dict -> a flat
Django setting of the same name -> environment variable -> the default below.

The seams (see MODULE.md):

- ``REGISTER_RESOLVER`` — whether ``AppConfig.ready()`` hands stapel-attributes
  the in-process ``OrmResolver``. On by default: a service that has the tables
  is the service that should answer. A service that holds no vocabulary tables
  turns it off and points ``STAPEL_ATTRIBUTES["VOCABULARY_RESOLVER"]`` at
  ``stapel_vocabularies.resolver.CommResolver`` instead.
- ``RESOLVER_CACHE_TTL_SECONDS`` — how long the ``CommResolver`` may hold a
  ``describe`` it could not revalidate. It also subscribes to
  ``vocabulary.changed``, so this is the ceiling for a deployment where that
  event does not reach it, not the normal invalidation path.
"""
from stapel_core.conf import AppSettings

#: AppSettings-shaped literal dict (capability-config.md §2): a top-level
#: DEFAULTS lets the capabilities.json emitter introspect axis keys/kinds.
DEFAULTS = {
    # Axis: does this service answer vocabulary questions in-process? True
    # where the tables live, False in a service that resolves over comm.
    "REGISTER_RESOLVER": True,
    # max-age of the public term reads. Terms move when a catalogue is
    # re-imported, which is a deployment event, not a user action — but the
    # ETag carries the revision, so a stale cache is corrected by a
    # conditional request rather than by waiting this out.
    "CACHE_MAX_AGE": 300,
    # Ceiling on a CommResolver describe that no vocabulary.changed
    # invalidated. Seconds.
    "RESOLVER_CACHE_TTL_SECONDS": 60,
    # Hard ceiling on `limit` for the term listing, and on the number of codes
    # one resolve call may name. A page of terms is a typeahead, not an export.
    "MAX_PAGE_SIZE": 200,
    # Default page size when the client does not ask.
    "DEFAULT_PAGE_SIZE": 50,
    # Rows per bulk_create/bulk_update batch in load_vocabulary.
    "LOAD_BATCH_SIZE": 2000,
}

vocabularies_settings = AppSettings(
    "STAPEL_VOCABULARIES",
    defaults=DEFAULTS,
)

#: Truthy spellings accepted for a boolean key set from the environment.
_TRUE = frozenset({"1", "true", "yes", "on"})


def flag(key: str) -> bool:
    """Read a boolean key, tolerating the string an env var hands back.

    ``AppSettings`` returns ``os.environ`` values verbatim, so a deployment
    that set ``REGISTER_RESOLVER=false`` would otherwise register the resolver
    anyway — a non-empty string is truthy. Reading every boolean through here
    means "false" turns the seam off wherever it is written.
    """
    value = getattr(vocabularies_settings, key)
    if isinstance(value, str):
        return value.strip().casefold() in _TRUE
    return bool(value)


def number(key: str) -> int:
    """Read an integer key, tolerating the string an env var hands back."""
    value = getattr(vocabularies_settings, key)
    if isinstance(value, str):
        return int(value.strip())
    return int(value)


__all__ = ["DEFAULTS", "flag", "number", "vocabularies_settings"]
