from django.db import migrations, models


class Migration(migrations.Migration):
    """Add ``Term.extra`` — expand only.

    A nullable-free JSON column with a ``dict`` default: every live row reads
    back ``{}``, nothing is rewritten, and a 0.3.0 process running against a
    migrated database never selects the column. Rollback is the field going
    away again; no data moves either way.
    """

    dependencies = [
        ('vocabularies', '0002_popularity_band'),
    ]

    operations = [
        migrations.AddField(
            model_name='term',
            name='extra',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
