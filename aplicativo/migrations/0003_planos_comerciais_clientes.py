from decimal import Decimal

from django.db import migrations, models
import django.db.models.deletion


def criar_planos_iniciais(apps, schema_editor):
    PlanoComercial = apps.get_model('aplicativo', 'PlanoComercial')
    PerfilCliente = apps.get_model('aplicativo', 'PerfilCliente')

    basico, _ = PlanoComercial.objects.get_or_create(
        slug='basico',
        defaults={
            'nome': 'Básico',
            'subtitulo': 'Consulta territorial essencial',
            'descricao': 'Para quem precisa consultar o imóvel rural e reunir informações territoriais em uma única plataforma.',
            'nivel_acesso': 'BASICO',
            'preco_mensal': Decimal('59.90'),
            'preco_anual': Decimal('718.80'),
            'recursos': '\n'.join([
                'Dados CAR',
                'INCRA / SIGEF',
                'Área e limites do imóvel',
                'Embargos IBAMA',
                'ICMBio e CNUC',
                'Camada PRODES',
                'Downloads territoriais disponíveis',
            ]),
            'texto_cta': 'Escolher Básico',
            'ativo': True,
            'ordem': 10,
        },
    )
    total, _ = PlanoComercial.objects.get_or_create(
        slug='total',
        defaults={
            'nome': 'Total',
            'subtitulo': 'Consulta + ferramentas de gleba',
            'descricao': 'Todos os recursos do Básico com as ferramentas avançadas para desenho, análise e exportação de glebas.',
            'nivel_acesso': 'TOTAL',
            'preco_mensal': Decimal('99.90'),
            'preco_anual': Decimal('1198.80'),
            'recursos': '\n'.join([
                'Todos os recursos do Básico',
                'Desenho e edição de glebas',
                'Cálculo aproximado de área',
                'Alertas de sobreposição',
                'Download individual das glebas',
                'Ferramentas territoriais avançadas',
            ]),
            'recursos_exclusivos': 'Área de trabalho completa para glebas',
            'destaque': True,
            'selo': 'Melhor custo-benefício',
            'texto_cta': 'Escolher Total',
            'ativo': True,
            'ordem': 20,
        },
    )

    PerfilCliente.objects.filter(plano='BASICO', plano_comercial__isnull=True).update(plano_comercial=basico)
    PerfilCliente.objects.filter(plano='TOTAL', plano_comercial__isnull=True).update(plano_comercial=total)
    PerfilCliente.objects.filter(plano_desejado='BASICO', plano_desejado_comercial__isnull=True).update(plano_desejado_comercial=basico)
    PerfilCliente.objects.filter(plano_desejado='TOTAL', plano_desejado_comercial__isnull=True).update(plano_desejado_comercial=total)


def remover_vinculos_planos(apps, schema_editor):
    PerfilCliente = apps.get_model('aplicativo', 'PerfilCliente')
    PerfilCliente.objects.update(plano_comercial=None, plano_desejado_comercial=None)


class Migration(migrations.Migration):
    dependencies = [
        ('aplicativo', '0002_cadastro_publico_e_sem_plano'),
    ]

    operations = [
        migrations.CreateModel(
            name='PlanoComercial',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('nome', models.CharField(max_length=80, unique=True)),
                ('slug', models.SlugField(max_length=90, unique=True)),
                ('subtitulo', models.CharField(blank=True, max_length=150)),
                ('descricao', models.TextField(blank=True)),
                ('nivel_acesso', models.CharField(choices=[('BASICO', 'Básico'), ('TOTAL', 'Total')], db_index=True, default='BASICO', help_text='Define o nível técnico liberado no mapa. Planos comerciais diferentes podem compartilhar o mesmo nível.', max_length=10)),
                ('preco_mensal', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=10)),
                ('preco_anual', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=10)),
                ('recursos', models.TextField(blank=True, help_text='Informe um recurso por linha. A lista será exibida automaticamente na Home e na tela de planos.')),
                ('recursos_exclusivos', models.TextField(blank=True, help_text='Opcional. Informe um recurso exclusivo por linha.')),
                ('destaque', models.BooleanField(default=False)),
                ('selo', models.CharField(blank=True, help_text='Ex.: Melhor custo-benefício', max_length=60)),
                ('texto_cta', models.CharField(default='Escolher plano', max_length=50)),
                ('ativo', models.BooleanField(db_index=True, default=True)),
                ('ordem', models.PositiveSmallIntegerField(db_index=True, default=10)),
                ('criado_em', models.DateTimeField(auto_now_add=True)),
                ('atualizado_em', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'plano comercial',
                'verbose_name_plural': 'planos comerciais',
                'ordering': ['ordem', 'preco_mensal', 'nome'],
            },
        ),
        migrations.AddField(
            model_name='perfilcliente',
            name='empresa',
            field=models.CharField(blank=True, max_length=150),
        ),
        migrations.AddField(
            model_name='perfilcliente',
            name='fim_acesso',
            field=models.DateField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name='perfilcliente',
            name='inicio_acesso',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='perfilcliente',
            name='observacoes_admin',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='perfilcliente',
            name='plano_comercial',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='clientes', to='aplicativo.planocomercial'),
        ),
        migrations.AddField(
            model_name='perfilcliente',
            name='plano_desejado_comercial',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='interessados', to='aplicativo.planocomercial'),
        ),
        migrations.AddField(
            model_name='perfilcliente',
            name='renovacao_automatica',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='perfilcliente',
            name='telefone',
            field=models.CharField(blank=True, max_length=25),
        ),
        migrations.RunPython(criar_planos_iniciais, remover_vinculos_planos),
    ]
