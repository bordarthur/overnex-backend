"""Contrato dos painéis por cartões (Vendas, Equipe comercial, Clientes e atendimentos,
Resultado, Receber e pagar, Fluxo de caixa, Pessoas e Estoque), espelho de src/contracts/painel.ts
do overnex-frontend.

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
    "vendas",
    "equipe-comercial",
    "clientes",
    "resultado",
    "receber-pagar",
    "fluxo-caixa",
    "pessoas",
    "estoque",
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
    # valores lado a lado ("R$ 1,21 mi · R$ 20 mil"), cada um inteiro; quando vem, o front
    # mostra estes no lugar de `valor` (por enquanto só nas estatísticas do andamento do mês)
    partes: list[Exibicao] | None = None


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
    # id do cartão que mostra este filtro como chave no cabeçalho (a projeção, no saldo
    # projetado); sem ele, o filtro fica na barra
    cartao: str | None = None


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
    "saudavel",
    "atencao",
    "critico",
    "serie",
    "serie-media",
    "serie-clara",
    "fantasma",
    "projecao",
    # cores de série do mockup, fora de status (financiamentos, admissões, desligamentos)
    "serie-azul",
    "serie-laranja",
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
    # media: cinza, rótulo à direita (padrão); minimo: laranja, rótulo à esquerda (o caixa
    # mínimo)
    estilo: Literal["media", "minimo"] | None = None


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
    "linha",
    "linha-clara",
    "pontilhada",
    "media",
    "minimo",
    "quadrado-escuro",
    # o tom da barra `serie-media`
    "quadrado-medio",
    "quadrado-claro",
    # os tons das barras `serie-azul` e `serie-laranja` (ou `atencao`)
    "quadrado-azul",
    "quadrado-laranja",
    # o farol das barras (estoque real contra ideal) e o traço do ideal
    "quadrado-saudavel",
    "quadrado-atencao",
    "quadrado-critico",
    "traco",
]

# tons das faixas de saúde do estoque, que também pintam a barra pequena das listas: o
# vermelho cheio e o claro (zerado e abaixo do mínimo), o azul (ideal) e o laranja e o escuro
# (excesso e parado)
EstiloFaixa = Literal["critico", "critico-claro", "saudavel", "atencao", "atencao-escura"]

# a Classe BCG de um produto (Matriz BCG e as listas do Estoque)
IdQuadranteBcg = Literal["estrela", "vaca-leiteira", "interrogacao", "abacaxi"]


class QuadranteBcg(ModeloCamel):
    # o id é também o id da opção do filtro `classe`; a cor fica no front, pelo id
    id: IdQuadranteBcg
    rotulo: str


class BarraCelula(ModeloCamel):
    proporcao: float
    estilo: EstiloFaixa


class LinkTela(ModeloCamel):
    """Atalho para outra tela do app, pelo trecho da rota ("compras"). O front só mostra se a
    tela existe nele e está em `telas` da sessão."""

    rotulo: str
    tela: str


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
    # cor da etiqueta: azul (padrão, "rampa") ou vermelha ("parou de comprar")
    tom_etiqueta: Literal["azul", "vermelho"] | None = None
    # pílula escura antes do texto (o mês, na semana a semana)
    marca: str | None = None
    # a Classe BCG do produto: a bolinha na cor do quadrante e o nome (listas do Estoque)
    quadrante: QuadranteBcg | None = None
    # barra pequena embaixo do número ("dinheiro preso"): proporção (0 a 1) contra o maior
    barra: BarraCelula | None = None
    # atalho depois do conteúdo ("Comprar →")
    link: LinkTela | None = None
    # valor cru para ordenar a coluna (só nas colunas `ordenavel`): número ou texto
    ordem: float | str | None = None


class ColunaTabela(ModeloCamel):
    rotulo: str
    alinhar: Literal["esquerda", "direita", "centro"] | None = None
    ocultar_no_celular: bool = False
    # o cabeçalho ordena a tabela pelo `ordem` das células desta coluna
    ordenavel: bool | None = None


class LinhaTabela(ModeloCamel):
    # pinta o fundo da linha
    status: Status | None = None
    # traço grosso em cima (outro mês começa)
    separar: bool | None = None
    celulas: list[Celula]


class NomesCartao(ModeloCamel):
    rotulo: str


class CartaoTabela(CartaoBase):
    tipo: Literal["tabela"]
    # calor: células coloridas e centralizadas (atingimento mês a mês); tendencia: para ler a
    # tendência, não número a número (números curtos e farol claro, sem negrito); listrada:
    # linhas alternadas em cinza (semana a semana)
    variante: Literal["padrao", "compacta", "calor", "tendencia", "listrada"] | None = None
    colunas: list[ColunaTabela]
    linhas: list[LinhaTabela]
    vazio: str | None = None
    # chave "Mostrar nomes": só para quem vê dado nominal na tela; ligada, o front pede
    # GET /api/telas/{tela}/cartoes/{id}/nomes
    nomes: NomesCartao | None = None
    # mostra só as primeiras N linhas, com "Ver todas as M" (as listas do Estoque: 10)
    limite: int | None = None
    # texto embaixo da tabela ("Mais 23 produtos abaixo do mínimo fora dos principais.")
    rodape: list[Trecho] | None = None
    # atalho no cabeçalho do cartão ("Ver sugestão de compra em Compras →")
    link: LinkTela | None = None


class ItemRealIdeal(ModeloCamel):
    """Uma linha do "Estoque real contra ideal": seção, família ou produto."""

    # seção: o id da opção do filtro `secao`; família e produto: livres
    id: str
    nome: str
    # "ideal de 12 dias", "4 produtos", "cobertura 2,0 dias · ideal 12"
    sub: str
    # em reais, a custo
    real: Numero
    ideal: Numero
    # "+R$ 117 mil · 1,7×", "−R$ 17 mil · 0,9×", "abaixo do mínimo", "zerado"
    diferenca: str
    status: Status
    # a linha "Outros produtos da seção": apagada e sem abrir
    outros: bool | None = None
    # o nível de baixo; null no produto e no "Outros"
    itens: list["ItemRealIdeal"] | None
    # o subtítulo do cartão com este item aberto
    subtitulo_aberto: str | None = None


class CartaoRealIdeal(CartaoBase):
    """Estoque real contra o ideal, com o traço do ideal e a abertura por nível."""

    tipo: Literal["real-ideal"]
    escala: Escala
    # as seções, do maior desvio para o menor
    itens: list[ItemRealIdeal]
    # o primeiro passo da trilha ("Todas as seções")
    rotulo_raiz: str
    # ids do caminho aberto de início (com o filtro Seção, a seção dele); null: fechado
    aberto: list[str] | None = None


class FaixaSaude(ModeloCamel):
    # zerado, abaixo, ideal, excesso ou parado
    id: str
    rotulo: str
    # "sem saldo", "cobertura abaixo de metade do ideal"
    sub: str
    estilo: EstiloFaixa
    # "38 produtos"
    produtos: Exibicao
    # null: "—" (o zerado não tem valor)
    valor: Valor | None


class ParteSaude(ModeloCamel):
    # id da faixa
    faixa: str
    valor: Numero


class BarraSaude(ModeloCamel):
    # "Em valor", "Em produtos"
    rotulo: str
    total: Exibicao
    # a largura de cada parte é `valor` sobre a soma das partes
    partes: list[ParteSaude]


class GrupoSaude(ModeloCamel):
    # "Falta", "No ponto", "Sobra"
    rotulo: str
    faixas: list[FaixaSaude]


class CartaoSaudeEstoque(CartaoBase):
    """Onde está cada real e cada produto: duas barras empilhadas e as faixas por grupo."""

    tipo: Literal["saude-estoque"]
    barras: list[BarraSaude]
    grupos: list[GrupoSaude]


class TabelaNomes(ModeloCamel):
    """GET /api/telas/{tela}/cartoes/{id}/nomes: o cartão com uma linha por pessoa."""

    # no lugar do subtítulo do cartão
    subtitulo: str | None = None
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
    # índice da paleta das séries (0 a 6, a do empilhado): cada setor na cor dele
    cor: int | None = None
    # gráfico mais alto que o padrão (o saldo projetado)
    alto: bool | None = None


class QuadroCartao(ModeloCamel):
    """Quadro embaixo dos gráficos ("Próximos 30 dias: 58% da média paga"); sem status,
    cinza."""

    rotulo: str
    titulo: str
    texto: str
    status: Status | None = None


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
    # quadros lado a lado embaixo dos gráficos
    quadros: list[QuadroCartao] | None = None
    # texto com destaques no fim do cartão, antes da nota
    rodape: list[Trecho] | None = None


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


class TendenciaLista(ModeloCamel):
    realizado: list[Numero]
    comparacao: list[Numero]


class TendenciaDre(TendenciaLista):
    """Últimos 6 meses. brl: em reais; pct: em % da receita bruta, na base da coluna
    "% receita bruta" da linha. Sem unidade, o front desenha só a faísca."""

    unidade: Literal["brl", "pct"] | None = None


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


class ColunaArvore(ModeloCamel):
    # "abr-26"
    rotulo: str
    # "Abril de 2026"
    titulo: str | None = None
    # a coluna do período inteiro (acumulado), separada dos meses
    total: bool = False


class LinhaArvoreMensal(ModeloCamel):
    id: str
    nome: str
    # 0 = subtotal (Saldo inicial, geração de cada seção, Saldo final); 1 a 4 = níveis da
    # árvore (no fluxo de caixa: 1 = Entradas e Saídas da seção; 2 a 4 = níveis do plano)
    nivel: Literal[0, 1, 2, 3, 4]
    # id da linha de cima na árvore
    pai: str | None = None
    # a última linha (Saldo final), em destaque escuro
    final: bool = False
    # um por coluna, na ordem de `colunas`; None: mês sem movimento (célula vazia)
    valores: list[Numero | None]
    # fundo de farol de cada célula, na ordem de `colunas`
    fundos: list[Status | None] | None = None


class CartaoArvoreMensal(CartaoBase):
    """Árvore de contas com uma coluna por mês (fluxo de caixa realizado): abre por nível."""

    tipo: Literal["arvore-mensal"]
    # cabeçalho da primeira coluna ("Conta (R$)")
    rotulo_conta: str
    colunas: list[ColunaArvore]
    escala: Escala
    nivel_inicial: Literal[1, 2, 3, 4]
    # níveis que a chave oferece
    niveis: list[Literal[1, 2, 3, 4]]
    # na ordem de exibição, com os subtotais
    linhas: list[LinhaArvoreMensal]
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
    # cor do número fora de status: azul o que entra, vermelho o que sai
    cor: Literal["entra", "sai"] | None = None
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
    # a faixa fica só na barra, com o rótulo dentro, sem quadro embaixo (o a vencer)
    so_na_barra: bool | None = None


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
    # texto depois do percentual de cada faixa da idade: "da carteira", "do total"
    rotulo_participacao: str | None = None
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
    # realizado cheio, comparação tracejada
    tendencia: TendenciaLista
    detalhe: DetalheLista


class CartaoListaDetalhe(CartaoBase):
    """Lista em que cada linha abre o próprio gráfico ao lado (produtividade por setor). A
    primeira linha começa escolhida."""

    tipo: Literal["lista-detalhe"]
    colunas: list[ColunaTabela]
    itens: list[ItemListaDetalhe]


class TrechoCiclo(ModeloCamel):
    # texto dentro do trecho ("Estoque 15 d")
    rotulo: str
    # em dias, já calculados; a régua vai até o maior `ate`
    de: Numero
    ate: Numero
    estilo: Literal["estoque", "recebimento", "pagamento", "preso"]


class LinhaCiclo(ModeloCamel):
    rotulo: str
    trechos: list[TrechoCiclo]


class CartaoCicloCaixa(CartaoBase):
    """Ciclo de caixa em réguas de dias: compra → venda → recebe, compra → paga e o dinheiro
    preso."""

    tipo: Literal["ciclo-caixa"]
    linhas: list[LinhaCiclo]
    # frases do rodapé, lado a lado ("Eram 17 dias em março; hoje 22")
    rodape: list[list[Trecho]] | None = None
    # os prazos mês a mês, embaixo
    grafico: GraficoLinhas | None = None


class EtiquetaOrigem(ModeloCamel):
    rotulo: str
    # lancado cinza; nao-lancado e futuro (V2) em bege
    estilo: Literal["lancado", "nao-lancado", "futuro"]


class ItemOrigem(ModeloCamel):
    nome: str
    # com sinal: saída negativa
    valor: Valor
    etiqueta: EtiquetaOrigem
    # fora do saldo da visão escolhida: linha apagada e número riscado
    riscado: bool | None = None


class GrupoOrigem(ModeloCamel):
    titulo: str
    itens: list[ItemOrigem]


class ParteOrigem(ModeloCamel):
    rotulo: str
    # as proporções somam 1
    proporcao: Numero
    estilo: Literal["lancado", "nao-lancado"]


class CartaoOrigemProjecao(CartaoBase):
    """De onde vem a projeção de caixa: o lançado contra o que falta lançar, e uma linha por
    origem (títulos e cada provisão), em entradas e saídas."""

    tipo: Literal["origem-projecao"]
    barra: list[ParteOrigem]
    texto: list[Trecho] | None = None
    grupos: list[GrupoOrigem]


class LinhaProvisao(ModeloCamel):
    """Um grupo das provisões pelo histórico. Valores em reais."""

    id: str
    nome: str
    # segunda linha, menor ("não recorrente: desligado")
    detalhe: str | None = None
    ligado: bool
    # média por mês no período escolhido
    media: Numero
    # lançado nos próximos 30 dias; None: nada lançado
    lancado: Numero | None
    # média − lançado, nunca abaixo de zero
    sugerida: Numero
    # o que a projeção usa nos próximos 30 dias: o valor digitado ou o sugerido
    usado: Numero
    # "personalizado por Ana em 05/11"; None (sai como null): usa o sugerido
    personalizado: str | None


class ColunasProvisoes(ModeloCamel):
    usar: str
    grupo: str
    media: str
    lancado: str
    sugerida: str
    usado: str


class CartaoProvisoes(CartaoBase):
    """Provisões pelo histórico: completa o que ainda não foi lançado com a média do
    período. Cada mudança vai por PUT e vale para o cliente."""

    tipo: Literal["provisoes"]
    periodos: list[OpcaoFiltro]
    periodo: str
    colunas: ColunasProvisoes
    grupos: list[LinhaProvisao]
    # False: só leitura
    editavel: bool


class MudancaPeriodoProvisoes(ModeloCamel):
    """Corpo de PUT /api/telas/fluxo-caixa/cartoes/provisoes/periodo."""

    periodo: str


class MudancaGrupoProvisao(ModeloCamel):
    """Corpo de PUT /api/telas/fluxo-caixa/cartoes/provisoes/grupos/{grupo}. `valor` em
    reais; None volta ao sugerido. Número conferido na rota (negativo ou não numérico →
    422 com a mensagem própria)."""

    ligado: bool
    valor: object = Field(default=None, json_schema_extra={"type": ["number", "null"]})


# ---------- Matriz BCG (Estoque; IdQuadranteBcg e QuadranteBcg ficam lá em cima) ----------


class CorteBcg(ModeloCamel):
    valor: Numero
    # "mediana 4,2×", "mediana 33%"
    rotulo: str


class PontoBcg(ModeloCamel):
    """Uma bolha: família (primeiro nível) ou produto (dentro da família aberta)."""

    id: str
    nome: str
    quadrante: IdQuadranteBcg
    # giro de venda, vezes por mês (o produto sem estoque fica na borda direita)
    x: Numero
    # margem bruta, %
    y: Numero
    # faturamento de 12 meses, em reais (área da bolha)
    tamanho: Numero
    # fora dos filtros Seção ou Classe BCG: bolha esmaecida (não sai da matriz)
    apagado: bool
    # escreve o nome ao lado (os maiores do nível)
    rotular: bool
    # linhas da dica, prontas
    dica: list[ItemValor]
    # só nas famílias: os produtos dela, para abrir com o clique; null nos produtos
    produtos: list["PontoBcg"] | None
    # só nas famílias: a trilha e o subtítulo quando aberta; null nos produtos
    trilha: str | None
    subtitulo_aberto: str | None


class CartaoBcg(CartaoBase):
    """Dispersão de bolhas, giro contra margem, com os cortes nas medianas dos produtos."""

    tipo: Literal["bcg"]
    eixo_x: EixoDispersao
    eixo_y: EixoDispersao
    corte_x: CorteBcg
    corte_y: CorteBcg
    # na ordem: Estrela, Vaca leiteira, Interrogação, Abacaxi
    quadrantes: list[QuadranteBcg]
    # primeiro nível: as famílias
    pontos: list[PontoBcg]
    # "15 famílias · 38 principais produtos · clique numa bolha para abrir"
    trilha: str
    # "Todas as famílias"
    rotulo_voltar: str


class ItemAcaoBcg(ModeloCamel):
    quadrante: IdQuadranteBcg
    rotulo: str
    # "2 abaixo do mínimo", "31% do estoque", "48% do faturamento"
    nota: str
    acao: str
    # produtos, faturado 12m e em estoque (este com status crítico no alerta)
    numeros: list[ItemValor]
    # é o valor do filtro `classe` agora
    selecionado: bool


class CartaoAcoesBcg(CartaoBase):
    """O que fazer em cada quadrante. Clicar num item troca o filtro `classe` para o
    `quadrante` dele; clicar de novo volta a `todas`."""

    tipo: Literal["acoes-bcg"]
    itens: list[ItemAcaoBcg]


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
    | CartaoArvoreMensal
    | CartaoBarraPartes
    | CartaoDesvios
    | CartaoJanelas
    | CartaoDoisLados
    | CartaoBarrasHorizontais
    | CartaoNumeroGrafico
    | CartaoListaDetalhe
    | CartaoCicloCaixa
    | CartaoOrigemProjecao
    | CartaoProvisoes
    | CartaoBcg
    | CartaoAcoesBcg
    | CartaoRealIdeal
    | CartaoSaudeEstoque,
    Field(discriminator="tipo"),
]


class EscopoSetor(ModeloCamel):
    """O painel só vale em alguns setores e soma só as unidades deles (o Estoque: só a
    Distribuidora). Na bandeja "Negócios", o front trava os outros setores e mostra o
    `rotulo` no rodapé."""

    # ids de cfg.setor
    setores: list[str]
    # "Este painel soma só a Distribuidora"
    rotulo: str


class PainelPayload(ModeloCamel):
    tela: TelaPainel
    versao_contrato: Literal["1.0"]
    cliente: Cliente
    # data dos dados (data_snapshot da última carga 'ok'); None até a primeira carga
    dados_ate: date | None
    # quando essa carga terminou, com fuso
    atualizado_em: datetime | None
    # texto de frescor pronto, nas telas que são o retrato do dia (Estoque: "Estoque de hoje,
    # 12/10, atualizado às 9h"); sem ele, o front monta o de sempre com dadosAte
    frescor: str | None = None
    filtros: list[FiltroPainel]
    aviso: Aviso | None = None
    # números grandes do topo (até 3)
    destaques: list[ItemValor]
    # linha de KPIs (no lugar dos destaques, em Clientes)
    kpis: list[Kpi]
    apoios: list[ItemValor]
    cartoes: list[Cartao]
    # painel de um setor; ausente nos painéis de todos os setores. Sem nenhuma unidade pedida
    # do setor, o painel vem só com o `aviso` (cartões e KPIs vazios)
    escopo: EscopoSetor | None = None
