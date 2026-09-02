from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('administracao', '0008_sicar_coleta_progresso'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='FonteSincronizacao',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('fonte_slug', models.CharField(db_index=True, max_length=20)),
                ('dataset_slug', models.CharField(db_index=True, max_length=100)),
                ('uf', models.CharField(blank=True, db_index=True, max_length=2)),
                ('origem', models.CharField(choices=[('AGENDADO', 'Automático'), ('MANUAL', 'Atualizar agora')], default='AGENDADO', max_length=20)),
                ('status', models.CharField(choices=[('AGUARDANDO', 'Aguardando na fila'), ('VERIFICANDO', 'Verificando fonte'), ('BAIXANDO', 'Baixando'), ('VALIDANDO', 'Validando'), ('IMPORTANDO', 'Importando'), ('CONCLUIDO', 'Atualizado'), ('SEM_ALTERACAO', 'Sem alteração'), ('FALHOU', 'Falhou')], db_index=True, default='AGUARDANDO', max_length=30)),
                ('progresso', models.PositiveSmallIntegerField(default=0)),
                ('etapa', models.CharField(blank=True, max_length=180)),
                ('assinatura_remota', models.CharField(blank=True, db_index=True, max_length=128)),
                ('bytes_baixados', models.BigIntegerField(default=0)),
                ('registros_fonte', models.BigIntegerField(blank=True, null=True)),
                ('novos', models.BigIntegerField(default=0)),
                ('alterados', models.BigIntegerField(default=0)),
                ('removidos', models.BigIntegerField(default=0)),
                ('detalhes', models.JSONField(blank=True, default=dict)),
                ('erro', models.TextField(blank=True)),
                ('criado_em', models.DateTimeField(auto_now_add=True)),
                ('iniciado_em', models.DateTimeField(blank=True, null=True)),
                ('finalizado_em', models.DateTimeField(blank=True, null=True)),
                ('ultima_atividade', models.DateTimeField(blank=True, null=True)),
                ('importacao', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='sincronizacoes_fontes', to='administracao.importacao')),
                ('solicitado_por', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='sincronizacoes_fontes', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-criado_em']},
        ),
        migrations.AddIndex(model_name='fontesincronizacao', index=models.Index(fields=['fonte_slug', 'dataset_slug', 'uf', '-criado_em'], name='idx_sync_fonte_dataset')),
        migrations.AddIndex(model_name='fontesincronizacao', index=models.Index(fields=['status', 'criado_em'], name='idx_sync_status_data')),
        migrations.AddConstraint(
            model_name='fontesincronizacao',
            constraint=models.UniqueConstraint(
                fields=('fonte_slug', 'dataset_slug', 'uf'),
                condition=models.Q(status__in=['AGUARDANDO', 'VERIFICANDO', 'BAIXANDO', 'VALIDANDO', 'IMPORTANDO']),
                name='uq_sync_fonte_ativa',
            ),
        ),
    ]
