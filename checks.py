"""Django system checks for stapel-vocabularies configuration.

Policy (docs/library-standard.md §3.7): E-level for configuration the service
cannot run with; W-level for what only degrades lazily.

- The installed stapel-attributes has no ``vocabularies`` protocol module, so
  no resolver could be registered -> W. The service boots and serves
  everything but ref-typed feature validation, which is a real deployment
  (the terms API and the comm Functions work regardless) — but silently
  serving a catalogue whose ``ref_select`` features refuse to save is exactly
  the quiet failure this module exists to avoid.
- ``REGISTER_RESOLVER`` off in a deployment that DOES mount this module's URLs
  and has the tables -> W. That combination means the process holding the
  vocabularies is the one that declined to answer about them, so every
  ``ref_select`` config validation in it fails with "no vocabulary resolver
  registered" while the HTTP surface happily lists the same terms.
- ``QUERY_EXPANDER`` names something that does not import or is not a
  callable -> W. The request path degrades to literal matching and logs
  (a picker must not 500 over a dotted path), so without this check the
  only trace of the typo is quietly worse recall on every term search.
"""
from django.core import checks


@checks.register(checks.Tags.compatibility)
def check_resolver_protocol_available(app_configs, **kwargs):
    from .conf import flag

    if not flag("REGISTER_RESOLVER"):
        return []
    try:
        import stapel_attributes.vocabularies  # noqa: F401
    except ImportError as exc:
        return [
            checks.Warning(
                "stapel_attributes.vocabularies is not importable "
                f"({exc}), so no VocabularyResolver was registered: every "
                "ref_select / ref_hierarchical_select feature in this "
                "deployment will fail config validation with 'no vocabulary "
                "resolver registered', while this module's own term "
                "endpoints keep answering normally.",
                hint=(
                    "Install stapel-attributes>=0.5 (the release that "
                    "declares the resolver protocol), or set "
                    "STAPEL_VOCABULARIES['REGISTER_RESOLVER'] = False to "
                    "state that this service does not answer vocabulary "
                    "questions."
                ),
                id="stapel_vocabularies.W001",
            )
        ]
    return []


@checks.register(checks.Tags.compatibility)
def check_resolver_registered_where_tables_live(app_configs, **kwargs):
    """W002: the process that owns the tables is the one that declined to answer.

    Measured, not assumed: the signal that this deployment owns the
    vocabularies is that it mounts their URL surface. A service that installs
    the app only for its models (a loader box, a migration runner) mounts no
    URLs and is not reported.
    """
    from stapel_core.django.mounts import module_urls_mounted

    from .conf import flag

    if flag("REGISTER_RESOLVER"):
        return []
    if not module_urls_mounted("stapel_vocabularies"):
        return []
    return [
        checks.Warning(
            "STAPEL_VOCABULARIES['REGISTER_RESOLVER'] is off, but this "
            "deployment mounts stapel_vocabularies' URLs — so the process "
            "that holds the terms is the one refusing to answer about them. "
            "Saving a ref_select feature here fails with 'no vocabulary "
            "resolver registered' while GET /vocabularies/api/v1/... lists "
            "the very same terms.",
            hint=(
                "Leave REGISTER_RESOLVER at its default (True) in the service "
                "that owns the tables; turn it off only in a service that "
                "resolves over comm (STAPEL_ATTRIBUTES['VOCABULARY_RESOLVER'] "
                "= 'stapel_vocabularies.resolver.CommResolver')."
            ),
            id="stapel_vocabularies.W002",
        )
    ]


@checks.register(checks.Tags.compatibility)
def check_query_expander_resolves(app_configs, **kwargs):
    """W003: the configured query expander is not a callable this process has.

    The term-search view already degrades to literal matching and logs when
    the seam is broken — a typeahead must answer — so at request time the
    misconfiguration only shows up as worse recall for the queries the
    expander existed to serve. Boot is where it gets said by name.
    """
    from .conf import vocabularies_settings

    try:
        expander = vocabularies_settings.QUERY_EXPANDER
    except ImportError as exc:
        problem = f"does not import ({exc})"
    else:
        if callable(expander):
            return []
        problem = f"resolved to a non-callable {expander!r}"
    return [
        checks.Warning(
            f"STAPEL_VOCABULARIES['QUERY_EXPANDER'] {problem}. Every term "
            "search in this deployment matches the literal query only, so "
            "the cross-script and alias variants the expander was "
            "configured to add are silently not matched.",
            hint=(
                "Point QUERY_EXPANDER at a callable "
                "`(query: str, language: str) -> Sequence[str]` — a fleet "
                "running stapel-search uses "
                "'stapel_search.suggest.query_terms' — or remove the key to "
                "fall back to the literal default "
                "('stapel_vocabularies.expand.literal')."
            ),
            id="stapel_vocabularies.W003",
        )
    ]
