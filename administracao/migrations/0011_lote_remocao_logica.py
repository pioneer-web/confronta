from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('administracao', '0010_lote_interrupcao'),
    ]

    operations = [
        migrations.AddField(
            model_name='loteimportacao',
            name='oculto_painel',
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name='loteimportacao',
            name='removido_painel_em',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
