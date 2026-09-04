from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ('aplicativo', '0006_plano_confronta_lancamento'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='avisocliente',
            name='destinatario',
            field=models.ForeignKey(
                blank=True,
                help_text='Em branco, o aviso é exibido para todos os clientes.',
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='avisos_clientes_recebidos',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.CreateModel(
            name='AtendimentoCliente',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(choices=[('ABERTO', 'Aberto'), ('EM_ATENDIMENTO', 'Em atendimento'), ('ENCERRADO', 'Encerrado')], db_index=True, default='ABERTO', max_length=20)),
                ('criado_em', models.DateTimeField(auto_now_add=True)),
                ('atualizado_em', models.DateTimeField(auto_now=True)),
                ('ultima_interacao_em', models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ('atendente', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='atendimentos_assumidos', to=settings.AUTH_USER_MODEL)),
                ('cliente', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='atendimento_cliente', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'atendimento ao cliente',
                'verbose_name_plural': 'atendimentos aos clientes',
                'ordering': ['-ultima_interacao_em', '-id'],
            },
        ),
        migrations.CreateModel(
            name='MensagemAtendimento',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('texto', models.TextField()),
                ('criado_em', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('lida_em', models.DateTimeField(blank=True, null=True)),
                ('atendimento', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='mensagens', to='aplicativo.atendimentocliente')),
                ('autor', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='mensagens_atendimento', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'mensagem de atendimento',
                'verbose_name_plural': 'mensagens de atendimento',
                'ordering': ['criado_em', 'id'],
            },
        ),
        migrations.AddIndex(
            model_name='mensagematendimento',
            index=models.Index(fields=['atendimento', 'criado_em'], name='aplicativo_m_atendim_4d4e7c_idx'),
        ),
        migrations.AddIndex(
            model_name='mensagematendimento',
            index=models.Index(fields=['atendimento', 'lida_em'], name='aplicativo_m_atendim_6a87a1_idx'),
        ),
    ]
