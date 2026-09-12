from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('aplicativo', '0007_comunicacao_e_atendimento'),
    ]

    operations = [
        migrations.RenameIndex(
            model_name='mensagematendimento',
            old_name='aplicativo_m_atendim_4d4e7c_idx',
            new_name='aplicativo__atendim_aad25f_idx',
        ),
        migrations.RenameIndex(
            model_name='mensagematendimento',
            old_name='aplicativo_m_atendim_6a87a1_idx',
            new_name='aplicativo__atendim_7f21e3_idx',
        ),
    ]
