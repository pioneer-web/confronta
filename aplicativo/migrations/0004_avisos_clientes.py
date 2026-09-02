from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('aplicativo', '0003_planos_comerciais_clientes'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='AvisoCliente',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('mensagem', models.TextField()),
                ('ativo', models.BooleanField(db_index=True, default=True)),
                ('criado_em', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('criado_por', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='avisos_clientes_criados', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'aviso aos clientes',
                'verbose_name_plural': 'avisos aos clientes',
                'ordering': ['-criado_em', '-id'],
            },
        ),
        migrations.CreateModel(
            name='LeituraAvisoCliente',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('lido_em', models.DateTimeField(auto_now_add=True)),
                ('aviso', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='leituras', to='aplicativo.avisocliente')),
                ('usuario', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='leituras_avisos_clientes', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'leitura de aviso aos clientes',
                'verbose_name_plural': 'leituras de avisos aos clientes',
                'ordering': ['-lido_em'],
                'indexes': [models.Index(fields=['usuario', 'aviso'], name='idx_aviso_leitura_user')],
                'constraints': [models.UniqueConstraint(fields=('aviso', 'usuario'), name='uniq_leitura_aviso_cliente_usuario')],
            },
        ),
    ]
