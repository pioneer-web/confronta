from decimal import Decimal

from django.db import migrations


RECURSOS = '\n'.join([
    'Consulta e inteligência por CAR',
    'Alertas de sobreposição territorial',
    'Embargos ambientais IBAMA',
    'Desmatamento PRODES',
    'Unidades de Conservação e ICMBio',
    'Terras Indígenas e dados fundiários disponíveis',
    'Assentamentos e bases INCRA disponíveis',
    'SIGEF / SNCI nas bases publicadas',
    'SICOR — glebas de crédito rural disponíveis',
    'Mapa, ferramentas de gleba e exportações disponíveis',
])


def configurar_plano(apps, schema_editor):
    PlanoComercial = apps.get_model('aplicativo', 'PlanoComercial')

    plano = PlanoComercial.objects.filter(slug='confronta').first()
    if plano is None:
        plano = PlanoComercial.objects.filter(nome__iexact='CONFRONTA').first()
    if plano is None:
        plano = PlanoComercial()

    plano.slug = 'confronta'
    plano.nome = 'CONFRONTA'
    plano.subtitulo = 'Inteligência territorial completa'
    plano.descricao = 'Acesso às consultas, alertas, mapa e ferramentas territoriais disponíveis no CONFRONTA.'
    plano.nivel_acesso = 'TOTAL'
    plano.preco_mensal = Decimal('67.90')
    plano.preco_anual = Decimal('598.80')
    plano.recursos = RECURSOS
    plano.recursos_exclusivos = ''
    plano.destaque = True
    plano.selo = 'Preço de lançamento'
    plano.texto_cta = 'Assinar CONFRONTA'
    plano.ativo = True
    plano.ordem = 1
    plano.save()
    PlanoComercial.objects.exclude(pk=plano.pk).update(ativo=False)


def reverter(apps, schema_editor):
    PlanoComercial = apps.get_model('aplicativo', 'PlanoComercial')
    PlanoComercial.objects.filter(slug='confronta').update(ativo=False)
    PlanoComercial.objects.filter(slug__in=['basico', 'total']).update(ativo=True)


class Migration(migrations.Migration):
    dependencies = [('aplicativo', '0005_limite_seguranca')]
    operations = [migrations.RunPython(configurar_plano, reverter)]
