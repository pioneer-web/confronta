import unicodedata
from contextlib import nullcontext

from django.conf import settings
from django.db import DatabaseError, connection, transaction

from aplicativo.repositories import (
    CamadaIndisponivel,
    ImovelDuplicado,
    ImovelNaoEncontrado,
    RepositorioTerritorial,
)


class ConsultaCarErro(Exception):
    pass


class ConsultaCarService:
    """Orquestra a consulta territorial sem persistir resultados derivados.

    As bases oficiais permanecem nas tabelas operacionais do Módulo 1. Os
    cruzamentos CAR × fontes externas são calculados no PostGIS a cada consulta
    e entregues ao mapa/relatório, evitando caches territoriais que ficariam
    obsoletos após uma atualização mensal das fontes.
    """

    # Regra de negócio: para a análise de restrição relacionada a crédito rural,
    # somente ocorrências PRODES a partir de 2019 são consideradas. O histórico
    # anterior permanece disponível como dado territorial, sem ativar restrição.
    PRODES_ANO_INICIAL_RESTRICAO_CREDITO = 2019

    def __init__(self, repositorio=None):
        self.repositorio = repositorio or RepositorioTerritorial()

    @staticmethod
    def _aplicar_timeout_transacao():
        timeout_ms = max(0, int(getattr(settings, 'TERRITORIAL_QUERY_TIMEOUT_MS', 120000)))
        if timeout_ms:
            with connection.cursor() as cursor:
                # SET LOCAL vale apenas para a transação de leitura atual e não
                # interfere no worker/importador GIS.
                cursor.execute("SELECT set_config('statement_timeout', %s, true)", [str(timeout_ms)])

    def _contexto_leitura(self):
        # Repositórios injetados em testes/unidades não precisam abrir uma
        # transação apenas para configurar o PostgreSQL.
        if isinstance(self.repositorio, RepositorioTerritorial):
            return transaction.atomic()
        return nullcontext()

    def _configurar_timeout_se_real(self):
        if isinstance(self.repositorio, RepositorioTerritorial):
            self._aplicar_timeout_transacao()

    def validar_existencia(self, car):
        """Validação leve do CAR para evitar executar todo o confronto duas vezes."""
        try:
            with self._contexto_leitura():
                self._configurar_timeout_se_real()
                return self.repositorio.buscar_imovel_por_car(car)
        except (CamadaIndisponivel, ImovelNaoEncontrado, ImovelDuplicado) as exc:
            raise ConsultaCarErro(str(exc)) from exc
        except DatabaseError as exc:
            raise ConsultaCarErro(
                'Não foi possível consultar a base territorial neste momento. Verifique a integridade das tabelas operacionais.'
            ) from exc

    def localizar_por_coordenada(self, latitude, longitude):
        """Retorna CARs candidatos para uma coordenada WGS84, sem persistir nada."""
        try:
            with self._contexto_leitura():
                self._configurar_timeout_se_real()
                resultados = self.repositorio.buscar_cars_por_ponto(latitude, longitude)
        except CamadaIndisponivel as exc:
            raise ConsultaCarErro(str(exc)) from exc
        except DatabaseError as exc:
            raise ConsultaCarErro(
                'Não foi possível consultar a base territorial neste momento.'
            ) from exc

        if not resultados:
            raise ConsultaCarErro('Nenhum CAR foi localizado na coordenada informada.')
        return resultados

    def localizar_por_geometria(self, geometria):
        """Retorna CARs que possuem interseção de área real com a geometria WGS84."""
        try:
            with self._contexto_leitura():
                self._configurar_timeout_se_real()
                resultados = self.repositorio.buscar_cars_por_geojson(geometria)
        except (CamadaIndisponivel, ValueError) as exc:
            raise ConsultaCarErro(str(exc)) from exc
        except DatabaseError as exc:
            raise ConsultaCarErro(
                'Não foi possível consultar a base territorial neste momento.'
            ) from exc

        if not resultados:
            raise ConsultaCarErro('Nenhum CAR com sobreposição de área foi localizado para a gleba informada.')
        return resultados

    def executar(self, car):
        try:
            with self._contexto_leitura():
                self._configurar_timeout_se_real()
                imovel = self.repositorio.buscar_imovel_por_car(car)
                imovel['situacao_apresentacao'] = self._situacao_car_apresentacao(imovel)
                camadas = self.repositorio.buscar_camadas_sicar(imovel['cod_imovel'])
                analises_externas = self.repositorio.buscar_analises_externas(imovel['cod_imovel'])
                analises_externas['prodes'] = self._preparar_prodes(analises_externas.get('prodes'))
                outros_cars = self.repositorio.buscar_sobreposicoes_outros_cars(imovel['cod_imovel'])
        except (CamadaIndisponivel, ImovelNaoEncontrado, ImovelDuplicado) as exc:
            raise ConsultaCarErro(str(exc)) from exc
        except DatabaseError as exc:
            raise ConsultaCarErro(
                'Não foi possível consultar a base territorial neste momento. Verifique a integridade das tabelas operacionais.'
            ) from exc

        alertas = self._montar_alertas(analises_externas, outros_cars)
        camadas_externas = self._montar_camadas_externas(analises_externas, outros_cars)

        return {
            'imovel': imovel,
            'camadas': camadas,
            'analises_externas': analises_externas,
            'camadas_externas': camadas_externas,
            'outros_cars': outros_cars,
            'alertas': alertas,
            'restricoes': alertas['restricoes'],
        }

    @staticmethod
    def _normalizar_texto(value):
        texto = str(value or '').strip().upper()
        return ''.join(
            c for c in unicodedata.normalize('NFKD', texto)
            if not unicodedata.combining(c)
        )

    @classmethod
    def _situacao_car_apresentacao(cls, imovel):
        bruto = str(imovel.get('situacao_car') or imovel.get('condicao') or '').strip()
        normalizado = cls._normalizar_texto(bruto)
        if normalizado in {'AT', 'ATIVO', 'ATIVA'} or normalizado.startswith('ATIV'):
            return 'Ativo'
        if any(token in normalizado for token in ('INATIV', 'CANCEL', 'CANCELAD', 'BAIXAD')):
            return 'Inativo'
        # Pendente/suspenso/outros estados são preservados como publicados pela
        # fonte, em vez de classificá-los silenciosamente como ativo/inativo.
        return bruto or 'Não informada'

    @classmethod
    def _tipo_prodes(cls, registro):
        valor = cls._normalizar_texto(
            registro.get('main_class') or registro.get('tipo_prodes') or registro.get('class_name')
        )
        if 'QUEIM' in valor:
            return 'QUEIMADA'
        if 'DESMAT' in valor or 'SUPRESS' in valor:
            return 'DESMATAMENTO'
        if 'RESERVATOR' in valor:
            return 'RESERVATORIO'
        return valor or 'OCORRENCIA_PRODES'

    @classmethod
    def _preparar_prodes(cls, resultado):
        resultado = dict(resultado or {})
        if not resultado.get('disponivel'):
            return resultado

        registros = []
        for registro in resultado.get('registros', []):
            item = dict(registro)
            item['tipo_alerta'] = cls._tipo_prodes(item)
            registros.append(item)

        features = []
        for feature in resultado.get('features', []):
            props = dict(feature.get('properties') or {})
            props['tipo_alerta'] = cls._tipo_prodes(props)
            features.append({
                'type': 'Feature',
                'properties': props,
                'geometry': feature.get('geometry'),
            })

        resultado['registros'] = registros
        resultado['features'] = features
        resultado['quantidade'] = len(registros)
        resultado['anos'] = cls._valores_unicos_ordenados(r.get('year') for r in registros)
        resultado['tipos'] = cls._valores_unicos_ordenados(r.get('tipo_alerta') for r in registros)
        resultado['area_soma_ocorrencias_ha'] = round(sum(
            float(r.get('area_sobreposta_ha') or 0) for r in registros
            if isinstance(r.get('area_sobreposta_ha'), (int, float))
        ), 6)
        # Sem uma geometria oficial de cobertura da base não declaramos "NÃO HÁ"
        # como conclusão definitiva quando nenhuma ocorrência é encontrada.
        resultado['cobertura_status'] = (
            'COM_OCORRENCIAS' if registros else 'COBERTURA_NAO_CONFIRMADA'
        )
        return resultado

    @classmethod
    def _prodes_restricao_credito(cls, resultado):
        """Recorta PRODES para a regra de restrição de crédito rural.

        Todas as feições PRODES continuam disponíveis para visualização territorial.
        Para alerta/restrição, entram somente registros com ano >= 2019. Registros
        sem ano confirmado não ativam restrição por prudência e rastreabilidade.
        """
        resultado = dict(resultado or {})
        if not resultado.get('disponivel'):
            return resultado

        def ano_eh_restritivo(valor):
            try:
                return int(str(valor).strip()) >= cls.PRODES_ANO_INICIAL_RESTRICAO_CREDITO
            except (TypeError, ValueError):
                return False

        registros = [
            dict(registro) for registro in resultado.get('registros', [])
            if ano_eh_restritivo(registro.get('year'))
        ]
        features = []
        for feature in resultado.get('features', []):
            props = dict(feature.get('properties') or {})
            if not ano_eh_restritivo(props.get('year')):
                continue
            features.append({
                'type': 'Feature',
                'properties': props,
                'geometry': feature.get('geometry'),
            })

        resultado['registros'] = registros
        resultado['features'] = features
        resultado['quantidade'] = len(registros)
        resultado['anos'] = cls._valores_unicos_ordenados(r.get('year') for r in registros)
        resultado['tipos'] = cls._valores_unicos_ordenados(r.get('tipo_alerta') for r in registros)
        resultado['area_soma_ocorrencias_ha'] = round(sum(
            float(r.get('area_sobreposta_ha') or 0) for r in registros
            if isinstance(r.get('area_sobreposta_ha'), (int, float))
        ), 6)
        # A área única do repositório foi calculada sobre todo o histórico PRODES.
        # Não a reutilizamos no recorte >= 2019 para não publicar uma métrica
        # temporalmente incorreta.
        resultado['area_unica_sobreposta_ha'] = None
        resultado['cobertura_status'] = (
            'COM_OCORRENCIAS' if registros else 'COBERTURA_NAO_CONFIRMADA'
        )
        return resultado

    @classmethod
    def _montar_alertas(cls, analises_externas, outros_cars):
        """Transforma resultados GIS em mensagens operacionais conservadoras."""
        ibama_raw = analises_externas.get('ibama') or {}
        ibama = cls._alerta_ibama(ibama_raw)

        prodes_raw = analises_externas.get('prodes') or {}
        prodes_credito = cls._prodes_restricao_credito(prodes_raw)
        prodes = cls._alerta_padrao(
            prodes_credito,
            titulo='INPE / PRODES',
            identificado='Ocorrência PRODES a partir de 2019 com interseção espacial identificada',
            nao_identificado=(
                'Nenhuma ocorrência PRODES a partir de 2019 foi localizada nas bases carregadas. '
                'A ausência somente deve ser concluída quando a cobertura da base para a região estiver confirmada.'
            ),
            layer_key='ext_prodes',
        )
        prodes['anos'] = prodes_credito.get('anos') or []
        prodes['tipos'] = prodes_credito.get('tipos') or []
        prodes['area_soma_ocorrencias_ha'] = prodes_credito.get('area_soma_ocorrencias_ha')
        prodes['area_unica_sobreposta_ha'] = prodes_credito.get('area_unica_sobreposta_ha')
        prodes['cobertura_status'] = prodes_credito.get('cobertura_status', '')
        prodes['ano_inicial_restricao_credito'] = cls.PRODES_ANO_INICIAL_RESTRICAO_CREDITO

        assentamentos_raw = analises_externas.get('assentamentos') or {}
        assentamentos = cls._alerta_padrao(
            assentamentos_raw,
            titulo='Projeto de Assentamento — INCRA',
            identificado='Sobreposição com Projeto de Assentamento identificada',
            nao_identificado='Nenhuma sobreposição com Projeto de Assentamento identificada',
            layer_key='ext_assentamentos',
        )
        assentamentos['nomes'] = cls._valores_unicos_ordenados(
            registro.get('nome') for registro in assentamentos_raw.get('registros', [])
        )

        quilombolas_raw = analises_externas.get('quilombolas') or {}
        quilombolas = cls._alerta_padrao(
            quilombolas_raw,
            titulo='Território Quilombola — INCRA',
            identificado='Sobreposição com Território Quilombola identificada',
            nao_identificado='Nenhuma sobreposição com Território Quilombola identificada',
            layer_key='ext_quilombolas',
        )
        quilombolas['nomes'] = cls._valores_unicos_ordenados(
            registro.get('nome') for registro in quilombolas_raw.get('registros', [])
        )

        apa_raw = analises_externas.get('apa') or {}
        apa = cls._alerta_padrao(
            apa_raw,
            titulo='Área de Proteção Ambiental — APA',
            identificado='Interseção com Área de Proteção Ambiental identificada',
            nao_identificado='Nenhuma APA identificada nas bases disponíveis',
            layer_key='ext_apa',
        )
        apa['nomes'] = cls._valores_unicos_ordenados(
            registro.get('nome_uc') for registro in apa_raw.get('registros', [])
        )
        apa['area_unica_sobreposta_ha'] = apa_raw.get('area_unica_sobreposta_ha')

        sicor_raw = analises_externas.get('sicor') or {}
        sicor = cls._alerta_padrao(
            sicor_raw,
            titulo='SICOR / Crédito Rural',
            identificado=(
                'Gleba vinculada a registro de crédito rural do SICOR identificada por interseção espacial. '
                'A ocorrência não confirma, isoladamente, que a operação esteja ativa.'
            ),
            nao_identificado='Nenhuma gleba SICOR foi identificada por interseção espacial nas bases carregadas.',
            layer_key='ext_sicor',
        )
        sicor['registros'] = [cls._registro_sicor_publico(r) for r in sicor_raw.get('registros', [])]
        sicor['anos'] = cls._valores_unicos_ordenados(
            r.get('ano_operacao') or r.get('ano_sicor') for r in sicor_raw.get('registros', [])
        )
        sicor['area_unica_sobreposta_ha'] = sicor_raw.get('area_unica_sobreposta_ha')

        outros = cls._alerta_padrao(
            outros_cars,
            titulo='Sobreposição com outros CARs',
            identificado='Sobreposição de área com outro CAR identificada',
            nao_identificado='Nenhuma sobreposição de área com outro CAR identificada',
            layer_key='ext_outros_car',
        )

        itens = {
            'ibama': ibama,
            'prodes': prodes,
            'assentamentos': assentamentos,
            'quilombolas': quilombolas,
            'apa': apa,
            'sicor': sicor,
            'outros_cars': outros,
        }

        # Alertas são ocorrências territoriais que merecem análise. A classificação
        # é operacional e não representa conclusão jurídica.
        catalogo_alertas = (
            ('outros_cars', 'Sobreposição com outro CAR'),
            ('apa', 'Sobreposição com APA'),
            ('assentamentos', 'Sobreposição com Assentamento'),
            ('quilombolas', 'Sobreposição com Área Quilombola'),
            ('sicor', 'Crédito rural — SICOR'),
            ('prodes', 'Ocorrência PRODES'),
            ('ibama', 'Embargo IBAMA'),
        )
        tipos_alerta = [
            label for key, label in catalogo_alertas
            if itens.get(key, {}).get('estado') in {'alerta', 'atencao'}
        ]
        resumo_alertas = {
            'quantidade': len(tipos_alerta),
            'tipos': tipos_alerta,
            'resumo': ' • '.join(tipos_alerta),
        }

        avisos = []
        if tipos_alerta:
            avisos.append('Alertas identificados: ' + ', '.join(tipos_alerta) + '.')

        return {
            **itens,
            'resumo': resumo_alertas,
            # Alias temporário para componentes antigos; o frontend novo usa "resumo".
            'restricoes': resumo_alertas,
            'tem_alerta': bool(tipos_alerta),
            'resumo_mapa': ' '.join(avisos),
        }

    @classmethod
    def _alerta_ibama(cls, resultado):
        resultado = resultado or {}
        disponivel = bool(resultado.get('disponivel'))
        quantidade = int(resultado.get('quantidade') or 0)

        if not disponivel:
            estado = 'nao_verificado'
            status = 'Não verificado'
            mensagem = 'A base IBAMA não está disponível para esta verificação.'
        elif quantidade > 0:
            estado = 'atencao'
            status = 'Identificado'
            mensagem = (
                'Embargo IBAMA identificado por interseção espacial na base carregada. '
                'O resultado é um alerta de planejamento e não uma conclusão jurídica.'
            )
        else:
            estado = 'ok'
            status = 'Sem indício espacial'
            mensagem = 'Nenhuma interseção espacial foi localizada na base IBAMA carregada.'

        registros = [cls._registro_ibama_publico(r) for r in resultado.get('registros', [])]
        return {
            'titulo': 'Embargo IBAMA',
            'estado': estado,
            'status': status,
            'mensagem': mensagem,
            'quantidade': quantidade,
            'registros': registros,
            'truncada': bool(resultado.get('truncada')),
            'layer_key': 'ext_ibama',
            'confirmacao_externa_necessaria': quantidade > 0,
        }

    @staticmethod
    def _registro_ibama_publico(registro):
        campos = (
            'numero_embargo', 'serie_embargo', 'auto_infracao', 'processo',
            'data_embargo', 'tipo_area', 'bioma', 'municipio', 'uf',
            'area_embargo_informada_ha', 'area_geometria_ha',
            'area_sobreposta_ha', 'percentual_car',
        )
        return {campo: registro.get(campo) for campo in campos if registro.get(campo) not in (None, '')}

    @staticmethod
    def _registro_sicor_publico(registro):
        campos = (
            'ref_bacen', 'nu_ordem', 'indice_gleba', 'ano_sicor', 'ano_operacao',
            'dt_emissao', 'dt_vencimento', 'cd_estado', 'cd_fonte_recurso',
            'cd_empreendimento', 'cd_programa', 'vl_parc_credito', 'vl_area_financ',
            'vl_area_informada', 'vl_juros', 'origem_gleba_sicor',
            'area_geometria_ha', 'area_sobreposta_ha', 'percentual_car',
        )
        return {campo: registro.get(campo) for campo in campos if registro.get(campo) not in (None, '')}

    @staticmethod
    def _alerta_padrao(resultado, *, titulo, identificado, nao_identificado, layer_key):
        resultado = resultado or {}
        disponivel = bool(resultado.get('disponivel'))
        quantidade = int(resultado.get('quantidade') or 0)

        if not disponivel:
            estado = 'nao_verificado'
            status = 'Não verificado'
            mensagem = 'Esta verificação ainda não está disponível.'
        elif quantidade > 0:
            estado = 'alerta'
            status = 'Identificado'
            mensagem = identificado
        else:
            estado = 'ok'
            status = 'Não identificado'
            mensagem = nao_identificado

        return {
            'titulo': titulo,
            'estado': estado,
            'status': status,
            'mensagem': mensagem,
            'quantidade': quantidade,
            'registros': resultado.get('registros', []),
            'truncada': bool(resultado.get('truncada')),
            'layer_key': layer_key,
            'area_unica_sobreposta_ha': resultado.get('area_unica_sobreposta_ha'),
        }

    @classmethod
    def _montar_camadas_externas(cls, analises_externas, outros_cars):
        camadas = {}

        def adicionar(chave, label, resultado, features=None):
            resultado = resultado or {}
            feats = list(features if features is not None else resultado.get('features', []))
            camadas[chave] = {
                'label': label,
                'disponivel': bool(resultado.get('disponivel')),
                'features': feats,
            }

        ibama = analises_externas.get('ibama') or {}
        ibama_features = []
        for feature in ibama.get('features', []):
            ibama_features.append({
                'type': 'Feature',
                'properties': cls._registro_ibama_publico(feature.get('properties') or {}),
                'geometry': feature.get('geometry'),
            })
        adicionar('ibama', 'Embargo IBAMA', ibama, ibama_features)
        adicionar('assentamentos', 'Assentamentos INCRA', analises_externas.get('assentamentos'))
        adicionar('quilombolas', 'Territórios Quilombolas', analises_externas.get('quilombolas'))
        adicionar('apa', 'APA / Unidade de Conservação', analises_externas.get('apa'))

        sicor = analises_externas.get('sicor') or {}
        sicor_features = []
        for feature in sicor.get('features', []):
            sicor_features.append({
                'type': 'Feature',
                'properties': cls._registro_sicor_publico(feature.get('properties') or {}),
                'geometry': feature.get('geometry'),
            })
        adicionar('sicor', 'SICOR / Crédito Rural', sicor, sicor_features)
        adicionar('outros_car', 'Sobreposição com outros CARs', outros_cars)

        # Uma única camada PRODES evita descartar classes não previstas e mantém
        # o mapa alinhado ao layer_key do alerta ('ext_prodes'). A classificação
        # original permanece disponível nas propriedades de cada feição/popup.
        prodes = analises_externas.get('prodes') or {}
        adicionar('prodes', 'INPE / PRODES', prodes)
        return camadas

    @staticmethod
    def _valores_unicos_ordenados(valores):
        limpos = {str(valor).strip() for valor in valores if valor not in (None, '') and str(valor).strip()}

        def chave(valor):
            try:
                return (0, int(valor))
            except (TypeError, ValueError):
                return (1, valor.casefold())

        return sorted(limpos, key=chave)
