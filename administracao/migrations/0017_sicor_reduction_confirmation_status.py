from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('administracao', '0016_alter_alerta_fonte_alter_camadaimportada_fonte_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='importacao',
            name='status',
            field=models.CharField(
                choices=[
                    ('RECEBIDO', 'Recebido'),
                    ('VALIDANDO', 'Validando segurança'),
                    ('REJEITADO_SEGURANCA', 'Rejeitado por segurança'),
                    ('VALIDANDO_IDENTIDADE', 'Validando identidade do dataset'),
                    ('REJEITADO_IDENTIDADE', 'Dataset não confirmado'),
                    ('VALIDANDO_GIS', 'Validando GIS'),
                    ('IMPORTANDO', 'Importando'),
                    ('AGUARDANDO_CONFIRMACAO_REDUCAO', 'Aguardando confirmação de redução SICOR'),
                    ('CONCLUIDO', 'Concluído'),
                    ('IGNORADO_DUPLICADO', 'Ignorado — já importado'),
                    ('SEM_ALTERACAO', 'Verificado — sem alteração'),
                    ('INTERROMPIDO', 'Interrompido'),
                    ('FALHOU', 'Falhou'),
                ],
                db_index=True,
                default='RECEBIDO',
                max_length=40,
            ),
        ),
        migrations.AlterField(
            model_name='itemloteimportacao',
            name='status',
            field=models.CharField(
                choices=[
                    ('AGUARDANDO_FILA', 'Aguardando na fila'),
                    ('PENDENTE', 'Aguardando na fila'),
                    ('PROCESSANDO', 'Processando'),
                    ('PRONTO_IMPORTAR', 'Alteração detectada'),
                    ('CONCLUIDO', 'Concluído'),
                    ('IGNORADO_DUPLICADO', 'Ignorado — já importado'),
                    ('SEM_ALTERACAO', 'Sem alteração'),
                    ('REQUER_REVISAO', 'Requer revisão'),
                    ('AGUARDANDO_CONFIRMACAO_SICOR', 'Aguardando confirmação SICOR'),
                    ('INTERROMPIDO', 'Interrompido'),
                    ('FALHOU', 'Falhou'),
                ],
                db_index=True,
                default='AGUARDANDO_FILA',
                max_length=40,
            ),
        ),
    ]
