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
