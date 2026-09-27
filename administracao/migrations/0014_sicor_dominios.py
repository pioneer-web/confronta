from django.db import migrations, models


_FONTE_CHOICES = [
    ('SICAR', 'SICAR'), ('IBAMA', 'IBAMA'), ('ICMBIO', 'ICMBio'), ('CNUC', 'CNUC'),
    ('PRODES', 'INPE / PRODES'), ('INCRA', 'INCRA'), ('SICOR', 'SICOR / Crédito Rural'),
    ('SICOR_OPERACOES', 'Operações SICOR'), ('SICOR_DOMINIOS', 'Domínios SICOR'),
    ('SIGEF', 'SIGEF / INCRA'), ('SNCR', 'SNCR / INCRA'), ('FUNAI', 'FUNAI / Terras Indígenas'),
    ('FLORESTAS_PUBLICAS', 'Florestas Públicas'), ('DETER', 'INPE / DETER'), ('ANA', 'ANA / Outorgas'),
    ('ANM', 'ANM / Processos Minerários'), ('ZARC', 'ZARC'), ('MAPBIOMAS', 'MapBiomas'),
    ('FOCOS_CALOR', 'INPE / Focos de Calor'),
]


class Migration(migrations.Migration):
    dependencies = [('administracao', '0013_sicor_operacoes_model')]

    operations = [
        migrations.RunSQL('CREATE SCHEMA IF NOT EXISTS "dados_sicor"', migrations.RunSQL.noop),
        migrations.CreateModel(
            name='SicorInstituicao',
            fields=[('cnpj_if', models.TextField(primary_key=True, serialize=False)),
                    ('nome_if', models.TextField()), ('segmento_if', models.TextField(blank=True, default=''))],
            options={'db_table': '"dados_sicor"."sicor_instituicoes"', 'verbose_name': 'instituição financeira SICOR', 'verbose_name_plural': 'instituições financeiras SICOR'},
        ),
        migrations.CreateModel(
            name='SicorPrograma',
            fields=[('cd_programa', models.TextField(primary_key=True, serialize=False)),
                    ('descricao', models.TextField()), ('data_inicio', models.DateField(blank=True, null=True)),
                    ('data_fim', models.DateField(blank=True, null=True)),
                    ('financiamento', models.TextField(blank=True, default=''))],
            options={'db_table': '"dados_sicor"."sicor_programas"', 'verbose_name': 'programa SICOR', 'verbose_name_plural': 'programas SICOR'},
        ),
        migrations.AlterField(model_name='importacao', name='fonte', field=models.CharField(max_length=20, choices=_FONTE_CHOICES, db_index=True)),
        migrations.AlterField(model_name='loteimportacao', name='fonte', field=models.CharField(max_length=20, choices=_FONTE_CHOICES, db_index=True)),
        migrations.AlterField(model_name='camadaimportada', name='fonte', field=models.CharField(max_length=20, choices=_FONTE_CHOICES, db_index=True)),
        migrations.AlterField(model_name='alerta', name='fonte', field=models.CharField(max_length=20, choices=_FONTE_CHOICES, db_index=True)),
    ]
