"""Painel Estoque (EST-01, "Módulo Distribuidora"; só com unidade do setor Distribuidora,
app/seguranca/setores.py): o aviso, 4 KPIs, a Matriz BCG e o que fazer em cada quadrante, o
estoque real contra o ideal, a saúde do estoque, os críticos (comprar já) e o excesso e os
parados (o dinheiro preso).

É o retrato de hoje: sem mês nem comparação. Filtros: Seção (do cadastro; filtra tudo, e o
real contra ideal vem aberto nela), Classe BCG (a matriz e as duas listas) e "O que mostrar"
(`lista`: tudo, excesso ou parados; só a lista do dinheiro preso, com a chave no cabeçalho
dela). A montagem é a função pura `montar_conteudo`, que a demo também usa.

REGRAS PROVISÓRIAS (propostas em 25/11/2026 e aceitas pelo usuário até ele ajustar; ver
TODO.md):

- **Fontes:** vendas de 12 meses até a data da carga (f_vendas_*: produto, valor e custo),
  saldo de hoje (f_estoque_*: produto e quantidade, somados entre as filiais; saldo negativo
  conta zero) e cadastro (d_bitem_*: nome, família `en_grupo`, seção `en_secao` e custo
  unitário `en_custounitario`).
- **Estoque a custo** = quantidade × custo unitário do cadastro (o DW não guarda o valor do
  estoque). Se a fonte de estoque informar o papel `custo_unitario` (colunas_modelo), vale o
  dela.
- **Principais produtos** = a curva A do faturamento de 12 meses (até 80% do total, contando
  o produto que passa dos 80%), no máximo 40.
- **Margem bruta** = (valor vendido − custo) ÷ valor vendido, em 12 meses.
- **Giro de venda** (vezes por mês) = custo das vendas por mês (12 meses ÷ 12) ÷ estoque de
  hoje a custo. O DW não guarda o estoque dia a dia, então não há estoque médio. Produto
  **sem estoque hoje** e com venda conta como giro acima de qualquer outro (vende e não tem:
  Estrela ou Vaca leiteira), fica na borda direita da matriz, com a dica "sem estoque hoje",
  e está abaixo do mínimo.
- **Medianas (cortes):** mediana simples do giro e da margem dos principais produtos. Os
  filtros não mudam as medianas nem tiram pontos: só apagam os de fora.
- **Quadrante:** giro ≥ mediana e margem ≥ mediana, Estrela; giro ≥ e margem <, Vaca
  leiteira; giro < e margem ≥, Interrogação; os dois abaixo, Abacaxi (empate vai para cima
  ou para a direita).
- **Família:** faturamento e estoque somam os principais dela; margem = (Σ valor − Σ custo) ÷
  Σ valor; giro = Σ custo por mês ÷ Σ estoque a custo; quadrante contra as medianas dos
  produtos.
- **Venda por dia** (a custo) = custo das vendas de 12 meses ÷ 365. O mockup fala em "venda
  média diária de 60 dias, regra D-08": a confirmar com o manual; a troca é só em
  `venda_dia` (DIAS_VENDA e a janela), e vale para a matriz e para o resto.
- **Ideal** = venda por dia × dias ideais (da seção, ou o padrão); **mínimo** (regra D-06) =
  metade do ideal. **Cobertura** = estoque ÷ venda por dia. Dias ideais: parâmetro
  `estoque.dias_ideais` do cliente, `{"padrao": 30, "secoes": {"<seção>": 20}}`; sem ele, 30.
- **Status de uma linha do real contra ideal** (produto, família ou seção, pelas somas):
  zerado ou abaixo do mínimo, crítico; até o ideal, saudável; até 1,5× o ideal, atenção;
  acima, crítico. Diferença: "zerado", "abaixo do mínimo" ou "+R$ 117 mil · 1,7×".
- **Faixas da saúde** (cada produto com estoque ou venda em 12 meses numa só, nesta ordem):
  parado (estoque e sem venda há 90 dias ou mais, regra D-07; sem venda em 12 meses também),
  zerado (saldo zero, com venda), abaixo do mínimo, excesso (acima de 1,5× o ideal) e ideal.
- **KPIs** (o recorte da seção): valor em estoque (e o ideal); acima do ideal = Σ (real −
  ideal) dos produtos em excesso (crítico acima de 20% do estoque, atenção acima de 10%);
  abaixo do mínimo = zerados e abaixo (atenção com algum); cobertura média = Σ estoque ÷ Σ
  venda por dia (saudável entre o mínimo e 1,25× o ideal ponderado).
- **Críticos:** os principais zerados ou abaixo do mínimo, zerados primeiro e depois a menor
  cobertura; o rodapé conta os de fora dos principais. **Em trânsito** (pedidos de compra
  sem recebimento, regra D-12) não tem fonte no DW: "—" em todas as linhas e a nota diz.
- **Dinheiro preso:** os principais em excesso (real − ideal) e os parados, principais ou
  não (o estoque inteiro; o parado fora dos principais conta como Abacaxi), do maior para o
  menor, 10 de início. O rodapé soma e conta o excesso fora dos principais.
- **O que fazer:** conta os principais produtos, respeita o filtro Seção e ignora o filtro
  Classe. Alerta (estoque em vermelho): Abacaxi com mais de 20% do estoque; Estrela com algum
  produto abaixo do mínimo.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from psycopg import sql

from app.admin.textos import FORTALEZA
from app.core.db import em_uma_ida
from app.farol.parametro import parametros_do_cliente, vigente
from app.seguranca.acesso import ContextoConsolidado
from app.telas.contrato import Status
from app.telas.moldura import UltimaCarga
from app.telas.paineis.base import (
    CAMPOS_VENDAS,
    COLUNAS_VENDAS_PAINEL,
    Consulta,
    Conteudo,
    Opcoes,
    Tipo,
    escolher,
    mediana,
    opcoes,
    parametros,
    rotulo,
    selecao,
    slug,
)
from app.telas.paineis.contrato import (
    Aviso,
    BarraCelula,
    BarraSaude,
    Cartao,
    CartaoAcoesBcg,
    CartaoBcg,
    CartaoRealIdeal,
    CartaoSaudeEstoque,
    CartaoTabela,
    Celula,
    ColunaTabela,
    CorteBcg,
    EixoDispersao,
    Escala,
    EstiloFaixa,
    FaixaSaude,
    FiltroPainel,
    GrupoSaude,
    IdQuadranteBcg,
    ItemAcaoBcg,
    ItemLegenda,
    ItemRealIdeal,
    ItemValor,
    LinhaTabela,
    LinkTela,
    OpcaoFiltro,
    ParteSaude,
    PillComRotulo,
    PontoBcg,
    QuadranteBcg,
)
from app.telas.paineis.formato import CEM, brl, dias, pct, q, qtd
from app.telas.visao_geral.contrato import Kpi, Trecho, Valor
from app.telas.visao_geral.texto import moeda, numero
from app.telas.visao_geral.vendas import filtro_empresa, tabelas_do_cliente

COLUNAS_ESTOQUE: Mapping[str, str] = {"produto": "en_codigoproduto", "saldo": "en_saldoestoque"}
COLUNAS_ITEM: Mapping[str, str] = {
    "codigo": "en_codigoproduto",
    "nome": "en_nome",
    "familia": "en_grupo",
    "secao": "en_secao",
    "custo": "en_custounitario",
}

MESES_JANELA = 12
CURVA_A = Decimal("0.8")
MAX_PRINCIPAIS = 40
DIAS_IDEAIS_PADRAO = Decimal(30)
PARAMETRO_DIAS_IDEAIS = "estoque.dias_ideais"
ALERTA_ABACAXI = Decimal(20)
ROTULADOS = 5
SEM_FAMILIA = "Sem família"
INFINITO = Decimal("Infinity")
# venda por dia = custo de 12 meses ÷ DIAS_VENDA
DIAS_VENDA = Decimal(365)
# excesso: acima de 1,5× o ideal
LIMITE_EXCESSO = Decimal("1.5")
# parado: estoque e sem venda há 90 dias ou mais (regra D-07)
DIAS_PARADO = 90
# cobertura média saudável até 1,25× o ideal
COBERTURA_ALTA = Decimal("1.25")
# KPI "Acima do ideal": crítico acima de 20% do estoque, atenção acima de 10%
ACIMA_CRITICO = Decimal(20)
ACIMA_ATENCAO = Decimal(10)
# linhas da lista do dinheiro preso antes do "Ver todas"
LIMITE_LISTA = 10
SEM_SECAO = "sem-secao"
ACAO_PARADO = "Liquidar: promoção com os vendedores"

ESTRELA: IdQuadranteBcg = "estrela"
VACA: IdQuadranteBcg = "vaca-leiteira"
INTERROGACAO: IdQuadranteBcg = "interrogacao"
ABACAXI: IdQuadranteBcg = "abacaxi"
QUADRANTES: tuple[IdQuadranteBcg, ...] = (ESTRELA, VACA, INTERROGACAO, ABACAXI)
ROTULOS: Mapping[IdQuadranteBcg, str] = {
    ESTRELA: "Estrela",
    VACA: "Vaca leiteira",
    INTERROGACAO: "Interrogação",
    ABACAXI: "Abacaxi",
}
ACOES: Mapping[IdQuadranteBcg, str] = {
    ESTRELA: "Repor e não deixar faltar.",
    VACA: "Proteger a margem: negociar custo e segurar preço.",
    INTERROGACAO: "Testar ou decidir: empurrar com o vendedor certo ou sair.",
    ABACAXI: "Liquidar o estoque e não recomprar.",
}
# a ação curta da lista de excesso: no excesso, a Estrela segura a compra
ACOES_EXCESSO: Mapping[IdQuadranteBcg, str] = {
    ESTRELA: "Segurar a compra",
    VACA: "Segurar a compra",
    INTERROGACAO: "Reduzir o próximo pedido",
    ABACAXI: "Liquidar e não recomprar",
}

FILTRO_CLASSE = Opcoes(
    filtro=FiltroPainel(
        id="classe",
        rotulo="Classe BCG",
        opcoes=[
            OpcaoFiltro(id="todas", rotulo="Todas"),
            *(OpcaoFiltro(id=k, rotulo=ROTULOS[k]) for k in QUADRANTES),
        ],
        padrao="todas",
    ),
    valores={k: (k,) for k in QUADRANTES},
)


# ---------- a regra ----------


@dataclass(frozen=True)
class Produto:
    codigo: str
    nome: str
    familia: str
    # valor de en_secao como no cadastro; None sem seção
    secao: str | None
    # 12 meses
    faturado: Decimal
    custo_vendas: Decimal
    # hoje, a custo
    estoque: Decimal
    dias_ideais: Decimal = DIAS_IDEAIS_PADRAO
    # a última venda nos dados (as vendas do mart, até a data da carga); None = nenhuma
    ultima_venda: date | None = None

    @property
    def venda_dia(self) -> Decimal:
        """Custo vendido por dia (12 meses ÷ 365)."""
        return self.custo_vendas / DIAS_VENDA

    @property
    def ideal(self) -> Decimal:
        """Estoque ideal a custo: venda por dia × dias ideais."""
        return self.venda_dia * self.dias_ideais

    @property
    def minimo(self) -> Decimal:
        """Metade do ideal (regra D-06)."""
        return self.ideal / 2

    @property
    def margem(self) -> Decimal:
        return (self.faturado - self.custo_vendas) / self.faturado * CEM

    @property
    def giro(self) -> Decimal | None:
        """Vezes por mês; None = sem estoque hoje (vende e não tem)."""
        return _giro(self.custo_vendas, self.estoque)

    @property
    def cobertura(self) -> Decimal | None:
        """Dias de venda que o estoque cobre; None sem custo de venda (cobre sempre)."""
        if self.custo_vendas <= 0:
            return None
        return self.estoque / self.venda_dia

    @property
    def abaixo_do_minimo(self) -> bool:
        c = self.cobertura
        return c is not None and c < self.dias_ideais / 2


def _giro(custo_vendas: Decimal, estoque: Decimal) -> Decimal | None:
    if estoque <= 0:
        return None
    return custo_vendas / MESES_JANELA / estoque


def _ordem(giro: Decimal | None) -> Decimal:
    return INFINITO if giro is None else giro


def principais(produtos: Iterable[Produto]) -> list[Produto]:
    """A curva A do faturamento de 12 meses (até 80%, com o produto que passa dos 80%), no
    máximo MAX_PRINCIPAIS, do maior para o menor."""
    vendidos = sorted(
        (p for p in produtos if p.faturado > 0), key=lambda p: (-p.faturado, p.codigo)
    )
    total = sum((p.faturado for p in vendidos), Decimal(0))
    saida: list[Produto] = []
    acumulado = Decimal(0)
    for p in vendidos:
        if len(saida) >= MAX_PRINCIPAIS or (saida and acumulado >= total * CURVA_A):
            break
        saida.append(p)
        acumulado += p.faturado
    return saida


@dataclass(frozen=True)
class Medianas:
    giro: Decimal
    margem: Decimal


def medianas(produtos: Sequence[Produto]) -> Medianas:
    """Mediana simples do giro e da margem. O sem estoque conta como o maior giro; se ele
    for a mediana, vale o maior giro que existe."""
    giro = mediana([_ordem(p.giro) for p in produtos])
    if not giro.is_finite():
        finitos = [g for p in produtos if (g := p.giro) is not None]
        giro = max(finitos) if finitos else Decimal(0)
    return Medianas(giro=giro, margem=mediana([p.margem for p in produtos]))


def quadrante(giro: Decimal | None, margem: Decimal, m: Medianas) -> IdQuadranteBcg:
    alto_giro = giro is None or giro >= m.giro
    alta_margem = margem >= m.margem
    if alto_giro:
        return ESTRELA if alta_margem else VACA
    return INTERROGACAO if alta_margem else ABACAXI


@dataclass(frozen=True)
class Familia:
    nome: str
    produtos: tuple[Produto, ...]

    @property
    def faturado(self) -> Decimal:
        return sum((p.faturado for p in self.produtos), Decimal(0))

    @property
    def custo_vendas(self) -> Decimal:
        return sum((p.custo_vendas for p in self.produtos), Decimal(0))

    @property
    def estoque(self) -> Decimal:
        return sum((p.estoque for p in self.produtos), Decimal(0))

    @property
    def margem(self) -> Decimal:
        return (self.faturado - self.custo_vendas) / self.faturado * CEM

    @property
    def giro(self) -> Decimal | None:
        return _giro(self.custo_vendas, self.estoque)


def familias(produtos: Sequence[Produto]) -> list[Familia]:
    """Os principais por família, da maior para a menor (faturado)."""
    grupos: dict[str, list[Produto]] = {}
    for p in produtos:
        grupos.setdefault(p.familia, []).append(p)
    return sorted(
        (Familia(nome, tuple(ps)) for nome, ps in grupos.items()),
        key=lambda f: (-f.faturado, f.nome),
    )


# ---------- textos ----------


def _vezes(v: Decimal) -> str:
    return f"{numero(v, 1)}×"


def _pct(v: Decimal) -> str:
    return f"{numero(v, 0)}%"


def _plural(n: int, um: str, varios: str) -> str:
    return f"{n} {um if n == 1 else varios}"


def _secao_rotulo(secoes: Iterable[str | None]) -> str:
    nomes = sorted({rotulo(s) for s in secoes if s})
    if not nomes:
        return "Sem seção"
    return nomes[0] if len(nomes) == 1 else "Várias"


def _dica(
    secao: str,
    segunda: ItemValor,
    quad: IdQuadranteBcg,
    giro: Decimal | None,
    margem: Decimal,
    faturado: Decimal,
    estoque: Decimal,
) -> list[ItemValor]:
    return [
        ItemValor(rotulo="Seção", valor=secao),
        segunda,
        ItemValor(rotulo="Quadrante", valor=ROTULOS[quad]),
        ItemValor(rotulo="Giro", valor="sem estoque hoje" if giro is None else _vezes(giro)),
        ItemValor(rotulo="Margem", valor=pct(margem)),
        ItemValor(rotulo="Faturamento 12m", valor=brl(faturado)),
        ItemValor(rotulo="Estoque hoje", valor=brl(estoque)),
    ]


# ---------- os cartões ----------


def _x(giro: Decimal | None, borda: Decimal) -> Decimal:
    return q(borda if giro is None else giro, 2)


def _maiores(pontos: list[PontoBcg]) -> list[PontoBcg]:
    """Os ROTULADOS maiores ganham o nome escrito ao lado."""
    ordem = sorted(range(len(pontos)), key=lambda i: -pontos[i].tamanho)[:ROTULADOS]
    return [p.model_copy(update={"rotular": i in ordem}) for i, p in enumerate(pontos)]


def cartao_bcg(
    lista: Sequence[Produto],
    m: Medianas,
    na_secao: Mapping[str, bool],
    classe: IdQuadranteBcg | None,
) -> CartaoBcg:
    """Primeiro nível, as famílias; cada uma leva os produtos dela para abrir."""
    grupos = familias(lista)
    giros = [g for g in (*(p.giro for p in lista), *(f.giro for f in grupos)) if g is not None]
    # a borda direita, para quem está sem estoque
    borda = max([*giros, m.giro]) * Decimal("1.1") if giros or m.giro else Decimal(1)
    sem_estoque = sum(1 for p in lista if p.giro is None)

    pontos: list[PontoBcg] = []
    for fam in grupos:
        produtos: list[PontoBcg] = []
        for p in sorted(fam.produtos, key=lambda p: (-p.faturado, p.codigo)):
            quad = quadrante(p.giro, p.margem, m)
            produtos.append(
                PontoBcg(
                    id=p.codigo,
                    nome=p.nome,
                    quadrante=quad,
                    x=_x(p.giro, borda),
                    y=q(p.margem, 1),
                    tamanho=q(p.faturado, 0),
                    apagado=not na_secao[p.codigo] or (classe is not None and quad != classe),
                    rotular=False,
                    dica=_dica(
                        _secao_rotulo([p.secao]),
                        ItemValor(rotulo="Família", valor=fam.nome),
                        quad,
                        p.giro,
                        p.margem,
                        p.faturado,
                        p.estoque,
                    ),
                    produtos=None,
                    trilha=None,
                    subtitulo_aberto=None,
                )
            )
        quad = quadrante(fam.giro, fam.margem, m)
        n = len(fam.produtos)
        pontos.append(
            PontoBcg(
                id=f"familia-{slug(fam.nome) or 'sem-nome'}",
                nome=fam.nome,
                quadrante=quad,
                x=_x(fam.giro, borda),
                y=q(fam.margem, 1),
                tamanho=q(fam.faturado, 0),
                apagado=not any(na_secao[p.codigo] for p in fam.produtos)
                or (classe is not None and quad != classe),
                rotular=False,
                dica=_dica(
                    _secao_rotulo(p.secao for p in fam.produtos),
                    ItemValor(rotulo="Produtos", valor=qtd(Decimal(n))),
                    quad,
                    fam.giro,
                    fam.margem,
                    fam.faturado,
                    fam.estoque,
                ),
                produtos=_maiores(produtos),
                trilha=f"{fam.nome} · {_plural(n, 'produto', 'produtos')}",
                subtitulo_aberto=(
                    f"Produtos da família {fam.nome}, 12 meses. Os cortes continuam nas "
                    "medianas do recorte."
                ),
            )
        )

    nota = None
    if sem_estoque:
        nota = (
            f"{_plural(sem_estoque, 'produto', 'produtos')} sem estoque hoje "
            f"{'fica' if sem_estoque == 1 else 'ficam'} na borda direita, como giro alto."
        )
    return CartaoBcg(
        id="matriz-bcg",
        tipo="bcg",
        largura=8,
        titulo="Matriz BCG: giro contra margem",
        subtitulo=(
            "Famílias dos principais produtos (curva A do faturamento de 12 meses). Os cortes "
            "são as medianas dos produtos."
        ),
        nota=nota,
        eixo_x=EixoDispersao(unidade="vezes", casas=1, rotulo="Giro de venda (vezes por mês)"),
        eixo_y=EixoDispersao(unidade="pct", casas=0, rotulo="Margem bruta"),
        corte_x=CorteBcg(valor=q(m.giro, 2), rotulo=f"mediana {_vezes(m.giro)}"),
        corte_y=CorteBcg(valor=q(m.margem, 1), rotulo=f"mediana {_pct(m.margem)}"),
        quadrantes=[QuadranteBcg(id=k, rotulo=ROTULOS[k]) for k in QUADRANTES],
        pontos=_maiores(pontos),
        trilha=(
            f"{_plural(len(grupos), 'família', 'famílias')} · "
            f"{_plural(len(lista), 'principal produto', 'principais produtos')} · "
            "clique numa bolha para abrir"
        ),
        rotulo_voltar="Todas as famílias",
    )


def cartao_acoes(
    lista: Sequence[Produto],
    m: Medianas,
    na_secao: Mapping[str, bool],
    classe: IdQuadranteBcg | None,
) -> CartaoAcoesBcg:
    """Um item por quadrante, com os principais da seção escolhida (o filtro Classe não
    muda os números)."""
    da_secao = [p for p in lista if na_secao[p.codigo]]
    por_quad: dict[IdQuadranteBcg, list[Produto]] = {k: [] for k in QUADRANTES}
    for p in da_secao:
        por_quad[quadrante(p.giro, p.margem, m)].append(p)
    total_fat = sum((p.faturado for p in da_secao), Decimal(0))
    total_est = sum((p.estoque for p in da_secao), Decimal(0))

    def parte(v: Decimal, total: Decimal) -> Decimal:
        return v / total * CEM if total > 0 else Decimal(0)

    itens: list[ItemAcaoBcg] = []
    for k in QUADRANTES:
        ps = por_quad[k]
        fat = sum((p.faturado for p in ps), Decimal(0))
        est = sum((p.estoque for p in ps), Decimal(0))
        alerta = False
        if k == ESTRELA:
            abaixo = sum(1 for p in ps if p.abaixo_do_minimo)
            nota = "nenhum abaixo do mínimo" if not abaixo else f"{abaixo} abaixo do mínimo"
            alerta = abaixo > 0
        elif k == ABACAXI:
            fatia = parte(est, total_est)
            nota = f"{_pct(fatia)} do estoque"
            alerta = fatia > ALERTA_ABACAXI
        else:
            nota = f"{_pct(parte(fat, total_fat))} do faturamento"
        status: Status | None = "critico" if alerta else None
        itens.append(
            ItemAcaoBcg(
                quadrante=k,
                rotulo=ROTULOS[k],
                nota=nota,
                acao=ACOES[k],
                numeros=[
                    ItemValor(rotulo="Produtos", valor=qtd(Decimal(len(ps)))),
                    ItemValor(rotulo="Faturado 12m", valor=brl(fat)),
                    ItemValor(rotulo="Em estoque", valor=brl(est), status=status),
                ],
                selecionado=classe == k,
            )
        )
    return CartaoAcoesBcg(
        id="acoes-bcg",
        tipo="acoes-bcg",
        largura=4,
        titulo="O que fazer em cada quadrante",
        subtitulo="Principais produtos, 12 meses. Clique para filtrar a matriz.",
        itens=itens,
    )


def montar_bcg(
    produtos: Sequence[Produto],
    secoes: tuple[str, ...] | None,
    classe: IdQuadranteBcg | None,
) -> tuple[CartaoBcg, CartaoAcoesBcg] | None:
    """Os dois cartões da BCG; None sem nenhum produto vendido em 12 meses.

    `secoes`: os valores de en_secao do filtro (None = todas); `classe`: o quadrante do
    filtro (None = todos)."""
    lista = principais(produtos)
    if not lista:
        return None
    m = medianas(lista)
    escolhidas = None if secoes is None else set(secoes)
    na_secao = {p.codigo: escolhidas is None or p.secao in escolhidas for p in lista}
    return cartao_bcg(lista, m, na_secao, classe), cartao_acoes(lista, m, na_secao, classe)


# ---------- real contra ideal, saúde e as listas ----------


@dataclass(frozen=True)
class Faixa:
    id: str
    rotulo: str
    sub: str
    estilo: EstiloFaixa
    grupo: str


ZERADO, ABAIXO, IDEAL, EXCESSO, PARADO = "zerado", "abaixo", "ideal", "excesso", "parado"
FAIXAS: tuple[Faixa, ...] = (
    Faixa(ZERADO, "Zerado", "sem saldo", "critico", "Falta"),
    Faixa(
        ABAIXO, "Abaixo do mínimo", "cobertura abaixo de metade do ideal", "critico-claro", "Falta"
    ),
    Faixa(IDEAL, "Ideal", "entre o mínimo e 1,5× o ideal", "saudavel", "No ponto"),
    Faixa(EXCESSO, "Excesso", "acima de 1,5× o ideal", "atencao", "Sobra"),
    Faixa(PARADO, "Parado", f"{DIAS_PARADO} dias ou mais sem venda", "atencao-escura", "Sobra"),
)
GRUPOS_SAUDE = ("Falta", "No ponto", "Sobra")


def conta(p: Produto) -> bool:
    """O produto entra no painel: tem estoque hoje ou venda em 12 meses."""
    return p.estoque > 0 or p.faturado > 0


def dias_sem_venda(p: Produto, data: date) -> int | None:
    return None if p.ultima_venda is None else (data - p.ultima_venda).days


def parado(p: Produto, data: date) -> bool:
    """Estoque hoje e nenhuma venda há DIAS_PARADO dias ou mais (regra D-07); sem venda em 12
    meses também conta."""
    if p.estoque <= 0:
        return False
    if p.faturado <= 0:
        return True
    sem = dias_sem_venda(p, data)
    return sem is not None and sem >= DIAS_PARADO


def faixa(p: Produto, data: date) -> str:
    """A faixa da saúde, nesta prioridade: parado, zerado, abaixo do mínimo, excesso, ideal."""
    if parado(p, data):
        return PARADO
    if p.estoque <= 0:
        return ZERADO
    if p.estoque < p.minimo:
        return ABAIXO
    if p.estoque > p.ideal * LIMITE_EXCESSO:
        return EXCESSO
    return IDEAL


def comparar(real: Decimal, ideal: Decimal) -> tuple[Status, str]:
    """Status e o texto da diferença de uma linha do real contra o ideal."""
    if ideal <= 0:
        if real <= 0:
            return "saudavel", "sem estoque nem venda"
        return "critico", f"+{moeda(real)} · sem venda"
    if real <= 0:
        return "critico", "zerado"
    if real < ideal / 2:
        return "critico", "abaixo do mínimo"
    razao = real / ideal
    status: Status = "saudavel" if real <= ideal else "atencao"
    if razao > LIMITE_EXCESSO:
        status = "critico"
    sinal = "+" if real >= ideal else "−"
    return status, f"{sinal}{moeda(abs(real - ideal))} · {_vezes(razao)}"


def _dias_texto(d: Decimal) -> str:
    """12 → "12"; 7,5 → "7,5"."""
    return numero(d, 0 if d == d.to_integral_value() else 1)


def _soma(ps: Iterable[Produto], campo: str) -> Decimal:
    return sum((getattr(p, campo) for p in ps), Decimal(0))


def _item(
    id_: str,
    nome: str,
    sub: str,
    real: Decimal,
    ideal: Decimal,
    itens: list[ItemRealIdeal] | None,
    *,
    outros: bool | None = None,
    subtitulo_aberto: str | None = None,
) -> ItemRealIdeal:
    status, diferenca = comparar(real, ideal)
    return ItemRealIdeal(
        id=id_,
        nome=nome,
        sub=sub,
        real=q(real),
        ideal=q(ideal),
        diferenca=diferenca,
        status=status,
        outros=outros,
        itens=itens,
        subtitulo_aberto=subtitulo_aberto,
    )


def _por_desvio(itens: list[ItemRealIdeal]) -> list[ItemRealIdeal]:
    return sorted(itens, key=lambda i: (-(Decimal(str(i.real)) - Decimal(str(i.ideal))), i.nome))


def id_secao(secao: str | None) -> str:
    """O id da seção: o mesmo da opção do filtro `secao` (base.opcoes)."""
    return slug(secao) if secao and slug(secao) else SEM_SECAO


def _sub_produto(p: Produto) -> str:
    d = _dias_texto(p.dias_ideais)
    if p.estoque <= 0:
        return f"sem estoque · ideal {d} dias"
    c = p.cobertura
    if c is None:
        return f"sem custo de venda · ideal {d}"
    return f"cobertura {numero(c, 1)} dias · ideal {d}"


def cartao_real_ideal(
    produtos: Sequence[Produto], lista: Sequence[Produto], aberta: str | None
) -> CartaoRealIdeal:
    """Seções (todos os produtos) › famílias (só as dos principais, mais a linha "Outros") ›
    produtos (os principais), do maior desvio para o menor."""
    codigos = {p.codigo for p in lista}
    por_secao: dict[str, list[Produto]] = {}
    for p in produtos:
        por_secao.setdefault(id_secao(p.secao), []).append(p)

    secoes: list[ItemRealIdeal] = []
    for sid, ps in por_secao.items():
        nome = _secao_rotulo([ps[0].secao])
        principais_da_secao = [p for p in ps if p.codigo in codigos]
        familias_da_secao: list[ItemRealIdeal] = []
        for fam in familias(principais_da_secao):
            produtos_fam = _por_desvio(
                [
                    _item(p.codigo, p.nome, _sub_produto(p), p.estoque, p.ideal, None)
                    for p in fam.produtos
                ]
            )
            n = len(fam.produtos)
            familias_da_secao.append(
                _item(
                    f"familia-{slug(fam.nome) or 'sem-nome'}",
                    fam.nome,
                    _plural(n, "produto", "produtos"),
                    _soma(fam.produtos, "estoque"),
                    _soma(fam.produtos, "ideal"),
                    produtos_fam,
                    subtitulo_aberto=(
                        f"Produtos da família {fam.nome}, do maior desvio para o menor."
                    ),
                )
            )
        filhos = _por_desvio(familias_da_secao)
        fora = [p for p in ps if p.codigo not in codigos]
        if fora:
            filhos.append(
                _item(
                    f"{sid}-outros",
                    "Outros produtos da seção",
                    "fora dos principais",
                    _soma(fora, "estoque"),
                    _soma(fora, "ideal"),
                    None,
                    outros=True,
                )
            )
        secoes.append(
            _item(
                sid,
                nome,
                f"ideal de {_dias_texto(ps[0].dias_ideais)} dias",
                _soma(ps, "estoque"),
                _soma(ps, "ideal"),
                filhos,
                subtitulo_aberto=(
                    f"Famílias da seção {nome}, do maior desvio para o menor. Clique para "
                    "abrir os produtos."
                ),
            )
        )
    return CartaoRealIdeal(
        id="real-ideal",
        tipo="real-ideal",
        largura=7,
        titulo="Estoque real contra ideal",
        subtitulo=(
            "Por seção, do maior desvio para o menor. Clique para abrir as famílias e os produtos."
        ),
        legenda=[
            ItemLegenda(nome="Até o ideal", marca="quadrado-saudavel"),
            ItemLegenda(nome="Até 1,5× o ideal", marca="quadrado-atencao"),
            ItemLegenda(nome="Mais de 1,5× ou abaixo do mínimo", marca="quadrado-critico"),
            ItemLegenda(nome="Ideal", marca="traco"),
        ],
        escala=Escala(unidade="brl"),
        itens=_por_desvio(secoes),
        rotulo_raiz="Todas as seções",
        aberto=[aberta] if aberta is not None and aberta in por_secao else None,
    )


def _produtos(n: int) -> Valor:
    return qtd(Decimal(n), sufixo="produto" if n == 1 else "produtos")


def cartao_saude(produtos: Sequence[Produto], data: date) -> CartaoSaudeEstoque:
    """Todos os produtos do recorte, cada um numa faixa."""
    por_faixa: dict[str, list[Produto]] = {f.id: [] for f in FAIXAS}
    for p in produtos:
        por_faixa[faixa(p, data)].append(p)
    valor = {f.id: _soma(por_faixa[f.id], "estoque") for f in FAIXAS}
    return CartaoSaudeEstoque(
        id="saude-estoque",
        tipo="saude-estoque",
        largura=5,
        titulo="Saúde do estoque",
        subtitulo="Onde está cada real e cada produto",
        barras=[
            BarraSaude(
                rotulo="Em valor",
                total=brl(_soma(produtos, "estoque")),
                partes=[
                    ParteSaude(faixa=f.id, valor=q(valor[f.id], 0))
                    for f in FAIXAS
                    if f.id != ZERADO and valor[f.id] > 0
                ],
            ),
            BarraSaude(
                rotulo="Em produtos",
                total=_produtos(len(produtos)),
                partes=[
                    ParteSaude(faixa=f.id, valor=Decimal(len(por_faixa[f.id])))
                    for f in FAIXAS
                    if por_faixa[f.id]
                ],
            ),
        ],
        grupos=[
            GrupoSaude(
                rotulo=g,
                faixas=[
                    FaixaSaude(
                        id=f.id,
                        rotulo=f.rotulo,
                        sub=f.sub,
                        estilo=f.estilo,
                        produtos=_produtos(len(por_faixa[f.id])),
                        valor=None if f.id == ZERADO else brl(valor[f.id]),
                    )
                    for f in FAIXAS
                    if f.grupo == g
                ],
            )
            for g in GRUPOS_SAUDE
        ],
    )


def kpis(produtos: Sequence[Produto], data: date, secao_escolhida: bool) -> list[Kpi]:
    """Valor em estoque, acima do ideal, abaixo do mínimo e cobertura média, do recorte."""
    total = _soma(produtos, "estoque")
    ideal = _soma(produtos, "ideal")
    saida = [
        Kpi(
            rotulo="Valor em estoque",
            valor=brl(total),
            contexto=f"a custo · ideal: {moeda(ideal)}",
        )
    ]

    excesso = [p for p in produtos if faixa(p, data) == EXCESSO]
    acima = sum((p.estoque - p.ideal for p in excesso), Decimal(0))
    fatia = acima / total * CEM if total > 0 else Decimal(0)
    status: Status = "saudavel"
    if fatia > ACIMA_CRITICO:
        status = "critico"
    elif fatia > ACIMA_ATENCAO:
        status = "atencao"
    saida.append(
        Kpi(
            rotulo="Acima do ideal",
            valor=brl(acima),
            contexto=(
                f"{_pct(fatia)} do estoque, em {_plural(len(excesso), 'produto', 'produtos')}"
                if excesso
                else "nenhum produto acima de 1,5× o ideal"
            ),
            status=status,
        )
    )

    faltas = [p for p in produtos if faixa(p, data) in (ZERADO, ABAIXO)]
    zerados = sum(1 for p in faltas if faixa(p, data) == ZERADO)
    if not zerados:
        contexto = "nenhum zerado"
    elif len(faltas) == 1:
        contexto = "zerado"
    else:
        contexto = f"{zerados} deles {'zerado' if zerados == 1 else 'zerados'}"
    saida.append(
        Kpi(
            rotulo="Abaixo do mínimo",
            valor=_produtos(len(faltas)),
            contexto=contexto,
            status="atencao" if faltas else "saudavel",
        )
    )

    por_dia = _soma(produtos, "venda_dia")
    if por_dia > 0:
        cobertura = total / por_dia
        dias_ideal = ideal / por_dia
        distintos = {p.dias_ideais for p in produtos if p.venda_dia > 0}
        onde = "da seção" if secao_escolhida else "da empresa"
        contexto = (
            f"ideal {onde}: {_dias_texto(next(iter(distintos)))} dias"
            if len(distintos) == 1
            else f"ideal ponderado: {numero(dias_ideal, 0)} dias"
        )
        bom = dias_ideal / 2 <= cobertura <= dias_ideal * COBERTURA_ALTA
        saida.append(
            Kpi(
                rotulo="Cobertura média",
                valor=dias(cobertura),
                contexto=contexto,
                status="saudavel" if bom else "atencao",
            )
        )
    return saida


def aviso(data: date, hoje: date) -> Aviso:
    quando = f"de hoje, {data:%d/%m}" if data == hoje else f"de {data:%d/%m}"
    return Aviso(
        tom="info",
        texto=[
            Trecho(texto=f"Retrato do estoque {quando}, a custo. "),
            Trecho(
                texto=(
                    "O DW ainda não guarda o estoque dia a dia, então tudo aqui se compara "
                    "com o ideal da empresa."
                )
            ),
        ],
    )


def _quadrante_celula(k: IdQuadranteBcg) -> Celula:
    return Celula(quadrante=QuadranteBcg(id=k, rotulo=ROTULOS[k]), ordem=QUADRANTES.index(k))


def _celula_produto(p: Produto, principal: bool) -> Celula:
    origem = p.familia if principal else "fora dos principais"
    return Celula(valor=p.nome, sub=f"{origem} · {_secao_rotulo([p.secao])}", ordem=p.nome)


def _cru(v: Decimal) -> float:
    return float(q(v, 4))


COLUNAS_CRITICOS = [
    ColunaTabela(rotulo="Produto", alinhar="esquerda", ordenavel=True),
    ColunaTabela(rotulo="Classe BCG", alinhar="esquerda", ordenavel=True, ocultar_no_celular=True),
    ColunaTabela(rotulo="Estoque", ordenavel=True),
    ColunaTabela(rotulo="Mínimo", ordenavel=True, ocultar_no_celular=True),
    ColunaTabela(rotulo="Cobertura", ordenavel=True),
    ColunaTabela(rotulo="Em trânsito", ordenavel=True, ocultar_no_celular=True),
    ColunaTabela(rotulo="Situação", alinhar="esquerda", ordenavel=True),
]
COMPRAR = LinkTela(rotulo="Comprar →", tela="compras")


def tabela_criticos(
    recorte: Sequence[Produto],
    codigos: Mapping[str, IdQuadranteBcg],
    classe: IdQuadranteBcg | None,
    data: date,
) -> CartaoTabela:
    """Os principais zerados ou abaixo do mínimo: zerados primeiro, depois a menor cobertura.
    `codigos`: o quadrante de cada principal."""
    criticos = [
        p
        for p in recorte
        if p.codigo in codigos
        and faixa(p, data) in (ZERADO, ABAIXO)
        and (classe is None or codigos[p.codigo] == classe)
    ]
    criticos.sort(key=lambda p: (p.estoque > 0, p.cobertura or Decimal(0), p.nome))
    linhas: list[LinhaTabela] = []
    for p in criticos:
        zerado = p.estoque <= 0
        cobertura = p.cobertura or Decimal(0)
        d = p.dias_ideais
        linhas.append(
            LinhaTabela(
                celulas=[
                    _celula_produto(p, True),
                    _quadrante_celula(codigos[p.codigo]),
                    Celula(valor=brl(p.estoque), ordem=_cru(p.estoque)),
                    Celula(valor=brl(p.minimo), ordem=_cru(p.minimo)),
                    Celula(
                        valor=Valor(valor=q(cobertura, 1), unidade="dias", casas=1),
                        tom="critico",
                        negrito=True,
                        sub=f"mínimo {_dias_texto(d / 2)} · ideal {_dias_texto(d)}",
                        ordem=_cru(cobertura),
                    ),
                    # sem fonte de pedidos de compra em aberto (regra D-12)
                    Celula(valor="—", tom="apagado", ordem=0),
                    Celula(
                        pill=PillComRotulo(status="critico", rotulo="Zerado")
                        if zerado
                        else PillComRotulo(status="atencao", rotulo="Abaixo do mínimo"),
                        link=COMPRAR,
                        ordem=0 if zerado else 1,
                    ),
                ]
            )
        )
    fora = [p for p in recorte if p.codigo not in codigos and faixa(p, data) in (ZERADO, ABAIXO)]
    rodape: list[Trecho] | None = None
    if fora:
        zerados = sum(1 for p in fora if p.estoque <= 0)
        rodape = [
            Trecho(
                texto=f"Mais {_plural(len(fora), 'produto', 'produtos')} abaixo do mínimo fora "
                f"dos principais{' ' if zerados else '.'}"
            )
        ]
        if zerados:
            rodape.append(Trecho(texto=f"({_plural(zerados, 'zerado', 'zerados')})."))
    return CartaoTabela(
        id="criticos",
        tipo="tabela",
        largura=12,
        titulo="Críticos: comprar já",
        subtitulo=(
            "Principais produtos zerados ou abaixo do mínimo, contando o que já está em trânsito"
        ),
        nota="Em trânsito: o DW ainda não tem os pedidos de compra sem recebimento.",
        colunas=COLUNAS_CRITICOS,
        linhas=linhas,
        vazio="Nenhum produto crítico neste recorte.",
        rodape=rodape,
        link=LinkTela(rotulo="Ver sugestão de compra em Compras →", tela="compras"),
    )


COLUNAS_PRESO = [
    ColunaTabela(rotulo="Produto", alinhar="esquerda", ordenavel=True),
    ColunaTabela(rotulo="Classe BCG", alinhar="esquerda", ordenavel=True, ocultar_no_celular=True),
    ColunaTabela(rotulo="Dinheiro preso", ordenavel=True),
    ColunaTabela(rotulo="Cobertura", ordenavel=True, ocultar_no_celular=True),
    ColunaTabela(rotulo="Sem venda há", ordenavel=True),
    ColunaTabela(rotulo="Ação sugerida", alinhar="esquerda", ocultar_no_celular=True),
]
SEM_ORDEM = 9999


def tabela_preso(
    recorte: Sequence[Produto],
    codigos: Mapping[str, IdQuadranteBcg],
    classe: IdQuadranteBcg | None,
    lista: str,
    data: date,
) -> CartaoTabela:
    """Os principais em excesso (preso = real − ideal) e os parados, principais ou não (preso =
    o estoque inteiro), do maior dinheiro preso para o menor. `lista`: tudo, excesso ou
    parados."""
    linhas_brutas: list[tuple[Decimal, Produto, IdQuadranteBcg, bool]] = []
    for p in recorte:
        f = faixa(p, data)
        principal = p.codigo in codigos
        k = codigos.get(p.codigo, ABACAXI)
        if classe is not None and k != classe:
            continue
        if f == EXCESSO and principal and lista in ("tudo", "excesso"):
            linhas_brutas.append((p.estoque - p.ideal, p, k, False))
        elif f == PARADO and lista in ("tudo", "parados"):
            linhas_brutas.append((p.estoque, p, k, True))
    linhas_brutas.sort(key=lambda x: (-x[0], x[1].nome))
    maior = linhas_brutas[0][0] if linhas_brutas else Decimal(0)

    linhas: list[LinhaTabela] = []
    for preso, p, k, e_parado in linhas_brutas:
        ideal_dias = f"ideal {_dias_texto(p.dias_ideais)}"
        cobertura = p.cobertura
        if e_parado or cobertura is None:
            celula_cobertura = Celula(valor="—", tom="apagado", sub=ideal_dias, ordem=SEM_ORDEM)
        else:
            celula_cobertura = Celula(
                valor=Valor(valor=q(cobertura, 1), unidade="dias", casas=0),
                sub=ideal_dias,
                ordem=_cru(cobertura),
            )
        if e_parado:
            sem = dias_sem_venda(p, data)
            celula_sem = (
                Celula(
                    valor=Valor(valor=Decimal(sem), unidade="dias", casas=0),
                    negrito=True,
                    ordem=sem,
                )
                if sem is not None
                else Celula(valor="mais de 1 ano", negrito=True, ordem=SEM_ORDEM)
            )
        else:
            celula_sem = Celula(valor="vende", tom="apagado", ordem=0)
        linhas.append(
            LinhaTabela(
                celulas=[
                    _celula_produto(p, p.codigo in codigos),
                    _quadrante_celula(k),
                    Celula(
                        valor=brl(preso),
                        negrito=True,
                        barra=BarraCelula(
                            proporcao=float(q(preso / maior, 3)) if maior > 0 else 0.0,
                            estilo="atencao-escura" if e_parado else "atencao",
                        ),
                        ordem=_cru(preso),
                    ),
                    celula_cobertura,
                    celula_sem,
                    Celula(valor=ACAO_PARADO if e_parado else ACOES_EXCESSO[k]),
                ]
            )
        )

    rodape: list[Trecho] | None = None
    if linhas_brutas:
        n = len(linhas_brutas)
        total = sum((x[0] for x in linhas_brutas), Decimal(0))
        fim = " presos."
        if lista != "parados":
            fora = [p for p in recorte if p.codigo not in codigos and faixa(p, data) == EXCESSO]
            if fora:
                fim += (
                    f" Mais {_plural(len(fora), 'produto', 'produtos')} com excesso fora dos "
                    f"principais: {moeda(sum((p.estoque - p.ideal for p in fora), Decimal(0)))}."
                )
        rodape = [
            Trecho(texto=f"{_plural(n, 'produto soma', 'produtos somam')} "),
            Trecho(texto=moeda(total), destaque=True),
            Trecho(texto=fim),
        ]
    return CartaoTabela(
        id="excesso-parados",
        tipo="tabela",
        largura=12,
        titulo="Excesso e parados: o dinheiro preso",
        subtitulo=f"Valor acima do ideal e produtos sem venda há {DIAS_PARADO} dias ou mais",
        colunas=COLUNAS_PRESO,
        linhas=linhas,
        vazio="Nada preso neste recorte.",
        limite=LIMITE_LISTA,
        rodape=rodape,
    )


FILTRO_LISTA = Opcoes(
    filtro=FiltroPainel(
        id="lista",
        rotulo="O que mostrar",
        opcoes=[
            OpcaoFiltro(id="tudo", rotulo="Tudo"),
            OpcaoFiltro(id="excesso", rotulo="Excesso"),
            OpcaoFiltro(id="parados", rotulo="Parados"),
        ],
        padrao="tudo",
        cartao="excesso-parados",
    ),
    valores={"excesso": ("excesso",), "parados": ("parados",)},
)


def montar_conteudo(
    produtos: Sequence[Produto],
    secoes: Sequence[str],
    pedidos: Mapping[str, str | None],
    data: date,
    hoje: date,
) -> Conteudo:
    """O painel a partir dos produtos lidos (função pura: a rota e a demo usam).

    `secoes`: os valores de en_secao do cadastro (opções do filtro); `pedidos`: os filtros
    da consulta (secao, classe e lista); `data`: a da carga; `hoje`: para o texto do aviso."""
    classe_pedida = escolher(FILTRO_CLASSE, pedidos.get("classe"))
    classe = None if classe_pedida is None else next(k for k in QUADRANTES if k == classe_pedida[0])
    lista_pedida = escolher(FILTRO_LISTA, pedidos.get("lista"))
    lista = "tudo" if lista_pedida is None else lista_pedida[0]
    filtro_secao = opcoes("secao", "Seção", secoes, todas=True) if secoes else None
    escolhidas = escolher(filtro_secao, pedidos.get("secao"))
    filtros = [FILTRO_CLASSE.filtro, FILTRO_LISTA.filtro]
    if filtro_secao is not None:
        filtros.insert(0, filtro_secao.filtro)

    todos = [p for p in produtos if conta(p)]
    if not todos:
        return Conteudo(filtros=filtros)
    recorte = [p for p in todos if escolhidas is None or p.secao in set(escolhidas)]
    cartoes: list[Cartao] = []
    lista_principais = principais(todos)
    codigos: dict[str, IdQuadranteBcg] = {}
    bcg = montar_bcg(todos, escolhidas, classe)
    if bcg is not None:
        cartoes += list(bcg)
        m = medianas(lista_principais)
        codigos = {p.codigo: quadrante(p.giro, p.margem, m) for p in lista_principais}
    aberta = pedidos.get("secao") if escolhidas is not None else None
    cartoes += [
        cartao_real_ideal(todos, lista_principais, aberta),
        cartao_saude(recorte, data),
        tabela_criticos(recorte, codigos, classe, data),
        tabela_preso(recorte, codigos, classe, lista, data),
    ]
    return Conteudo(
        filtros=filtros,
        aviso=aviso(data, hoje),
        kpis=kpis(recorte, data, escolhidas is not None),
        cartoes=cartoes,
    )


# ---------- leitura ----------

CAMPOS_ESTOQUE: Mapping[str, tuple[str, Tipo]] = {
    "produto": ("produto", "texto"),
    "saldo": ("saldo", "numero"),
    "custo_unitario": ("custo_unitario", "numero"),
}
CAMPOS_ITEM: Mapping[str, tuple[str, Tipo]] = {
    "codigo": ("codigo", "texto"),
    "nome": ("nome", "texto"),
    "familia": ("familia", "texto"),
    "secao": ("secao", "texto"),
    "custo": ("custo", "numero"),
}


def dias_ideais(valor: Any) -> tuple[Decimal, Mapping[str, Decimal]]:
    """`estoque.dias_ideais` → (padrão, dias por seção pelo slug); inválido vale o padrão."""
    padrao = DIAS_IDEAIS_PADRAO
    por_secao: dict[str, Decimal] = {}
    if isinstance(valor, dict):
        try:
            if valor.get("padrao") is not None:
                padrao = Decimal(str(valor["padrao"]))
            for nome, dias in (valor.get("secoes") or {}).items():
                por_secao[slug(str(nome))] = Decimal(str(dias))
        except (ArithmeticError, AttributeError, ValueError):
            return DIAS_IDEAIS_PADRAO, {}
    return padrao, por_secao


def um_ano_antes(data: date) -> date:
    """A janela de 12 meses é (um ano antes, data]: 29/02 vira 28/02."""
    try:
        return data.replace(year=data.year - 1)
    except ValueError:
        return data.replace(year=data.year - 1, day=28)


def frescor(carga: UltimaCarga, hoje: date) -> str:
    """ "Estoque de hoje, 12/10, atualizado às 9h" (sem "hoje" se a carga é de outro dia)."""
    d = carga.data_snapshot
    quando = f"de hoje, {d:%d/%m}" if d == hoje else f"de {d:%d/%m}"
    texto = f"Estoque {quando}"
    if carga.fim is not None:
        local: datetime = carga.fim.astimezone(FORTALEZA)
        hora = f"{local.hour}h" if local.minute == 0 else f"{local.hour}h{local.minute:02d}"
        texto += f", atualizado às {hora}"
    return texto


def ler_produtos(ctx: ContextoConsolidado, data: date) -> tuple[list[Produto], list[str]] | None:
    """Os produtos com venda em 12 meses ou estoque hoje, e as seções do cadastro (opções do
    filtro). None sem fonte de vendas ou de estoque."""
    conn = ctx.sessao.conn
    c = ctx.cliente_id
    vendas = tabelas_do_cliente(conn, c, "f_vendas_", COLUNAS_VENDAS_PAINEL)
    estoque = tabelas_do_cliente(conn, c, "f_estoque_", COLUNAS_ESTOQUE)
    itens = tabelas_do_cliente(conn, c, "d_bitem_", COLUNAS_ITEM)
    if not vendas or not estoque:
        return None
    filtro = filtro_empresa(ctx)
    inicio = um_ano_antes(data)
    consultas: dict[str, Consulta] = {
        "vendas": (
            sql.SQL(
                """
                select produto,
                       sum(valor) filter (where dia > %(inicio)s) as valor,
                       sum(coalesce(custo, 0)) filter (where dia > %(inicio)s) as custo,
                       max(dia) as ultima
                  from ({vendas}) v
                 where produto is not null and dia <= %(fim)s
                 group by produto
                """
            ).format(vendas=selecao(vendas, CAMPOS_VENDAS, filtro)),
            parametros(ctx, inicio=inicio, fim=data),
        ),
        "estoque": (
            sql.SQL(
                """
                select produto, sum(greatest(coalesce(saldo, 0), 0)) as saldo,
                       max(custo_unitario) as custo_unitario
                  from ({estoque}) e
                 where produto is not null
                 group by produto
                """
            ).format(estoque=selecao(estoque, CAMPOS_ESTOQUE, filtro)),
            parametros(ctx),
        ),
    }
    if itens:
        consultas["itens"] = (
            sql.SQL(
                """
                select distinct on (codigo) codigo, nome, familia, secao, custo
                  from ({itens}) i
                 where codigo is not null
                 order by codigo, nome is null, familia is null
                """
            ).format(itens=selecao(itens, CAMPOS_ITEM, filtro)),
            parametros(ctx),
        )
    linhas = em_uma_ida(conn, consultas)

    padrao, por_secao = dias_ideais(
        vigente(parametros_do_cliente(conn, c), PARAMETRO_DIAS_IDEAIS, data)
    )
    cadastro = {r["codigo"]: r for r in linhas.get("itens", [])}
    vendido = {r["produto"]: r for r in linhas["vendas"]}
    saldo = {r["produto"]: r for r in linhas["estoque"]}
    produtos: list[Produto] = []
    for codigo in vendido.keys() | saldo.keys():
        item = cadastro.get(codigo, {})
        v = vendido.get(codigo, {})
        e = saldo.get(codigo, {})
        custo_unit = e.get("custo_unitario") or item.get("custo") or Decimal(0)
        secao = item.get("secao")
        produtos.append(
            Produto(
                codigo=codigo,
                nome=rotulo(item["nome"]) if item.get("nome") else f"Produto {codigo}",
                familia=rotulo(item["familia"]) if item.get("familia") else SEM_FAMILIA,
                secao=secao,
                faturado=v.get("valor") or Decimal(0),
                custo_vendas=v.get("custo") or Decimal(0),
                estoque=(e.get("saldo") or Decimal(0)) * max(custo_unit, Decimal(0)),
                dias_ideais=por_secao.get(slug(secao), padrao) if secao else padrao,
                ultima_venda=v.get("ultima"),
            )
        )
    secoes = [r["secao"] for r in cadastro.values() if r.get("secao")]
    return produtos, secoes


def montar(
    ctx: ContextoConsolidado,
    escolhido: date | None,
    carga: UltimaCarga,
    pedidos: Mapping[str, str | None],
    a_decidir: bool = False,
) -> Conteudo:
    """O painel do dia da carga (`escolhido` não vale: a tela não tem mês). Sem fonte de
    vendas ou de estoque: só o filtro de classe, sem cartões."""
    escolher(FILTRO_CLASSE, pedidos.get("classe"))
    escolher(FILTRO_LISTA, pedidos.get("lista"))
    hoje = date.today()
    texto = frescor(carga, hoje)
    lidos = ler_produtos(ctx, carga.data_snapshot)
    if lidos is None:
        escolher(None, pedidos.get("secao"))
        return Conteudo(filtros=[FILTRO_CLASSE.filtro], frescor=texto)
    produtos, secoes = lidos
    conteudo = montar_conteudo(produtos, secoes, pedidos, carga.data_snapshot, hoje)
    conteudo.frescor = texto
    return conteudo
