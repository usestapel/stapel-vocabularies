"""i18n error keys of stapel-vocabularies.

Only ``error.<status>.<slug>`` keys leave this package — human-readable
strings are translations, never literals in responses.
"""
from stapel_core.django.api.errors import register_service_errors

ERR_404_VOCABULARY_NOT_FOUND = "error.404.vocabularies_vocabulary_not_found"
#: Also the answer to a missing ``level`` parameter: the level named (nothing)
#: is not a level of this vocabulary, and a second key for the empty case
#: would be a distinction no client can act on differently.
ERR_404_LEVEL_NOT_FOUND = "error.404.vocabularies_level_not_found"
ERR_400_BAD_PARENT = "error.400.vocabularies_bad_parent"

STAPEL_VOCABULARIES_ERRORS = {
    ERR_404_VOCABULARY_NOT_FOUND: "Vocabulary not found",
    ERR_404_LEVEL_NOT_FOUND: "Vocabulary '{vocabulary}' has no level '{level}'",
    ERR_400_BAD_PARENT: "No term '{parent}' at the parent level of '{level}'",
}

register_service_errors(STAPEL_VOCABULARIES_ERRORS)

__all__ = [
    "ERR_400_BAD_PARENT",
    "ERR_404_LEVEL_NOT_FOUND",
    "ERR_404_VOCABULARY_NOT_FOUND",
    "STAPEL_VOCABULARIES_ERRORS",
]
