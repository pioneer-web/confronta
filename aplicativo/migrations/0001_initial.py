# Generated manually for CONFRONTA Módulo 2 v0.3.0.
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='PerfilCliente',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('plano', models.CharField(choices=[('BASICO', 'Básico'), ('TOTAL', 'Total')], db_index=True, default='BASICO', max_length=10)),
                ('ativo', models.BooleanField(db_index=True, default=True)),
                ('criado_em', models.DateTimeField(auto_now_add=True)),
                ('atualizado_em', models.DateTimeField(auto_now=True)),
                ('usuario', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='perfil_cliente', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'perfil de cliente',
                'verbose_name_plural': 'perfis de clientes',
                'ordering': ['usuario__email'],
            },
        ),
    ]
