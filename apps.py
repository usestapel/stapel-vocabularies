from django.apps import AppConfig


class VocabulariesConfig(AppConfig):
    name = "stapel_vocabularies"
    label = "vocabularies"
    verbose_name = "Reference vocabularies"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        # Import-time side effects: error-key registration, comm functions,
        # system checks. Keep each in its own module.
        from . import checks  # noqa: F401
        from . import errors  # noqa: F401
        from . import functions  # noqa: F401

        # The in-process resolver, handed to stapel-attributes so a
        # ref_select feature can be validated at all. Off in a service that
        # holds no vocabulary tables — it points STAPEL_ATTRIBUTES
        # ["VOCABULARY_RESOLVER"] at CommResolver instead.
        from .conf import flag
        from .resolver import register_orm_resolver

        if flag("REGISTER_RESOLVER"):
            register_orm_resolver()
