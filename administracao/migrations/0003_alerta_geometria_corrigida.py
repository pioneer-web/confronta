from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('administracao', '0002_dataset_imports'),
    ]

    operations = [
        migrations.AlterField(
            model_name='alerta',
            name='tipo',
            field=models.CharField(choices=[
                ('TABELA_NAO_UTILIZADA', 'Tabela não utilizada'),
                ('ALTERACAO_ESTRUTURAL', 'Alteração estrutural'),
                ('GEOMETRIA_CORRIGIDA', 'Geometrias corrigidas automaticamente'),
            ], max_length=40),
        ),
    ]
