from django.db import models


class SicorInstituicao(models.Model):
    cnpj_if = models.TextField(primary_key=True)
    nome_if = models.TextField()
    segmento_if = models.TextField(blank=True, default='')

    class Meta:
        db_table = '"dados_sicor"."sicor_instituicoes"'
        verbose_name = 'instituição financeira SICOR'
        verbose_name_plural = 'instituições financeiras SICOR'


class SicorPrograma(models.Model):
    cd_programa = models.TextField(primary_key=True)
    descricao = models.TextField()
    data_inicio = models.DateField(null=True, blank=True)
    data_fim = models.DateField(null=True, blank=True)
    financiamento = models.TextField(blank=True, default='')

    class Meta:
        db_table = '"dados_sicor"."sicor_programas"'
        verbose_name = 'programa SICOR'
        verbose_name_plural = 'programas SICOR'
