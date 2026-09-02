from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('administracao', '0009_fonte_sincronizacao'),
    ]

    operations = [
        migrations.AlterField(
            model_name='loteimportacao',
            name='status',
            field=models.CharField(
                choices=[
                    ('RECEBIDO', 'Recebido'),
                    ('PREPARANDO', 'Preparando fila'),
                    ('ANALISANDO', 'Analisando arquivos'),
                    ('AGUARDANDO_CONFIRMACAO', 'Aguardando confirmação'),
                    ('PROCESSANDO', 'Importando alterações'),
                    ('INTERROMPENDO', 'Interrupção solicitada'),
                    ('INTERROMPIDO', 'Interrompido'),
                    ('CONCLUIDO', 'Concluído'),
                    ('CONCLUIDO_COM_PENDENCIAS', 'Concluído com pendências'),
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
                    ('INTERROMPIDO', 'Interrompido'),
                    ('FALHOU', 'Falhou'),
                ],
                db_index=True,
                default='AGUARDANDO_FILA',
                max_length=40,
            ),
        ),
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
    ]
