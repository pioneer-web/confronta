# CONFRONTA Módulo 2 v0.3.2 — cadastro público e estado sem plano.
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('aplicativo', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='perfilcliente',
            name='cpf',
            field=models.CharField(blank=True, max_length=11, null=True, unique=True),
        ),
        migrations.AddField(
            model_name='perfilcliente',
            name='plano_desejado',
            field=models.CharField(
                blank=True,
                choices=[('BASICO', 'Básico'), ('TOTAL', 'Total')],
                max_length=10,
                null=True,
            ),
        ),
        migrations.AlterField(
            model_name='perfilcliente',
            name='plano',
            field=models.CharField(
                choices=[('SEM_PLANO', 'Sem plano'), ('BASICO', 'Básico'), ('TOTAL', 'Total')],
                db_index=True,
                default='SEM_PLANO',
                max_length=12,
            ),
        ),
    ]
