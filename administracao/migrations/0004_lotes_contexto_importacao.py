from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('administracao', '0003_alerta_geometria_corrigida'),
    ]

    operations = [
        migrations.AddField(
            model_name='importacao',
            name='contexto',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AlterField(
            model_name='importacao',
            name='fonte',
            field=models.CharField(choices=[('SICAR','SICAR'),('IBAMA','IBAMA'),('ICMBIO','ICMBio'),('CNUC','CNUC'),('PRODES','INPE / PRODES'),('INCRA','INCRA')], db_index=True, max_length=20),
        ),
        migrations.AlterField(
            model_name='camadaimportada',
            name='fonte',
            field=models.CharField(choices=[('SICAR','SICAR'),('IBAMA','IBAMA'),('ICMBIO','ICMBio'),('CNUC','CNUC'),('PRODES','INPE / PRODES'),('INCRA','INCRA')], db_index=True, max_length=20),
        ),
        migrations.AlterField(
            model_name='alerta',
            name='fonte',
            field=models.CharField(choices=[('SICAR','SICAR'),('IBAMA','IBAMA'),('ICMBIO','ICMBio'),('CNUC','CNUC'),('PRODES','INPE / PRODES'),('INCRA','INCRA')], db_index=True, max_length=20),
        ),
        migrations.AlterModelOptions(
            name='camadaimportada',
            options={'ordering': ['fonte','dataset_slug','nome_tabela'], 'verbose_name': 'camada importada', 'verbose_name_plural': 'camadas importadas'},
        ),
        migrations.AlterField(
            model_name='importacao',
            name='status',
            field=models.CharField(
                choices=[
                    ('RECEBIDO', 'Recebido'),
                    ('VALIDANDO', 'Validando segurança'),
                    ('REJEITADO_SEGURANCA', 'Rejeitado por segurança'),
                    ('VALIDANDO_IDENTIDADE', 'Validando identidade do dataset'),
                    ('REJEITADO_IDENTIDADE', 'Dataset não confirmado'),
                    ('VALIDANDO_GIS', 'Validando GIS'),
                    ('IMPORTANDO', 'Importando'),
                    ('CONCLUIDO', 'Concluído'),
                    ('IGNORADO_DUPLICADO', 'Ignorado — já importado'),
                    ('FALHOU', 'Falhou'),
                ],
                db_index=True,
                default='RECEBIDO',
                max_length=40,
            ),
        ),
        migrations.CreateModel(
            name='LoteImportacao',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('fonte', models.CharField(choices=[('SICAR','SICAR'),('IBAMA','IBAMA'),('ICMBIO','ICMBio'),('CNUC','CNUC'),('PRODES','INPE / PRODES'),('INCRA','INCRA')], db_index=True, max_length=20)),
                ('nome_arquivo_original', models.CharField(max_length=255)),
                ('hash_sha256', models.CharField(db_index=True, max_length=64)),
                ('tamanho_bytes', models.BigIntegerField()),
                ('status', models.CharField(choices=[('RECEBIDO','Recebido'),('PREPARANDO','Preparando fila'),('PROCESSANDO','Processando'),('CONCLUIDO','Concluído'),('CONCLUIDO_COM_PENDENCIAS','Concluído com pendências'),('FALHOU','Falhou')], db_index=True, default='RECEBIDO', max_length=40)),
                ('data_inicio', models.DateTimeField(auto_now_add=True)),
                ('data_finalizacao', models.DateTimeField(blank=True, null=True)),
                ('resultado', models.JSONField(blank=True, default=dict)),
                ('motivo_falha', models.TextField(blank=True)),
                ('quarantine_path', models.CharField(blank=True, max_length=500)),
                ('extracted_path', models.CharField(blank=True, max_length=500)),
                ('administrador', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='lotes_importacao', to=settings.AUTH_USER_MODEL)),
            ],
            options={'verbose_name': 'lote de importação', 'verbose_name_plural': 'lotes de importação', 'ordering': ['-data_inicio']},
        ),
        migrations.CreateModel(
            name='ItemLoteImportacao',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('caminho_relativo', models.CharField(max_length=700)),
                ('nome_arquivo', models.CharField(max_length=255)),
                ('uf', models.CharField(blank=True, db_index=True, max_length=2)),
                ('dataset_slug', models.CharField(blank=True, db_index=True, max_length=100)),
                ('dataset_label', models.CharField(blank=True, max_length=255)),
                ('status', models.CharField(choices=[('PENDENTE','Pendente'),('PROCESSANDO','Processando'),('CONCLUIDO','Concluído'),('IGNORADO_DUPLICADO','Ignorado — já importado'),('REQUER_REVISAO','Requer revisão'),('FALHOU','Falhou')], db_index=True, default='PENDENTE', max_length=40)),
                ('motivo', models.TextField(blank=True)),
                ('criado_em', models.DateTimeField(auto_now_add=True)),
                ('iniciado_em', models.DateTimeField(blank=True, null=True)),
                ('finalizado_em', models.DateTimeField(blank=True, null=True)),
                ('importacao', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='itens_lote', to='administracao.importacao')),
                ('lote', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='itens', to='administracao.loteimportacao')),
            ],
            options={'verbose_name': 'item de lote de importação', 'verbose_name_plural': 'itens de lote de importação', 'ordering': ['id']},
        ),
        migrations.AddConstraint(
            model_name='itemloteimportacao',
            constraint=models.UniqueConstraint(fields=('lote','caminho_relativo'), name='uniq_item_caminho_lote'),
        ),
        migrations.AddIndex(
            model_name='itemloteimportacao',
            index=models.Index(fields=['lote','status'], name='idx_lote_item_status'),
        ),
    ]
