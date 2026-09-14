from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('aplicativo', '0008_rename_indices_atendimento'),
    ]

    operations = [
        migrations.AddField(
            model_name='perfilcliente',
            name='token_sessao_ativa',
            field=models.CharField(
                blank=True,
                db_index=True,
                default='',
                editable=False,
                max_length=64,
            ),
        ),
        migrations.AddField(
            model_name='perfilcliente',
            name='sessao_ativa_em',
            field=models.DateTimeField(
                blank=True,
                editable=False,
                null=True,
            ),
        ),
    ]
