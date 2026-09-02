import django.contrib.auth.models
import django.contrib.auth.validators
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import administracao.models.user


class Migration(migrations.Migration):
    initial = True
    dependencies = [('auth', '0012_alter_user_first_name_max_length')]

    operations = [
        migrations.CreateModel(
            name='User',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('password', models.CharField(max_length=128, verbose_name='password')),
                ('last_login', models.DateTimeField(blank=True, null=True, verbose_name='last login')),
                ('is_superuser', models.BooleanField(default=False, help_text='Designates that this user has all permissions without explicitly assigning them.', verbose_name='superuser status')),
                ('first_name', models.CharField(blank=True, max_length=150, verbose_name='first name')),
                ('last_name', models.CharField(blank=True, max_length=150, verbose_name='last name')),
                ('is_staff', models.BooleanField(default=False, help_text='Designates whether the user can log into this admin site.', verbose_name='staff status')),
                ('is_active', models.BooleanField(default=True, help_text='Designates whether this user should be treated as active.', verbose_name='active')),
                ('date_joined', models.DateTimeField(default=django.utils.timezone.now, verbose_name='date joined')),
                ('email', models.EmailField(max_length=254, unique=True, verbose_name='e-mail')),
                ('role', models.CharField(blank=True, choices=[('ADMIN_TOTAL', 'Administrador Total'), ('ADMIN_JUNIOR', 'Administrador Júnior')], max_length=20, null=True, verbose_name='nível administrativo')),
                ('groups', models.ManyToManyField(blank=True, help_text='The groups this user belongs to.', related_name='user_set', related_query_name='user', to='auth.group', verbose_name='groups')),
                ('user_permissions', models.ManyToManyField(blank=True, help_text='Specific permissions for this user.', related_name='user_set', related_query_name='user', to='auth.permission', verbose_name='user permissions')),
            ],
            options={'verbose_name': 'administrador', 'verbose_name_plural': 'administradores', 'swappable': 'AUTH_USER_MODEL'},
            managers=[('objects', administracao.models.user.UserManager())],
        ),
        migrations.CreateModel(
            name='Importacao',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('fonte', models.CharField(choices=[('SICAR','SICAR'),('IBAMA','IBAMA'),('ICMBIO','ICMBio'),('PRODES','INPE / PRODES'),('INCRA','INCRA')], db_index=True, max_length=20)),
                ('nome_arquivo_original', models.CharField(max_length=255)),
                ('hash_sha256', models.CharField(db_index=True, max_length=64)),
                ('tamanho_bytes', models.BigIntegerField()),
                ('status', models.CharField(choices=[('RECEBIDO','Recebido'),('VALIDANDO','Validando segurança'),('REJEITADO_SEGURANCA','Rejeitado por segurança'),('VALIDANDO_GIS','Validando GIS'),('IMPORTANDO','Importando'),('CONCLUIDO','Concluído'),('FALHOU','Falhou')], db_index=True, default='RECEBIDO', max_length=30)),
                ('data_inicio', models.DateTimeField(auto_now_add=True)),
                ('data_finalizacao', models.DateTimeField(blank=True, null=True)),
                ('resultado', models.JSONField(blank=True, default=dict)),
                ('motivo_rejeicao', models.TextField(blank=True)),
                ('quarantine_path', models.CharField(blank=True, max_length=500)),
                ('administrador', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='importacoes', to=settings.AUTH_USER_MODEL)),
            ],
            options={'verbose_name': 'importação', 'verbose_name_plural': 'importações', 'ordering': ['-data_inicio']},
        ),
        migrations.CreateModel(
            name='CamadaImportada',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('fonte', models.CharField(choices=[('SICAR','SICAR'),('IBAMA','IBAMA'),('ICMBIO','ICMBio'),('PRODES','INPE / PRODES'),('INCRA','INCRA')], db_index=True, max_length=20)),
                ('nome_original', models.CharField(max_length=255)),
                ('schema_banco', models.CharField(max_length=63)),
                ('nome_tabela', models.CharField(max_length=63)),
                ('tipo_geometria', models.CharField(blank=True, max_length=100)),
                ('srid', models.IntegerField(blank=True, null=True)),
                ('assinatura_estrutura', models.CharField(db_index=True, max_length=64)),
                ('primeira_importacao', models.DateTimeField()),
                ('ultima_importacao', models.DateTimeField()),
                ('status', models.CharField(choices=[('ATIVA','Ativa'),('NAO_ENCONTRADA','Não encontrada'),('PENDENTE_REVISAO','Pendente de revisão'),('REMOVIDA','Removida')], db_index=True, default='ATIVA', max_length=30)),
                ('data_sem_uso', models.DateTimeField(blank=True, null=True)),
                ('ultima_importacao_ref', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='camadas', to='administracao.importacao')),
            ],
            options={'verbose_name': 'camada importada', 'verbose_name_plural': 'camadas importadas', 'ordering': ['fonte','nome_tabela']},
        ),
        migrations.AddConstraint(
            model_name='camadaimportada',
            constraint=models.UniqueConstraint(fields=('fonte','schema_banco','nome_tabela'), name='uniq_camada_fonte_schema_tabela'),
        ),
        migrations.CreateModel(
            name='Alerta',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('tipo', models.CharField(choices=[('TABELA_NAO_UTILIZADA','Tabela não utilizada'),('ALTERACAO_ESTRUTURAL','Alteração estrutural')], max_length=40)),
                ('fonte', models.CharField(choices=[('SICAR','SICAR'),('IBAMA','IBAMA'),('ICMBIO','ICMBio'),('PRODES','INPE / PRODES'),('INCRA','INCRA')], db_index=True, max_length=20)),
                ('mensagem', models.TextField()),
                ('ativo', models.BooleanField(db_index=True, default=True)),
                ('criado_em', models.DateTimeField(auto_now_add=True)),
                ('resolvido_em', models.DateTimeField(blank=True, null=True)),
                ('camada', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='alertas', to='administracao.camadaimportada')),
                ('resolvido_por', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='alertas_resolvidos', to=settings.AUTH_USER_MODEL)),
            ],
            options={'verbose_name': 'alerta', 'verbose_name_plural': 'alertas', 'ordering': ['-criado_em']},
        ),
        migrations.CreateModel(
            name='Auditoria',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('acao', models.CharField(db_index=True, max_length=100)),
                ('entidade', models.CharField(max_length=100)),
                ('identificador', models.CharField(max_length=255)),
                ('detalhes', models.JSONField(blank=True, default=dict)),
                ('criado_em', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('usuario', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='auditorias', to=settings.AUTH_USER_MODEL)),
            ],
            options={'verbose_name': 'registro de auditoria', 'verbose_name_plural': 'registros de auditoria', 'ordering': ['-criado_em']},
        ),
    ]
