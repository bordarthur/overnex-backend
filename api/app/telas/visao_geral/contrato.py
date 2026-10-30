"""Contrato da Visão geral, espelho de src/contracts/visaoGeral.ts do overnex-frontend.

O front só desenha o que vem aqui: todo número, status, ponteiro, delta e texto vem pronto.
Mudou aqui, mude lá (ou gere os tipos do front a partir do OpenAPI).
"""

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import Field

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
    # brl compacto em milhares com casas (R$ 54,7 mil); sem ele, milhares saem inteiros
    casas_mil: int | None = None
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
    # delta com valor 0 aparece como "na média"; em texto, vai como veio ("na meta")
    delta: Valor | str | None
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
    # cabeçalho opcional (os outros cartões sempre têm); sem explicação, sem o botão "i"
    titulo: str | None = None
    subtitulo: str | None = None
    explicacao: str | None = None
    status: Status
    percentual: Valor
    texto: list[Trecho]
    nota: str
    carteira: list[ItemCarteira]
    serie: SerieInadimplencia


class ValorRotulado(ModeloCamel):
    """Valor com o rótulo ao lado dele no gráfico ("lançado", "média paga R$ 6,31 mi")."""

    valor: Valor
    rotulo: str


class ModoProximosDias(ModeloCamel):
    """Um modo do seletor do cartão ("Só o lançado", "Com o que falta lançar")."""

    id: str
    rotulo: str
    # true: completa o a pagar com o que falta lançar
    com_falta: bool
    status: Status | None


class LegendaProximosDias(ModeloCamel):
    saldo: str | None
    receber: str
    pagar: str
    falta: str


class MinimoCaixa(ModeloCamel):
    """Caixa mínimo da empresa: a linha tracejada ("caixa mínimo R$ 500 mil")."""

    valor: Numero
    rotulo: str


class FimJanela(ModeloCamel):
    valor: Valor
    status: Status | None
    # embaixo do valor: "entra mais do que sai"
    texto: str


class JanelaProximosDias(ModeloCamel):
    titulo: str
    # "até 19/10"
    ate: str
    receber: Valor
    pagar: Valor
    # o que falta lançar para chegar à média paga, proporcional aos dias
    falta: Valor
    # onde o caixa termina em cada modo, pelo id do modo
    fim: dict[str, FimJanela]


class CartaoProximosDias(ModeloCamel):
    """Próximos 7, 15 e 30 dias: saldo de hoje (quando houver), mais o que vence a receber,
    menos o que vence a pagar."""

    tipo: Literal["proximos-dias"]
    titulo: str
    subtitulo: str
    explicacao: str | None = None
    nota: str
    # o primeiro é o padrão
    modos: list[ModoProximosDias]
    legenda: LegendaProximosDias
    saldo: Valor | None
    minimo: MinimoCaixa | None
    janelas: list[JanelaProximosDias]


class PagoNoMes(ModeloCamel):
    rotulo: str
    valor: Valor


class ProximosLancado(ModeloCamel):
    rotulo: str
    lancado: ValorRotulado
    falta: ValorRotulado


class CartaoPagarLancado(ModeloCamel):
    """O que já está lançado a pagar nos próximos dias, contra o que a empresa pagou por mês."""

    tipo: Literal["pagar-lancado"]
    titulo: str
    subtitulo: str
    explicacao: str | None = None
    status: Status
    percentual: Valor
    # o texto ao lado do número grande
    texto: str
    nota: str
    pagos: list[PagoNoMes]
    media: ValorRotulado
    proximos: ProximosLancado


CartaoSecao = Annotated[
    CartaoInadimplencia | CartaoProximosDias | CartaoPagarLancado, Field(discriminator="tipo")
]


class OpcaoSeletor(ModeloCamel):
    id: str
    rotulo: str


class SeletorSecao(ModeloCamel):
    """Escolha que fica salva para o cliente e muda o que a seção mostra (na Custos: quantas
    linhas de custo viram velocímetro). Trocar grava em PUT
    /api/telas/visao-geral/preferencias/{id}."""

    id: str
    rotulo: str
    opcoes: list[OpcaoSeletor]
    valor: str
    nota: str


class Secao(ModeloCamel):
    id: str
    titulo: str
    modulo: str | None
    resumo: str
    kpis: list[Kpi]
    seletor: SeletorSecao | None = None
    cartoes: list[CartaoSecao]
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
    # a chave de cada velocímetro ("Mês", "Acumulado"); o primeiro é o padrão
    periodos: list[Periodo]
    # a nota no topo da tela, sobre como os cartões comparam
    nota: str | None = None
    secoes: list[Secao]
