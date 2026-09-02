from enum import StrEnum

from .consulta_car import ConsultaCarService


class CanalConsulta(StrEnum):
    """Canais previstos para consumir a mesma inteligência territorial."""

    WEB = 'WEB'
    WHATSAPP = 'WHATSAPP'
    APP = 'APP'


class InteligenciaTerritorialService:
    """Fachada neutra de interface para consultas do CONFRONTA.

    A camada Web pode continuar renderizando a consulta completa. WhatsApp e
    aplicativo móvel poderão consumir o mesmo resultado e pedir um resumo sem
    duplicar regras GIS. A futura consulta por coordenadas deve ser adicionada
    aqui quando o repositório PostGIS correspondente for implementado.
    """

    def __init__(self, consulta_car_service=None):
        self.consulta_car_service = consulta_car_service or ConsultaCarService()

    def validar_car(self, car):
        return self.consulta_car_service.validar_existencia(car)

    def consultar_por_car(self, car, *, canal=CanalConsulta.WEB):
        # ``canal`` já faz parte do contrato para futura auditoria/limites por
        # origem, mas não altera a regra territorial nesta versão.
        CanalConsulta(canal)
        return self.consulta_car_service.executar(car)

    @staticmethod
    def resumir(consulta):
        """Retorna dados compactos adequados a WhatsApp/mobile sem HTML."""
        consulta = consulta or {}
        imovel = consulta.get('imovel') or {}
        restricoes = consulta.get('restricoes') or {}
        alertas = consulta.get('alertas') or {}

        alertas_resumo = []
        for chave, alerta in alertas.items():
            if chave in {'tem_alerta', 'resumo_mapa', 'restricoes', 'resumo'} or not isinstance(alerta, dict):
                continue
            estado = alerta.get('estado')
            if estado in {'alerta', 'atencao'}:
                alertas_resumo.append({
                    'chave': chave,
                    'titulo': alerta.get('titulo') or chave,
                    'estado': estado,
                    'status': alerta.get('status') or '',
                })

        return {
            'car': imovel.get('cod_imovel'),
            'municipio': imovel.get('municipio'),
            'uf': imovel.get('uf'),
            'area_total_ha': imovel.get('area_total_ha'),
            'situacao_car': imovel.get('situacao_apresentacao') or imovel.get('situacao_car'),
            'restricoes': {
                'quantidade': int(restricoes.get('quantidade') or 0),
                'tipos': list(restricoes.get('tipos') or []),
            },
            'alertas': alertas_resumo,
        }
