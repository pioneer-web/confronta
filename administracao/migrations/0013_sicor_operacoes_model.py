from django.db import migrations, models

import administracao.models.sicor_operacoes


FONTE_CHOICES = [
    ('SICAR', 'SICAR'),
    ('IBAMA', 'IBAMA'),
    ('ICMBIO', 'ICMBio'),
    ('CNUC', 'CNUC'),
    ('PRODES', 'INPE / PRODES'),
    ('INCRA', 'INCRA'),
    ('SICOR', 'SICOR / Crédito Rural'),
    ('SICOR_OPERACOES', 'Operações SICOR'),
    ('SIGEF', 'SIGEF / INCRA'),
    ('SNCR', 'SNCR / INCRA'),
    ('FUNAI', 'FUNAI / Terras Indígenas'),
    ('FLORESTAS_PUBLICAS', 'Florestas Públicas'),
    ('DETER', 'INPE / DETER'),
    ('ANA', 'ANA / Outorgas'),
    ('ANM', 'ANM / Processos Minerários'),
    ('ZARC', 'ZARC'),
    ('MAPBIOMAS', 'MapBiomas'),
    ('FOCOS_CALOR', 'INPE / Focos de Calor'),
]


class Migration(migrations.Migration):
    dependencies = [
        ('administracao', '0012_alinhar_estado_models'),
    ]

    operations = [
        migrations.RunSQL(
            sql='CREATE SCHEMA IF NOT EXISTS "dados_sicor"',
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.AlterField(
            model_name='alerta',
            name='fonte',
            field=models.CharField(choices=FONTE_CHOICES, db_index=True, max_length=20),
        ),
        migrations.AlterField(
            model_name='camadaimportada',
            name='fonte',
            field=models.CharField(choices=FONTE_CHOICES, db_index=True, max_length=20),
        ),
        migrations.AlterField(
            model_name='importacao',
            name='fonte',
            field=models.CharField(choices=FONTE_CHOICES, db_index=True, max_length=20),
        ),
        migrations.AlterField(
            model_name='loteimportacao',
            name='fonte',
            field=models.CharField(choices=FONTE_CHOICES, db_index=True, max_length=20),
        ),
        migrations.CreateModel(
            name='SicorOperacao',
            fields=[
                ('id', models.BigAutoField(auto_created=True, db_column='_id', primary_key=True, serialize=False, verbose_name='ID')),
                ('numero_linha', models.BigIntegerField(db_column='_numero_linha')),
                ('ano_arquivo', models.IntegerField(blank=True, db_column='_ano_arquivo', null=True)),
                ('ref_bacen', models.TextField(blank=True, null=True)),
                ('nu_ordem', models.BigIntegerField(blank=True, null=True)),
                ('cnpj_if', models.TextField(blank=True, null=True)),
                ('dt_emissao', models.DateField(blank=True, null=True)),
                ('dt_vencimento', models.DateField(blank=True, null=True)),
                ('cd_inst_credito', models.TextField(blank=True, null=True)),
                ('cd_categ_emitente', models.TextField(blank=True, null=True)),
                ('cd_fonte_recurso', models.TextField(blank=True, null=True)),
                ('cnpj_agente_invest', models.TextField(blank=True, null=True)),
                ('cd_estado', models.TextField(blank=True, null=True)),
                ('cd_ref_bacen_investimento', models.TextField(blank=True, null=True)),
                ('cd_tipo_seguro', models.TextField(blank=True, null=True)),
                ('cd_empreendimento', models.TextField(blank=True, null=True)),
                ('cd_programa', models.TextField(blank=True, null=True)),
                ('cd_tipo_encarg_financ', models.TextField(blank=True, null=True)),
                ('cd_tipo_irrigacao', models.TextField(blank=True, null=True)),
                ('cd_tipo_agricultura', models.TextField(blank=True, null=True)),
                ('cd_fase_ciclo_producao', models.TextField(blank=True, null=True)),
                ('cd_tipo_cultivo', models.TextField(blank=True, null=True)),
                ('cd_tipo_intgr_consor', models.TextField(blank=True, null=True)),
                ('cd_tipo_grao_semente', models.TextField(blank=True, null=True)),
                ('vl_aliq_proagro', administracao.models.sicor_operacoes.UnboundedNumericField(blank=True, null=True)),
                ('vl_juros', administracao.models.sicor_operacoes.UnboundedNumericField(blank=True, null=True)),
                ('vl_prestacao_investimento', administracao.models.sicor_operacoes.UnboundedNumericField(blank=True, null=True)),
                ('vl_prev_prod', administracao.models.sicor_operacoes.UnboundedNumericField(blank=True, null=True)),
                ('vl_quantidade', administracao.models.sicor_operacoes.UnboundedNumericField(blank=True, null=True)),
                ('vl_receita_bruta_esperada', administracao.models.sicor_operacoes.UnboundedNumericField(blank=True, null=True)),
                ('vl_parc_credito', administracao.models.sicor_operacoes.UnboundedNumericField(blank=True, null=True)),
                ('vl_rec_proprio', administracao.models.sicor_operacoes.UnboundedNumericField(blank=True, null=True)),
                ('vl_perc_risco_stn', administracao.models.sicor_operacoes.UnboundedNumericField(blank=True, null=True)),
                ('vl_perc_risco_fundo_const', administracao.models.sicor_operacoes.UnboundedNumericField(blank=True, null=True)),
                ('vl_rec_proprio_srv', administracao.models.sicor_operacoes.UnboundedNumericField(blank=True, null=True)),
                ('vl_area_financ', administracao.models.sicor_operacoes.UnboundedNumericField(blank=True, null=True)),
                ('cd_subprograma', models.TextField(blank=True, null=True)),
                ('vl_produtiv_obtida', administracao.models.sicor_operacoes.UnboundedNumericField(blank=True, null=True)),
                ('dt_fim_colheita', models.DateField(blank=True, null=True)),
                ('dt_fim_plantio', models.DateField(blank=True, null=True)),
                ('dt_inic_colheita', models.DateField(blank=True, null=True)),
                ('dt_inic_plantio', models.DateField(blank=True, null=True)),
                ('vl_juros_enc_finan_posfix', administracao.models.sicor_operacoes.UnboundedNumericField(blank=True, null=True)),
                ('vl_perc_custo_efet_total', administracao.models.sicor_operacoes.UnboundedNumericField(blank=True, null=True)),
                ('cd_contrato_stn', models.TextField(blank=True, null=True)),
                ('cd_cnpj_cadastrante', models.TextField(blank=True, null=True)),
                ('vl_area_informada', administracao.models.sicor_operacoes.UnboundedNumericField(blank=True, null=True)),
                ('cd_ciclo_cultivar', models.TextField(blank=True, null=True)),
                ('cd_tipo_solo', models.TextField(blank=True, null=True)),
                ('pc_bonus_car', administracao.models.sicor_operacoes.UnboundedNumericField(blank=True, null=True)),
            ],
            options={
                'db_table': '"dados_sicor"."sicor_operacoes"',
                'verbose_name': 'operação SICOR',
                'verbose_name_plural': 'operações SICOR',
                'indexes': [
                    models.Index(fields=['ref_bacen'], name='sicor_operacoes_ref_idx'),
                    models.Index(fields=['ref_bacen', 'nu_ordem'], name='sicor_operacoes_ref_ord_idx'),
                    models.Index(fields=['ano_arquivo'], name='sicor_operacoes_ano_idx'),
                ],
            },
        ),
    ]
