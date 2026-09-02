from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('aplicativo', '0004_avisos_clientes'),
    ]

    operations = [
        migrations.CreateModel(
            name='LimiteSeguranca',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('escopo', models.CharField(max_length=64)),
                ('chave_hash', models.CharField(max_length=64)),
                ('janela_iniciada_em', models.DateTimeField()),
                ('tentativas', models.PositiveIntegerField(default=0)),
                ('bloqueado_ate', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('atualizado_em', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'limite de segurança',
                'verbose_name_plural': 'limites de segurança',
            },
        ),
        migrations.AddConstraint(
            model_name='limiteseguranca',
            constraint=models.UniqueConstraint(fields=('escopo', 'chave_hash'), name='uniq_limite_seguranca_escopo_chave'),
        ),
        migrations.AddIndex(
            model_name='limiteseguranca',
            index=models.Index(fields=['escopo', 'atualizado_em'], name='idx_seg_escopo_atualiz'),
        ),
    ]
