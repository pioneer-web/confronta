from django.db import migrations


RECURSOS = '\\n'.join([
    'Consulta e inteligência por CAR',
    'Visualização das camadas SICAR disponíveis',
    'Alertas de sobreposição territorial',
    'Embargos ambientais IBAMA e ICMBio',
    'Ocorrências PRODES disponíveis',
    'APAs nas bases CNUC e ICMBio disponíveis',
    'Terras Indígenas — FUNAI',
    'Assentamentos e territórios quilombolas — INCRA',
    'SICOR — glebas de crédito rural disponíveis',
    'Mapa, desenho de glebas, medição e exportações disponíveis',
])


def atualizar(apps, schema_editor):
    PlanoComercial = apps.get_model('aplicativo', 'PlanoComercial')
    PlanoComercial.objects.filter(
        slug='confronta'
    ).update(
        recursos=RECURSOS,
        descricao=(
            'Consultas territoriais por CAR, alertas, mapa e ferramentas '
            'geoespaciais disponíveis no CONFRONTA.'
        ),
    )


class Migration(migrations.Migration):

    dependencies = [
        ('aplicativo', '0009_sessao_unica_cliente'),
    ]

    operations = [
        migrations.RunPython(atualizar, migrations.RunPython.noop),
    ]
