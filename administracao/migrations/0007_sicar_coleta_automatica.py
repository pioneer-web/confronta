from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('administracao', '0006_sicar_estados_fingerprints_progresso'),
    ]

    operations = [
        migrations.CreateModel(
            name='SicarColetaAutomatica',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('uf', models.CharField(db_index=True, default='PE', max_length=2)),
                ('dataset_slug', models.CharField(db_index=True, default='sicar-perimetros', max_length=100)),
                ('origem', models.CharField(choices=[('MANUAL', 'Manual'), ('AGENDADA', 'Agendada')], db_index=True, default='MANUAL', max_length=20)),
                ('status', models.CharField(choices=[('AGUARDANDO_FILA', 'Aguardando na fila'), ('BAIXANDO', 'Baixando do SICAR'), ('AGUARDANDO_IMPORTACAO', 'Aguardando importação'), ('IMPORTANDO', 'Importando'), ('SEM_ALTERACAO', 'Sem alteração'), ('CONCLUIDO', 'Concluído'), ('ATENCAO', 'Concluído com pendências'), ('FALHOU', 'Falhou')], db_index=True, default='AGUARDANDO_FILA', max_length=40)),
                ('data_agendada', models.DateField(blank=True, db_index=True, null=True)),
                ('criado_em', models.DateTimeField(auto_now_add=True)),
                ('iniciado_em', models.DateTimeField(blank=True, null=True)),
                ('finalizado_em', models.DateTimeField(blank=True, null=True)),
                ('detalhes', models.JSONField(blank=True, default=dict)),
                ('erro', models.TextField(blank=True)),
                ('lote', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='coletas_sicar_automaticas', to='administracao.loteimportacao')),
                ('solicitado_por', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='coletas_sicar_solicitadas', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'coleta automática SICAR',
                'verbose_name_plural': 'coletas automáticas SICAR',
                'ordering': ['-criado_em'],
            },
        ),
        migrations.AddIndex(
            model_name='sicarcoletaautomatica',
            index=models.Index(fields=['status', 'criado_em'], name='idx_sicar_coleta_fila'),
        ),
        migrations.AddIndex(
            model_name='sicarcoletaautomatica',
            index=models.Index(fields=['uf', '-criado_em'], name='idx_sicar_coleta_uf_data'),
        ),
    ]
