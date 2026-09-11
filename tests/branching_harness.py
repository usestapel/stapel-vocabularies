"""Out-of-process check: a category's children, drawn through this resolver.

Run as a script, never collected by pytest (``tests/test_branching.py`` runs
it in a subprocess and asserts on the JSON it prints).

**Why a subprocess.** stapel-categories is an OPTIONAL consumer of this
module: it is not a dependency, it is not in this suite's INSTALLED_APPS, and
putting it there would drag a second Django app into the settings block the
contract-emission harness shares (``_codegen_settings.py``) — the emitted
contract would then be emitted under a configuration no deployment of THIS
module has. So the one test that needs both apps configures its own instance,
exactly as ``tests/test_contract.py`` runs the emitter out of process.

**What it proves.** The rung this release closes runs across two libraries:

    Category.children_expand_by -> the feature's ref_select optionsRef
      -> stapel_categories.branching._vocabulary_terms
        -> the registered VocabularyResolver's OPTIONAL terms() reader
          -> stapel_vocabularies.resolver.OrmResolver.terms

Every link but the last one already shipped, and the tree drew zero virtual
children because the last one did not exist. So the harness answers the same
question twice against the same rows: once with a resolver that has only the
four protocol methods (what this module shipped up to 0.2.1 — the RED half,
and the reason six leaves on a live stand had no children), and once with the
real ``OrmResolver``. The difference between the two answers is the release.
"""
import json
import sys


def _configure():
    from django.conf import settings

    from stapel_vocabularies._codegen_settings import settings_kwargs

    kwargs = settings_kwargs()
    kwargs["INSTALLED_APPS"] = list(kwargs["INSTALLED_APPS"]) + [
        # django-treenode's AppConfig wires the tree-cache signals the
        # Category/Feature tn_* fields need.
        "treenode",
        "stapel_categories",
    ]
    # Tables straight from the models, the way this suite already builds its
    # own — a migration graph is not what is under test here.
    kwargs["MIGRATION_MODULES"] = dict(kwargs["MIGRATION_MODULES"], categories=None)
    settings.configure(**kwargs)

    import django

    django.setup()

    from django.core.management import call_command

    call_command("migrate", run_syncdb=True, verbosity=0)


class _ProtocolOnly:
    """A resolver with the four protocol methods and nothing else.

    Not a mock of something that never existed: this is precisely the surface
    ``OrmResolver`` had before this release, so what it draws is what the live
    stand drew.
    """

    def __init__(self, inner):
        self._inner = inner

    def describe(self, vocabulary):
        return self._inner.describe(vocabulary)

    def exists(self, vocabulary, level, code):
        return self._inner.exists(vocabulary, level, code)

    def is_child(self, vocabulary, level, code, parent_level, parent_code):
        return self._inner.is_child(vocabulary, level, code, parent_level, parent_code)

    def labels(self, vocabulary, level, codes):
        return self._inner.labels(vocabulary, level, codes)


def main():
    _configure()

    from stapel_attributes.vocabularies import register_vocabulary_resolver
    from stapel_categories.branching import virtual_children
    from stapel_categories.models import Category, CategoryFeature, Feature

    from stapel_vocabularies.loader import load_fixture
    from stapel_vocabularies.resolver import OrmResolver

    load_fixture(
        {
            "slug": "makes",
            "name": "Makes",
            "levels": [{"name": "Make"}, {"name": "Model", "parent": "Make"}],
            "terms": [
                # A bag on the first term, deliberately: 0.4.0 widened the
                # MODEL, and the question this harness answers is whether the
                # consumer it widened around still reads the same rows.
                ["Make", "charlie", "Charlie", None, 0, 0, {"hue": "#1a1a1a"}],
                ["Make", "alfa", "Alfa", None],
                ["Make", "bravo", "Bravo", None],
                ["Model", "alfa-one", "Alfa One", None],
            ],
            "edges": [["Make", "alfa", "Model", "alfa-one"]],
        }
    )

    feature = Feature.objects.create(
        name="Make",
        slug="make_ref",
        config={
            "type": "ref_select",
            "optionsRef": {"vocabulary": "makes", "level": "Make"},
        },
    )
    category = Category.objects.create(name="Vehicles", slug="vehicles")
    CategoryFeature.objects.create(category=category, feature=feature, order=0)
    # The leaf's children ARE the values of that feature — the setting six
    # leaves on the client stand carry.
    category.children_expand_by = "make_ref"
    category.save()

    orm = OrmResolver()
    answers = {}
    for key, resolver in (("without_terms", _ProtocolOnly(orm)), ("with_terms", orm)):
        register_vocabulary_resolver(resolver)
        answers[key] = virtual_children(Category.objects.get(pk=category.pk))
    register_vocabulary_resolver(None)

    json.dump(answers, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
