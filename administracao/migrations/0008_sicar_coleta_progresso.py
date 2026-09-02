from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('administracao', '0007_sicar_coleta_automatica'),
    ]

    operations = [
        migrations.AddField(
            model_name='sicarcoletaautomatica',
            name='progresso_percentual',
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='sicarcoletaautomatica',
            name='etapa',
            field=models.CharField(blank=True, max_length=180),
        ),
        migrations.AddField(
            model_name='sicarcoletaautomatica',
            name='bytes_baixados',
            field=models.BigIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='sicarcoletaautomatica',
            name='ultima_atividade',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
