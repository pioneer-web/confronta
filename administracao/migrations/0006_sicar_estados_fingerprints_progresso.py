from django.db import migrations, models
import django.db.models.deletion


UF_CODES = [
    'AC','AL','AP','AM','BA','CE','DF','ES','GO','MA','MT','MS','MG','PA',
    'PB','PR','PE','PI','RJ','RN','RS','RO','RR','SC','SP','SE','TO',
]


def criar_estados(apps, schema_editor):
    SicarEstado = apps.get_model('administracao', 'SicarEstado')
    SicarFingerprintCamada = apps.get_model('administracao', 'SicarFingerprintCamada')
    Importacao = apps.get_model('administracao', 'Importacao')
    estados = {}
    for uf in UF_CODES:
        estados[uf], _ = SicarEstado.objects.get_or_create(uf=uf)

    # Só atribui histórico antigo quando a própria importação confirma uma única UF.
    # Importações nacionais/multUF permanecem sem atribuição estadual para não inventar dados.
    qs = Importacao.objects.filter(fonte='SICAR', status='CONCLUIDO').order_by('data_inicio')
    for imp in qs.iterator():
        contexto = imp.contexto or {}
        uf = str(contexto.get('uf') or '').strip().upper()
        if uf not in estados:
            detection = (imp.resultado or {}).get('ufs_sicar_detectadas') or {}
            detectadas = [str(v).strip().upper() for v in detection.get('detectadas', []) if str(v).strip().upper() in estados]
            uf = detectadas[0] if len(set(detectadas)) == 1 else ''
        if uf not in estados:
            continue
        when = imp.data_finalizacao or imp.data_inicio
        state = estados[uf]
        if not state.ultima_atualizacao or (when and when > state.ultima_atualizacao):
            state.ultima_atualizacao = when
            state.ultima_verificacao = when
            state.status = 'ATUALIZADO'
            state.detalhes = {'origem': 'HISTORICO_CONFIRMADO', 'importacao_id': imp.pk}
            state.save(update_fields=['ultima_atualizacao','ultima_verificacao','status','detalhes'])

        # O conteúdo vetorial antigo não é recalculado durante a migration. Porém,
        # quando UF + dataset estão confirmados, o SHA-256 do ZIP histórico pode ser
        # reutilizado como atalho exato (byte a byte) na primeira rotina mensal.
        # hash_conteudo permanece vazio até uma análise v0.3.7 estabelecer o
        # fingerprint independente do empacotamento.
        if imp.dataset_slug and imp.hash_sha256 and when:
            fp, created = SicarFingerprintCamada.objects.get_or_create(
                uf=uf,
                dataset_slug=imp.dataset_slug,
                defaults={
                    'hash_conteudo': '',
                    'hash_arquivo': imp.hash_sha256,
                    'ultima_verificacao': when,
                    'ultima_atualizacao': when,
                    'ultima_importacao_id': imp.pk,
                },
            )
            if not created and (not fp.ultima_atualizacao or when > fp.ultima_atualizacao):
                fp.hash_arquivo = imp.hash_sha256
                fp.ultima_verificacao = when
                fp.ultima_atualizacao = when
                fp.ultima_importacao_id = imp.pk
                fp.save(update_fields=[
                    'hash_arquivo','ultima_verificacao','ultima_atualizacao','ultima_importacao'
                ])


class Migration(migrations.Migration):
    dependencies = [
        ('administracao', '0005_audita_classificacao_lotes_legados'),
    ]

    operations = [
        migrations.AlterField(
            model_name='importacao', name='status',
            field=models.CharField(choices=[('RECEBIDO','Recebido'),('VALIDANDO','Validando segurança'),('REJEITADO_SEGURANCA','Rejeitado por segurança'),('VALIDANDO_IDENTIDADE','Validando identidade do dataset'),('REJEITADO_IDENTIDADE','Dataset não confirmado'),('VALIDANDO_GIS','Validando GIS'),('IMPORTANDO','Importando'),('CONCLUIDO','Concluído'),('IGNORADO_DUPLICADO','Ignorado — já importado'),('SEM_ALTERACAO','Verificado — sem alteração'),('FALHOU','Falhou')], db_index=True, default='RECEBIDO', max_length=40),
        ),
        migrations.AlterField(
            model_name='loteimportacao', name='status',
            field=models.CharField(choices=[('RECEBIDO','Recebido'),('PREPARANDO','Preparando fila'),('ANALISANDO','Analisando arquivos'),('AGUARDANDO_CONFIRMACAO','Aguardando confirmação'),('PROCESSANDO','Importando alterações'),('CONCLUIDO','Concluído'),('CONCLUIDO_COM_PENDENCIAS','Concluído com pendências'),('FALHOU','Falhou')], db_index=True, default='RECEBIDO', max_length=40),
        ),
        migrations.AlterField(
            model_name='itemloteimportacao', name='status',
            field=models.CharField(choices=[('PENDENTE','Pendente'),('PROCESSANDO','Processando'),('PRONTO_IMPORTAR','Alteração detectada'),('CONCLUIDO','Concluído'),('IGNORADO_DUPLICADO','Ignorado — já importado'),('SEM_ALTERACAO','Sem alteração'),('REQUER_REVISAO','Requer revisão'),('FALHOU','Falhou')], db_index=True, default='PENDENTE', max_length=40),
        ),
        migrations.AddField(
            model_name='itemloteimportacao', name='etapa',
            field=models.CharField(blank=True, max_length=160),
        ),
        migrations.AddField(
            model_name='itemloteimportacao', name='fingerprint_conteudo',
            field=models.CharField(blank=True, db_index=True, max_length=64),
        ),
        migrations.AddField(
            model_name='itemloteimportacao', name='hash_sha256',
            field=models.CharField(blank=True, db_index=True, max_length=64),
        ),
        migrations.AddField(
            model_name='itemloteimportacao', name='progresso',
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.CreateModel(
            name='SicarEstado',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('uf', models.CharField(db_index=True, max_length=2, unique=True)),
                ('status', models.CharField(choices=[('NUNCA_IMPORTADO','Nunca importado'),('EM_FILA','Em fila'),('PROCESSANDO','Processando'),('ATUALIZADO','Atualizado'),('SEM_ALTERACAO','Sem alteração'),('ATENCAO','Atenção'),('FALHOU','Falhou')], db_index=True, default='NUNCA_IMPORTADO', max_length=30)),
                ('ultima_verificacao', models.DateTimeField(blank=True, null=True)),
                ('ultima_atualizacao', models.DateTimeField(blank=True, null=True)),
                ('detalhes', models.JSONField(blank=True, default=dict)),
                ('atualizado_em', models.DateTimeField(auto_now=True)),
                ('ultimo_lote', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='estados_sicar', to='administracao.loteimportacao')),
            ],
            options={'ordering':['uf'], 'verbose_name':'estado SICAR', 'verbose_name_plural':'estados SICAR'},
        ),
        migrations.CreateModel(
            name='SicarFingerprintCamada',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('uf', models.CharField(db_index=True, max_length=2)),
                ('dataset_slug', models.CharField(db_index=True, max_length=100)),
                ('hash_conteudo', models.CharField(db_index=True, max_length=64)),
                ('hash_arquivo', models.CharField(blank=True, db_index=True, max_length=64)),
                ('ultima_verificacao', models.DateTimeField()),
                ('ultima_atualizacao', models.DateTimeField(blank=True, null=True)),
                ('ultima_importacao', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='fingerprints_sicar', to='administracao.importacao')),
            ],
            options={'ordering':['uf','dataset_slug'], 'verbose_name':'fingerprint de camada SICAR', 'verbose_name_plural':'fingerprints de camadas SICAR'},
        ),
        migrations.AddConstraint(
            model_name='sicarfingerprintcamada',
            constraint=models.UniqueConstraint(fields=('uf','dataset_slug'), name='uniq_sicar_fingerprint_uf_dataset'),
        ),
        migrations.AddIndex(
            model_name='sicarfingerprintcamada',
            index=models.Index(fields=['uf','dataset_slug'], name='idx_sicar_fp_uf_dataset'),
        ),
        migrations.RunPython(criar_estados, migrations.RunPython.noop),
    ]
