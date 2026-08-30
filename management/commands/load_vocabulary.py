"""``manage.py load_vocabulary <file.json> [...] [--replace]`` (spec §3.3)."""
from django.core.management.base import BaseCommand, CommandError

from stapel_vocabularies.loader import FixtureError, load_files


class Command(BaseCommand):
    help = (
        "Load one or more vocabulary fixtures (docs/vocabulary-fixture.schema.json). "
        "One file is one transaction, one revision increment and one "
        "vocabulary.changed event."
    )

    def add_arguments(self, parser):
        parser.add_argument("files", nargs="+", help="Fixture files to load.")
        parser.add_argument(
            "--replace",
            action="store_true",
            help=(
                "Make each file authoritative: terms it does not mention are "
                "deleted and the vocabulary's edge set is rebuilt from it. "
                "Without this the load is additive."
            ),
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=None,
            help="Rows per bulk_create/bulk_update batch "
            "(default: STAPEL_VOCABULARIES['LOAD_BATCH_SIZE']).",
        )

    def handle(self, *args, **options):
        try:
            results = load_files(
                options["files"],
                replace=options["replace"],
                batch_size=options["batch_size"],
            )
        except FixtureError as exc:
            raise CommandError(str(exc)) from None
        except FileNotFoundError as exc:
            raise CommandError(str(exc)) from None

        for result in results:
            verb = "created" if result.created else "updated"
            self.stdout.write(
                f"{result.slug}: {verb} at revision {result.revision} — "
                f"terms +{result.terms_created} ~{result.terms_updated} "
                f"-{result.terms_deleted}, edges +{result.edges_created} "
                f"-{result.edges_deleted}"
            )
