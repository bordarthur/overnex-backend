"""Contrato dos painéis por cartões (Vendas, Equipe comercial, Clientes e atendimentos,
Resultado, Receber e pagar e Pessoas), espelho de src/contracts/painel.ts do overnex-frontend.

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

TelaPainel = Literal[
    "vendas", "equipe-comercial", "clientes", "resultado", "receber-pagar", "pessoas"
]

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
    # reais em milhares com casas, na dica (R$ 54,7 mil)
    casas_mil: int | None = None
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
    # atraso: da faixa de 1 a 15 dias (mais clara) até mais de 90 (crítico)
    paleta: Literal["series", "atraso"] | None = None
    rotulos: list[str]
    series: list[SerieEmpilhada]
    # valor escrito dentro de cada parte (quando cabe)
    rotulos_nas_barras: bool = False
    # total escrito em cima de cada pilha, um por rótulo, já somado
    totais: list[Numero] | None = None


class PassoCascata(ModeloCamel):
    rotulo: str
    # início e fim da barra, já somados: o front não soma nada
    de: Numero
    ate: Numero
    valor: Numero
    # total: barra cheia; positivo ajudou; negativo atrapalhou
    estilo: Literal["total", "positivo", "negativo"]


class GraficoCascata(Escala):
    """Cascata: da receita ao lucro, do orçado ao realizado."""

    tipo: Literal["cascata"]
    passos: list[PassoCascata]
    # início da escala, para mostrar diferenças pequenas (ex.: ponte do EBITDA)
    piso: Numero | None = None


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
    | GraficoDegraus
    | GraficoCascata,
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


class PillComRotulo(ModeloCamel):
    status: Status
    # texto da pílula ("Alta", "Média", "Baixa")
    rotulo: str


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
    # pílula de status; com rótulo, o texto é o da API
    pill: Status | PillComRotulo | None = None
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


class VisaoGraficos(ModeloCamel):
    id: str
    rotulo: str
    graficos: list[GraficoCartao]
    # embaixo dos gráficos
    nota: list[Trecho] | None = None


class CartaoGraficos(CartaoBase):
    """Um ou mais gráficos lado a lado. Com `visoes`, o cartão ganha uma chave (Todos,
    Logística...) e desenha só os gráficos da visão escolhida; aí `graficos` vem vazio."""

    tipo: Literal["graficos"]
    # gráficos por linha quando há mais de um (padrão do front: 2)
    colunas: Literal[2, 3] | None = None
    graficos: list[GraficoCartao]
    # nome da chave, para leitor de tela ("Setor")
    rotulo_visoes: str | None = None
    # a primeira é a padrão
    visoes: list[VisaoGraficos] | None = None


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


class TendenciaDre(ModeloCamel):
    # últimos 6 meses, na escala do cartão
    realizado: list[Numero]
    comparacao: list[Numero]


class LinhaDre(ModeloCamel):
    id: str
    # código da conta no plano ("100.01"); subtotais não têm
    codigo: str | None = None
    nome: str
    # 0 = subtotal (Receita líquida, Lucro bruto…); 1 a 3 = níveis do plano de contas
    nivel: Literal[0, 1, 2, 3]
    # id da linha de cima na árvore
    pai: str | None = None
    # a última linha (Lucro líquido final), em destaque
    final: bool = False
    realizado: Numero
    # CMV: sobre a receita bruta menos as devoluções
    percentual_receita_bruta: Numero
    comparacao: Numero
    diferenca: Numero
    diferenca_percentual: Numero | None
    # cor do texto da diferença (melhor ou pior que a comparação)
    tom_diferenca: Status | None = None
    # |diferença %| ≥ 3: fundo de farol na diferença
    destacar_diferenca: bool = False
    status: Status
    # esta linha ou alguma abaixo dela fora de "saudavel" (o que fica com "Só desvios")
    com_desvio: bool
    tendencia: TendenciaDre


class CartaoDre(CartaoBase):
    """DRE em árvore: abre por nível, filtra os desvios e mostra a tendência de cada conta."""

    tipo: Literal["dre"]
    # cabeçalho da primeira coluna ("Conta (R$ mil)") e da comparação ("Orçado", "Agosto")
    rotulo_conta: str
    rotulo_comparacao: str
    escala: Escala
    nivel_inicial: Literal[1, 2, 3]
    # níveis que a chave oferece
    niveis: list[Literal[1, 2, 3]]
    # na ordem de exibição, com os subtotais
    linhas: list[LinhaDre]
    rodape: list[str] | None = None


class ParteBarra(ModeloCamel):
    rotulo: str
    # proporções da barra somam 1
    proporcao: Numero
    estilo: Literal["serie-clara", "saudavel", "atencao", "critico"]


class EscalaPartes(ModeloCamel):
    inicio: str
    fim: str


class CartaoBarraPartes(CartaoBase):
    """Uma barra dividida em partes (ponto de equilíbrio e folga), com a escala e um texto."""

    tipo: Literal["barra-partes"]
    partes: list[ParteBarra]
    escala: EscalaPartes
    texto: list[Trecho] | None = None


class ItemDesvio(ModeloCamel):
    nome: str
    detalhe: str | None = None
    valor: Numero


class GrupoDesvios(ModeloCamel):
    titulo: str
    status: Status
    itens: list[ItemDesvio]


class CartaoDesvios(CartaoBase):
    """Contas que mais pioram e mais melhoram o resultado contra a comparação."""

    tipo: Literal["desvios"]
    escala: Escala
    grupos: list[GrupoDesvios]


class LinhaJanela(ModeloCamel):
    rotulo: str
    valor: Valor
    tom: Status | None = None
    com_sinal: bool = False
    # total: com linha em cima; saldo: com o fundo do status da janela
    tipo: Literal["total", "saldo"] | None = None


class Janela(ModeloCamel):
    titulo: str
    status: Status | None = None
    linhas: list[LinhaJanela]


class AlertaJanelas(ModeloCamel):
    etiqueta: str
    texto: list[Trecho]
    rodape: str | None = None


class CartaoJanelas(CartaoBase):
    """Janelas de dias lado a lado (próximos 7, 15 e 30 dias), com um alerta opcional."""

    tipo: Literal["janelas"]
    janelas: list[Janela]
    alerta: AlertaJanelas | None = None


class FaixaIdade(ModeloCamel):
    rotulo: str
    valor: Valor
    # em % do total do lado
    participacao: Numero
    status: Status | None = None


class ItemMaior(ModeloCamel):
    nome: str
    valor: Valor
    # em % do total do lado
    participacao: Numero
    etiqueta: str | None = None


class LadoConta(ModeloCamel):
    titulo: str
    # "em aberto: R$ 7,13 mi"
    total: str
    # etiqueta de qualidade do dado ao lado do total
    alerta: str | None = None
    kpis: list[Kpi]
    idade: list[FaixaIdade]
    observacao: list[Trecho] | None = None
    maiores: list[ItemMaior]


class CartaoDoisLados(CartaoBase):
    """Os dois lados de uma visão (contas a receber × contas a pagar)."""

    tipo: Literal["dois-lados"]
    lados: list[LadoConta]


class BarraHorizontal(ModeloCamel):
    rotulo: str
    sub: str | None = None
    valor: Numero
    status: Status | None = None


class CartaoBarrasHorizontais(CartaoBase):
    """Lista de barras horizontais (a pagar por semana); a maior ocupa a largura toda."""

    tipo: Literal["barras-horizontais"]
    escala: Escala
    itens: list[BarraHorizontal]


class VariacaoNumero(ModeloCamel):
    # "▲ 3% em 12 meses": a seta segue o sinal do valor; o tom diz se é bom ou ruim
    valor: Valor
    texto: str
    tom: Status


class NumeroDestaque(ModeloCamel):
    valor: Valor
    texto: list[Trecho]
    variacao: VariacaoNumero | None = None
    nota: str | None = None


class CartaoNumeroGrafico(CartaoBase):
    """Um número grande com o texto que o explica, ao lado de um gráfico (faturamento por
    colaborador)."""

    tipo: Literal["numero-grafico"]
    numero: NumeroDestaque
    grafico: Grafico


class DetalheLista(ModeloCamel):
    titulo: str
    grafico: GraficoLinhas


class ItemListaDetalhe(ModeloCamel):
    id: str
    # uma a menos que as colunas: a última é a tendência
    celulas: list[Celula]
    # realizado cheio, comparação tracejada (mesmo formato da tendência da DRE)
    tendencia: TendenciaDre
    detalhe: DetalheLista


class CartaoListaDetalhe(CartaoBase):
    """Lista em que cada linha abre o próprio gráfico ao lado (produtividade por setor). A
    primeira linha começa escolhida."""

    tipo: Literal["lista-detalhe"]
    colunas: list[ColunaTabela]
    itens: list[ItemListaDetalhe]


Cartao = Annotated[
    CartaoAndamentoMes
    | CartaoFatores
    | CartaoContraMeta
    | CartaoTabela
    | CartaoDestaques
    | CartaoGraficos
    | CartaoFaixas
    | CartaoCurvaAbc
    | CartaoMatriz
    | CartaoDre
    | CartaoBarraPartes
    | CartaoDesvios
    | CartaoJanelas
    | CartaoDoisLados
    | CartaoBarrasHorizontais
    | CartaoNumeroGrafico
    | CartaoListaDetalhe,
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
