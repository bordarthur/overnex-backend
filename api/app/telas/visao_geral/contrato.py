"""Contrato da Visão geral, espelho de src/contracts/visaoGeral.ts do overnex-frontend.

O front só desenha o que vem aqui: todo número, status, ponteiro, delta e texto vem pronto.
Mudou aqui, mude lá (ou gere os tipos do front a partir do OpenAPI).
"""

from datetime import date, datetime
from typing import Literal

from app.telas.contrato import Status
from app.telas.contrato import Valor as Numero
from app.telas.moldura import Cliente, ModeloCamel

PeriodoId = Literal["mes", "ano"]
Unidade = Literal["brl", "pct", "pp", "dias", "vezes", "qtd"]


class Valor(ModeloCamel):
    valor: Numero
    unidade: Unidade
    casas: int | None = None
    # brl compacto (padrão do front: true): R$ 6,54 mi / R$ 360 mil; brl cheio: R$ 2.995
    compacto: bool | None = None
    # sufixo, por exemplo para qtd: "clientes", "linhas"
    sufixo: str | None = None


class Trecho(ModeloCamel):
    texto: str
    destaque: bool = False


class Kpi(ModeloCamel):
    rotulo: str
    valor: Valor
    contexto: str
    status: Status | None = None


class Faixa(ModeloCamel):
    de: Numero
    ate: Numero
    status: Status


class Gauge(ModeloCamel):
    min: Numero
    max: Numero
    meta: Numero
    unidade: Unidade
    faixas: list[Faixa]


class IndicadorPeriodo(ModeloCamel):
    status: Status
    valor: Valor
    # posição no velocímetro, na escala do gauge
    ponteiro: Numero
    # delta com valor 0 aparece como "na média"
    delta: Valor | None
    delta_ruim: bool
    referencia: str


class PeriodosIndicador(ModeloCamel):
    mes: IndicadorPeriodo
    ano: IndicadorPeriodo


class Tendencia(ModeloCamel):
    rotulos: list[str]
    real: list[Numero]
    referencia: list[Numero]
    faixa: tuple[Numero, Numero] | None = None
    unidade: Unidade
    casas: int | None = None


class Indicador(ModeloCamel):
    id: str
    nome: str
    tela_destino: str | None = None
    origem: Literal["dw", "cadastro"]
    gauge: Gauge
    periodos: PeriodosIndicador
    tendencia: Tendencia


class ItemCarteira(ModeloCamel):
    rotulo: str
    valor: Valor
    percentual: Numero
    status: Status


class SerieInadimplencia(ModeloCamel):
    rotulos: list[str]
    valores: list[Numero]
    media: Numero


class CartaoInadimplencia(ModeloCamel):
    tipo: Literal["inadimplencia"]
    status: Status
    percentual: Valor
    texto: list[Trecho]
    nota: str
    carteira: list[ItemCarteira]
    serie: SerieInadimplencia


class Secao(ModeloCamel):
    id: str
    titulo: str
    modulo: str | None
    resumo: str
    kpis: list[Kpi]
    cartoes: list[CartaoInadimplencia]
    indicadores: list[Indicador]


class Periodo(ModeloCamel):
    id: PeriodoId
    rotulo: str


class VisaoGeralPayload(ModeloCamel):
    tela: Literal["visao-geral"]
    versao_contrato: Literal["1.0"]
    cliente: Cliente
    # data dos dados (data_snapshot da última carga 'ok'); None se nunca houve carga
    dados_ate: date | None
    # quando essa carga terminou, com fuso
    atualizado_em: datetime | None
    periodos: list[Periodo]
    secoes: list[Secao]
