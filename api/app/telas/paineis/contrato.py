"""Contrato dos painéis por cartões (Vendas, Equipe comercial, Clientes e atendimentos),
espelho de src/contracts/painel.ts do overnex-frontend.

Como na Visão geral, o front só desenha: todo número, status, texto, rótulo, média,
projeção e diferença vem pronto. Mudou aqui, mude lá (ou gere os tipos do front a partir
do OpenAPI).

Opcional do TypeScript (`campo?:`) é campo com padrão aqui: com
`response_model_exclude_defaults`, sai do JSON quando não tem valor. Booleano que o front
recebe também como `false` (ex.: `diferencaRuim`) tem padrão None, para o `false` sair.
"""

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import Field

from app.telas.contrato import Status
from app.telas.contrato import Valor as Numero
from app.telas.moldura import Cliente, ModeloCamel
from app.telas.visao_geral.contrato import Kpi, Trecho, Unidade, Valor

TelaPainel = Literal["vendas", "equipe-comercial", "clientes"]

# número a formatar pela unidade, ou texto já pronto ("2 de 6")
Exibicao = Valor | str


class ItemValor(ModeloCamel):
    rotulo: str
    valor: Exibicao
    contexto: str | None = None
    status: Status | None = None
    # "+2,1%" em vez de "2,1%"
    com_sinal: bool = False


class OpcaoFiltro(ModeloCamel):
    id: str
    rotulo: str


class FiltroPainel(ModeloCamel):
    """Filtro próprio da tela; o valor escolhido vem na consulta: ?{id}={opcao.id}."""

    id: str
    rotulo: str
    opcoes: list[OpcaoFiltro]
    # id da opção padrão ("todos"); com ela, o parâmetro não vem
    padrao: str


class Aviso(ModeloCamel):
    tom: Literal["info", "atencao"]
    texto: list[Trecho]


# ---------- gráficos ----------


class Escala(ModeloCamel):
    unidade: Unidade
    casas: int | None = None
    # depois do número: "t", "clientes"
    sufixo: str | None = None


EstiloBarra = Literal[
    "saudavel", "atencao", "critico", "serie", "serie-media", "serie-clara", "fantasma", "projecao"
]


class Barra(ModeloCamel):
    nome: str
    valor: Numero
    estilo: EstiloBarra


class GrupoBarras(ModeloCamel):
    rotulo: str
    # título da dica; sem ele, o rótulo
    titulo: str | None = None
    barras: list[Barra]


class ReferenciaBarras(ModeloCamel):
    nome: str
    valor: Numero
    # media: cinza tracejada; minimo: laranja (o necessário)
    estilo: Literal["media", "minimo"]


class GraficoBarras(Escala):
    tipo: Literal["barras"]
    grupos: list[GrupoBarras]
    referencias: list[ReferenciaBarras] | None = None
    # valor escrito em cima de cada barra
    rotulos_nas_barras: bool = False


class NomesCombo(ModeloCamel):
    valor: str
    referencia: str | None = None
    linha: str | None = None


class PontoCombo(ModeloCamel):
    rotulo: str
    titulo: str | None = None
    valor: Numero
    referencia: Numero | None = None
    # crítico pinta a barra de vermelho (mês pior que a referência)
    status: Status | None = None
    linha: Numero | None = None


class GraficoCombo(Escala):
    """Barras contra uma referência (traço sobre a barra) e uma linha percentual em cima."""

    tipo: Literal["combo"]
    nomes: NomesCombo
    pontos: list[PontoCombo]
    escala_linha: Escala | None = None


EstiloLinha = Literal["principal", "secundaria", "pontilhada"]


class SerieLinha(ModeloCamel):
    nome: str
    # None: sem valor naquele ponto (vendedor que ainda não tinha entrado)
    valores: list[Numero | None]
    estilo: EstiloLinha
    area: bool = False
    # rótulo no fim da linha
    rotulo_fim: str | None = None


class ReferenciaLinha(ModeloCamel):
    valor: Numero
    rotulo: str


class Marcador(ModeloCamel):
    serie: int
    i: int
    rotulo: str


class GraficoLinhas(Escala):
    tipo: Literal["linhas"]
    rotulos: list[str]
    # título da dica de cada ponto; sem ele, o rótulo
    titulos: list[str] | None = None
    series: list[SerieLinha]
    # linha tracejada horizontal (a média)
    referencia: ReferenciaLinha | None = None
    marcadores: list[Marcador] | None = None
    # inclui este valor na escala (ex.: 0)
    piso: Numero | None = None


class SerieEmpilhada(ModeloCamel):
    nome: str
    valores: list[Numero]


class GraficoEmpilhado(Escala):
    """Barras empilhadas; as cores seguem a ordem das séries (até 7)."""

    tipo: Literal["empilhado"]
    rotulos: list[str]
    series: list[SerieEmpilhada]


class EixoDispersao(Escala):
    rotulo: str


class Quadrante(ModeloCamel):
    posicao: Literal[
        "superior-esquerdo", "superior-direito", "inferior-esquerdo", "inferior-direito"
    ]
    rotulo: str


class PontoDispersao(ModeloCamel):
    nome: str
    x: Numero
    y: Numero
    # área da bolha proporcional a este valor
    tamanho: Numero
    status: Status | None = None
    # escreve o nome ao lado da bolha
    rotular: bool = False
    dica: list[ItemValor]


class GraficoDispersao(ModeloCamel):
    tipo: Literal["dispersao"]
    eixo_x: EixoDispersao
    eixo_y: EixoDispersao
    # linhas de corte (medianas), já calculadas
    corte_x: Numero | None = None
    corte_y: Numero | None = None
    quadrantes: list[Quadrante] | None = None
    pontos: list[PontoDispersao]


class DiaDegrau(ModeloCamel):
    rotulo: str
    # "13/10 · ter"
    titulo: str
    util: bool
    # rótulo aparece no eixo (e no celular, só os `eixo_curto`)
    eixo: bool = False
    eixo_curto: bool = False


class SerieDegrau(ModeloCamel):
    nome: str
    estilo: Literal["realizado", "projecao", "meta", "supermeta"]
    valores: list[Numero | None]
    rotulo_fim: str | None = None
    rotulo_fim_curto: str | None = None


class Hoje(ModeloCamel):
    indice: int
    rotulo: str


class Falta(ModeloCamel):
    de: Numero
    ate: Numero


class GraficoDegraus(Escala):
    """Acumulado do mês dia a dia, em degraus: plano nos dias sem venda."""

    tipo: Literal["degraus"]
    dias: list[DiaDegrau]
    series: list[SerieDegrau]
    hoje: Hoje | None = None
    # traço vermelho no fim: da projeção até a meta
    falta: Falta | None = None


Grafico = Annotated[
    GraficoBarras
    | GraficoCombo
    | GraficoLinhas
    | GraficoEmpilhado
    | GraficoDispersao
    | GraficoDegraus,
    Field(discriminator="tipo"),
]

MarcaLegenda = Literal[
    "linha", "linha-clara", "pontilhada", "media", "minimo", "quadrado-escuro", "quadrado-claro"
]

# ---------- cartões ----------


class ItemLegenda(ModeloCamel):
    nome: str
    marca: MarcaLegenda


class Chamada(ModeloCamel):
    status: Status
    texto: list[Trecho]


class CartaoBase(ModeloCamel):
    id: str
    # colunas da grade de 12
    largura: Literal[4, 5, 6, 7, 8, 12]
    titulo: str
    subtitulo: str | None = None
    # pílula de status ao lado do título
    status: Status | None = None
    legenda: list[ItemLegenda] | None = None
    # caixa de destaque no fim do cartão
    chamada: Chamada | None = None
    nota: str | None = None
    # o que o gráfico mostra e como ler (texto puro); com ele, o título ganha o botão "i"
    explicacao: str | None = None


class TrechoRegua(ModeloCamel):
    estilo: Literal["realizado", "projecao", "falta"]
    rotulo: str
    de: Numero
    ate: Numero
    # o número da legenda ("+R$ 4,36 mi")
    valor: Valor
    com_sinal: bool = False


class MarcaRegua(ModeloCamel):
    rotulo: str
    valor: Numero
    secundaria: bool = False


class Regua(ModeloCamel):
    trechos: list[TrechoRegua]
    marcas: list[MarcaRegua]


class VisaoAndamento(ModeloCamel):
    id: str
    rotulo: str
    grafico: GraficoDegraus


class CartaoAndamentoMes(CartaoBase):
    """Régua do mês, estatísticas e o acumulado dia a dia (uma visão por unidade)."""

    tipo: Literal["andamento-mes"]
    regua: Regua
    estatisticas: list[ItemValor]
    visoes: list[VisaoAndamento]


class Fator(ModeloCamel):
    nome: str
    operador: str | None = None
    colunas: list[ItemValor]
    causa: bool = False


class CartaoFatores(CartaoBase):
    """Decomposição de um total em fatores, com o fator que mais pesa marcado."""

    tipo: Literal["fatores"]
    total: ItemValor
    fatores: list[Fator]
    conclusao: list[Trecho] | None = None


class ItemContraMeta(ModeloCamel):
    nome: str
    # "Varejo · novo, 3º mês"
    detalhe: str | None = None
    status: Status
    atingimento: Valor
    realizado: Valor
    meta: Valor
    diferenca: Valor | None = None
    diferenca_ruim: bool | None = None
    # linhas de rodapé do item ("Vendido até hoje R$ 948 mil", "2 vendedores")
    detalhes: list[str] | None = None


class CartaoContraMeta(CartaoBase):
    """Barras contra a meta de cada item: blocos (canais) ou ranking (vendedores)."""

    tipo: Literal["contra-meta"]
    layout: Literal["blocos", "ranking"]
    rotulo_realizado: str
    rotulo_meta: str
    itens: list[ItemContraMeta]


class Celula(ModeloCamel):
    valor: Exibicao | None = None
    # segunda linha, menor
    sub: str | None = None
    com_sinal: bool = False
    # ▲ ou ▼ antes do número, pelo sinal
    seta: bool = False
    # cor do texto
    tom: Status | Literal["apagado"] | None = None
    # fundo de farol
    fundo: Status | None = None
    pill: Status | None = None
    negrito: bool = False
    # etiqueta pequena depois do texto ("rampa")
    etiqueta: str | None = None


class ColunaTabela(ModeloCamel):
    rotulo: str
    alinhar: Literal["esquerda", "direita", "centro"] | None = None
    ocultar_no_celular: bool = False


class LinhaTabela(ModeloCamel):
    # pinta o fundo da linha
    status: Status | None = None
    celulas: list[Celula]


class CartaoTabela(CartaoBase):
    tipo: Literal["tabela"]
    # calor: células coloridas e centralizadas (atingimento mês a mês)
    variante: Literal["padrao", "compacta", "calor"] | None = None
    colunas: list[ColunaTabela]
    linhas: list[LinhaTabela]
    vazio: str | None = None


class ItemDestaque(ModeloCamel):
    categoria: str
    titulo: str
    texto: str
    status: Status


class CartaoDestaques(CartaoBase):
    """Cartões de destaque lado a lado ("Mais vendas", "Precisa de apoio")."""

    tipo: Literal["destaques"]
    itens: list[ItemDestaque]


class GraficoCartao(ModeloCamel):
    titulo: str | None = None
    nota: str | None = None
    grafico: Grafico


class CartaoGraficos(CartaoBase):
    """Um ou mais gráficos lado a lado."""

    tipo: Literal["graficos"]
    graficos: list[GraficoCartao]


class LinhaFaixa(ModeloCamel):
    rotulo: str
    valores: tuple[Numero, Numero]


class CartaoFaixas(CartaoBase):
    """Pares de barras por faixa (participação nos atendimentos e na receita)."""

    tipo: Literal["faixas"]
    # as duas séries, na ordem das barras
    series: tuple[str, str]
    linhas: list[LinhaFaixa]
    escala: Escala


class ClasseAbc(ModeloCamel):
    rotulo: str
    acao: str
    quantidade: Exibicao
    textos: list[list[Trecho]]
    # sem status: borda neutra
    status: Status | None = None


class CartaoCurvaAbc(CartaoBase):
    tipo: Literal["curva-abc"]
    classes: list[ClasseAbc]
    grafico: GraficoLinhas
    nota_grafico: str | None = None


class CelulaMatriz(ModeloCamel):
    valor: Numero
    # pinta a célula; sem ele, cinza na intensidade do valor
    status: Status | None = None


class CartaoMatriz(CartaoBase):
    """Matriz de contagens (curva de receita × curva de margem)."""

    tipo: Literal["matriz"]
    linhas: list[str]
    colunas: list[str]
    valores: list[list[CelulaMatriz]]


Cartao = Annotated[
    CartaoAndamentoMes
    | CartaoFatores
    | CartaoContraMeta
    | CartaoTabela
    | CartaoDestaques
    | CartaoGraficos
    | CartaoFaixas
    | CartaoCurvaAbc
    | CartaoMatriz,
    Field(discriminator="tipo"),
]


class PainelPayload(ModeloCamel):
    tela: TelaPainel
    versao_contrato: Literal["1.0"]
    cliente: Cliente
    # data dos dados (data_snapshot da última carga 'ok'); None até a primeira carga
    dados_ate: date | None
    # quando essa carga terminou, com fuso
    atualizado_em: datetime | None
    filtros: list[FiltroPainel]
    aviso: Aviso | None = None
    # números grandes do topo (até 3)
    destaques: list[ItemValor]
    # linha de KPIs (no lugar dos destaques, em Clientes)
    kpis: list[Kpi]
    apoios: list[ItemValor]
    cartoes: list[Cartao]
