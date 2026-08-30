"""``manage.py convert_vocabulary <input> --slug S --out F`` (spec §3.3).

A thin wrapper over the Django-free converters in ``convert.py`` — the same
functions the Avito importer calls directly. It exists so an operator holding
a vendor catalogue and a Django project does not need a second toolchain to
turn one into a reviewable fixture.
"""
from django.core.management.base import BaseCommand, CommandError

from stapel_vocabularies.convert import (
    ConvertError,
    csv_to_fixture,
    nested_xml_to_fixture,
    write_fixture,
)


class Command(BaseCommand):
    help = (
        "Convert a nested-XML or CSV catalogue into a vocabulary fixture "
        "(docs/vocabulary-fixture.schema.json), ready for load_vocabulary."
    )

    def add_arguments(self, parser):
        parser.add_argument("source", help="Catalogue file to read.")
        parser.add_argument("--slug", required=True, help="Slug of the vocabulary.")
        parser.add_argument("--out", required=True, help="Fixture file to write.")
        parser.add_argument(
            "--format",
            choices=["nested-xml", "csv"],
            default="nested-xml",
            help="Shape of the source file (default: nested-xml).",
        )
        parser.add_argument("--name", default=None, help="Display name (default: the slug).")
        parser.add_argument(
            "--source-url",
            default="",
            help="Where the catalogue came from, recorded as provenance.",
        )
        parser.add_argument(
            "--levels",
            default=None,
            help="nested-xml: comma-separated element tags to treat as levels, "
            "root first. Omit to auto-detect them in document order.",
        )
        parser.add_argument(
            "--name-attr",
            default="name",
            help="nested-xml: attribute carrying a term's label (default: name).",
        )
        parser.add_argument(
            "--id-attr",
            default=None,
            help="nested-xml: attribute carrying the catalogue's own id, stored "
            "as external_id. The code stays a slug of the label.",
        )
        parser.add_argument(
            "--columns",
            default=None,
            help="csv: comma-separated column names, root level first. Required "
            "for --format csv.",
        )
        parser.add_argument("--delimiter", default=",", help="csv: field delimiter.")

    def handle(self, *args, **options):
        try:
            if options["format"] == "csv":
                if not options["columns"]:
                    raise CommandError("--columns is required for --format csv")
                fixture = csv_to_fixture(
                    options["source"],
                    options["slug"],
                    [c.strip() for c in options["columns"].split(",") if c.strip()],
                    name=options["name"],
                    source=options["source_url"],
                    delimiter=options["delimiter"],
                )
            else:
                levels = options["levels"]
                fixture = nested_xml_to_fixture(
                    options["source"],
                    options["slug"],
                    levels=(
                        [part.strip() for part in levels.split(",") if part.strip()]
                        if levels
                        else None
                    ),
                    name_attr=options["name_attr"],
                    id_attr=options["id_attr"],
                    name=options["name"],
                    source=options["source_url"],
                )
        except ConvertError as exc:
            raise CommandError(str(exc)) from None
        except FileNotFoundError as exc:
            raise CommandError(str(exc)) from None

        path = write_fixture(fixture, options["out"])
        self.stdout.write(
            f"{fixture['slug']}: {len(fixture['levels'])} levels, "
            f"{len(fixture['terms'])} terms, {len(fixture['edges'])} edges -> {path}"
        )
