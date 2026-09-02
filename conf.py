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
- ``QUERY_EXPANDER`` — the callable that turns one typed query into the
  match variants the term search ORs together (see ``expand.py``). The
  fleet's cross-script layer lives in the search library; this seam is how
  a deployment consumes it without this module depending on it.
"""
import logging

from stapel_core.conf import AppSettings

logger = logging.getLogger(__name__)

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
    # Seam: `(query: str, language: str) -> Sequence[str]` — the match
    # variants the term search ORs together, the literal query included.
    # The default is this module's own identity expansion, so a standalone
    # install matches exactly what it matched before the seam existed. A
    # fleet that runs stapel-search points this at
    # `stapel_search.suggest.query_terms` — the ONE normalization layer.
    "QUERY_EXPANDER": "stapel_vocabularies.expand.literal",
}

vocabularies_settings = AppSettings(
    "STAPEL_VOCABULARIES",
    defaults=DEFAULTS,
    # Resolved with import_string, lazily, cached until setting_changed —
    # and thereby closed to the environment: a key that names the code the
    # process runs is not a value an env var may pick.
    import_strings=("QUERY_EXPANDER",),
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


def query_expander():
    """The configured ``QUERY_EXPANDER`` callable, degraded loudly but safely.

    The term search runs behind somebody's keystrokes, so a dotted path
    that does not import — a typo, a package the deployment forgot — must
    cost recall (literal matching only), never a 500. System check W003
    says the same thing at boot, by name; this is the per-request floor
    under it.
    """
    try:
        expander = vocabularies_settings.QUERY_EXPANDER
    except ImportError:
        logger.warning(
            "STAPEL_VOCABULARIES['QUERY_EXPANDER'] does not import; term "
            "searches match the literal query only (see check "
            "stapel_vocabularies.W003)",
            exc_info=True,
        )
        from .expand import literal

        return literal
    if not callable(expander):
        logger.warning(
            "STAPEL_VOCABULARIES['QUERY_EXPANDER'] resolved to a "
            "non-callable %r; term searches match the literal query only "
            "(see check stapel_vocabularies.W003)",
            expander,
        )
        from .expand import literal

        return literal
    return expander


__all__ = ["DEFAULTS", "flag", "number", "query_expander", "vocabularies_settings"]
