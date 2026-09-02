from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('administracao','0001_initial')]
    operations = [
        migrations.AddField(model_name='importacao', name='dataset_slug', field=models.CharField(db_index=True, default='', max_length=100)),
        migrations.AddField(model_name='importacao', name='dataset_label', field=models.CharField(blank=True, max_length=255)),
        migrations.AddField(model_name='importacao', name='identidade_status', field=models.CharField(db_index=True, default='PENDENTE', max_length=30)),
        migrations.AddField(model_name='importacao', name='identidade_relatorio', field=models.JSONField(blank=True, default=dict)),
        migrations.AddField(model_name='camadaimportada', name='dataset_slug', field=models.CharField(db_index=True, default='', max_length=100)),
        migrations.AddField(model_name='camadaimportada', name='tabela_raw', field=models.CharField(blank=True, max_length=63)),
        migrations.RemoveConstraint(model_name='camadaimportada', name='uniq_camada_fonte_schema_tabela'),
        migrations.AddConstraint(model_name='camadaimportada', constraint=models.UniqueConstraint(fields=('fonte','dataset_slug','schema_banco','nome_tabela'), name='uniq_camada_dataset_tabela')),
        migrations.AddIndex(model_name='importacao', index=models.Index(fields=['fonte','dataset_slug','-data_inicio'], name='idx_import_dataset_data')),
        migrations.AlterField(model_name='importacao', name='status', field=models.CharField(choices=[('RECEBIDO','Recebido'),('VALIDANDO','Validando segurança'),('REJEITADO_SEGURANCA','Rejeitado por segurança'),('VALIDANDO_IDENTIDADE','Validando identidade do dataset'),('REJEITADO_IDENTIDADE','Dataset não confirmado'),('VALIDANDO_GIS','Validando GIS'),('IMPORTANDO','Importando'),('CONCLUIDO','Concluído'),('FALHOU','Falhou')], db_index=True, default='RECEBIDO', max_length=40)),
    ]
