from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('administracao', '0011_lote_remocao_logica'),
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [
        migrations.AlterField(
            model_name='alerta',
            name='fonte',
            field=models.CharField(choices=[('SICAR', 'SICAR'), ('IBAMA', 'IBAMA'), ('ICMBIO', 'ICMBio'), ('CNUC', 'CNUC'), ('PRODES', 'INPE / PRODES'), ('INCRA', 'INCRA'), ('SICOR', 'SICOR / Crédito Rural'), ('SIGEF', 'SIGEF / INCRA'), ('SNCR', 'SNCR / INCRA'), ('FUNAI', 'FUNAI / Terras Indígenas'), ('FLORESTAS_PUBLICAS', 'Florestas Públicas'), ('DETER', 'INPE / DETER'), ('ANA', 'ANA / Outorgas'), ('ANM', 'ANM / Processos Minerários'), ('ZARC', 'ZARC'), ('MAPBIOMAS', 'MapBiomas'), ('FOCOS_CALOR', 'INPE / Focos de Calor')], db_index=True, max_length=20),
        ),
        migrations.AlterField(
            model_name='camadaimportada',
            name='fonte',
            field=models.CharField(choices=[('SICAR', 'SICAR'), ('IBAMA', 'IBAMA'), ('ICMBIO', 'ICMBio'), ('CNUC', 'CNUC'), ('PRODES', 'INPE / PRODES'), ('INCRA', 'INCRA'), ('SICOR', 'SICOR / Crédito Rural'), ('SIGEF', 'SIGEF / INCRA'), ('SNCR', 'SNCR / INCRA'), ('FUNAI', 'FUNAI / Terras Indígenas'), ('FLORESTAS_PUBLICAS', 'Florestas Públicas'), ('DETER', 'INPE / DETER'), ('ANA', 'ANA / Outorgas'), ('ANM', 'ANM / Processos Minerários'), ('ZARC', 'ZARC'), ('MAPBIOMAS', 'MapBiomas'), ('FOCOS_CALOR', 'INPE / Focos de Calor')], db_index=True, max_length=20),
        ),
        migrations.AlterField(
            model_name='importacao',
            name='fonte',
            field=models.CharField(choices=[('SICAR', 'SICAR'), ('IBAMA', 'IBAMA'), ('ICMBIO', 'ICMBio'), ('CNUC', 'CNUC'), ('PRODES', 'INPE / PRODES'), ('INCRA', 'INCRA'), ('SICOR', 'SICOR / Crédito Rural'), ('SIGEF', 'SIGEF / INCRA'), ('SNCR', 'SNCR / INCRA'), ('FUNAI', 'FUNAI / Terras Indígenas'), ('FLORESTAS_PUBLICAS', 'Florestas Públicas'), ('DETER', 'INPE / DETER'), ('ANA', 'ANA / Outorgas'), ('ANM', 'ANM / Processos Minerários'), ('ZARC', 'ZARC'), ('MAPBIOMAS', 'MapBiomas'), ('FOCOS_CALOR', 'INPE / Focos de Calor')], db_index=True, max_length=20),
        ),
        migrations.AlterField(
            model_name='loteimportacao',
            name='fonte',
            field=models.CharField(choices=[('SICAR', 'SICAR'), ('IBAMA', 'IBAMA'), ('ICMBIO', 'ICMBio'), ('CNUC', 'CNUC'), ('PRODES', 'INPE / PRODES'), ('INCRA', 'INCRA'), ('SICOR', 'SICOR / Crédito Rural'), ('SIGEF', 'SIGEF / INCRA'), ('SNCR', 'SNCR / INCRA'), ('FUNAI', 'FUNAI / Terras Indígenas'), ('FLORESTAS_PUBLICAS', 'Florestas Públicas'), ('DETER', 'INPE / DETER'), ('ANA', 'ANA / Outorgas'), ('ANM', 'ANM / Processos Minerários'), ('ZARC', 'ZARC'), ('MAPBIOMAS', 'MapBiomas'), ('FOCOS_CALOR', 'INPE / Focos de Calor')], db_index=True, max_length=20),
        ),
        migrations.AlterField(
            model_name='user',
            name='groups',
            field=models.ManyToManyField(blank=True, help_text='The groups this user belongs to. A user will get all permissions granted to each of their groups.', related_name='user_set', related_query_name='user', to='auth.group', verbose_name='groups'),
        ),
        migrations.AlterField(
            model_name='user',
            name='is_active',
            field=models.BooleanField(default=True, help_text='Designates whether this user should be treated as active. Unselect this instead of deleting accounts.', verbose_name='active'),
        ),
    ]
