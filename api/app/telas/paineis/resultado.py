"""FIN-02 — Resultado. REGRAS PROVISÓRIAS: confirmar com o negócio (ver TODO.md).

A DRE é a da seção Resultado da Visão geral (app/telas/visao_geral/resultado.py), aberta pelo
plano gerencial (Id_Descritivo) em vez de só por grupo:
- com lançamentos agrupados (f_lancamentos_agrupados_*: a view da DRE do cliente, com a
  regra dele; PJ: jobs/snapshot/azure/f_lancamentos_agrupados_pj.sql), o realizado vem só
  deles, já aberto por conta (receita por seção, CMV por seção, folha gerencial). Tabela
  vazia (a carga dela falhou ou ainda não rodou): a regra abaixo;
- sem eles:
- realizado: lançamentos (f_lancamentos_*; Tipo E soma, S subtrai), despesas do contas a
  pagar (f_despesas_*) pelo de-para cfg.conta_dre, faturado e custo das vendas (resumo
  mensal) e devoluções (f_devolucao_*). Faturado, custo e devoluções não têm Id_Descritivo:
  entram em 100.01 (Venda de produtos), 120.01 (CMV) e 110.02 (Devoluções de venda);
- orçado: f_orcamento_* (menos o comercial e o de ticket), no cenário do filtro ou no padrão
  da Visão geral (`orcamento.cenario` em cfg.parametro_cliente; sem ele, "Base" ou o único).
  Cada conta entra pelo valor absoluto da soma do mês (o DW não grava o sinal), na direção
  do fator dela no plano (uma redutora de custo abate o grupo);
- plano de contas padrão da LANN (cfg.plano_conta, app/telas/plano.py): entram na DRE só as
  contas com competência CO ou CO/CA (as de só caixa, CA, ficam de fora). Lançamento ou
  despesa em conta fora do plano fica fora da DRE e é citado no rodapé. Sem o plano
  cadastrado (antes da migration 17), entram todas as contas dos grupos da DRE;
- o Id_Descritivo tem três níveis: 140 (grupo da DRE, nível 1), 140.01 (nível 2) e 140.01.01
  (nível 3);
- sinal: a Visão geral soma cada grupo em valor absoluto; aqui o grupo entra com o sinal (um
  estorno maior que a despesa deixa o grupo do outro lado). Os números só diferem nesse caso;
- nomes: nível 1 fixo (GRUPOS); níveis 2 e 3 do plano. Sem nome, "Conta 120.03".

Período (filtro visao):
- mes: o último mês fechado com receita (o escolhido em `mes`, se fechado; senão o anterior
  ao mês da última carga), como na Visão geral;
- ano: de janeiro até esse mês;
- projecao: só no mês da última carga (sem `mes`, ou com ele): o mês em aberto no fim.
  Receita, deduções, custos e despesas variáveis (100 a 130) no ritmo dos dias úteis até a
  véspera da carga; as outras contas, o maior entre o lançado e a média dos 3 meses fechados
  anteriores (despesa fixa é lançada aos poucos).

Comparação (`comparar`, do chip "Comparar com" do front; não vai em `filtros`):
- orcado (padrão): o orçado do período. Sem orçado em algum mês do período, a do mês
  anterior, com aviso;
- mes-anterior: o mês antes do período; no acumulado do ano, o mesmo período do ano anterior.

Farol das linhas da DRE (o do mockup):
- receita (grupos 100, 160 e 190) e Receita líquida: realizado ÷ comparação; 100% ou mais,
  saudável; a partir de 97%, atenção;
- custos e despesas: peso na receita líquida contra o da comparação; até 1% acima,
  saudável; até 5%, atenção. Conta com valor e sem comparação: atenção;
- os outros subtotais: margem sobre a receita líquida contra a da comparação; até −0,2 p.p.,
  saudável; até −1,2 p.p., atenção.
Análise vertical sobre a receita bruta; o CMV (120.01) sobre a receita bruta menos as
devoluções (110.02). Diferença com |dif. %| ≥ 3 ganha fundo de farol.

Destaques: receita líquida, EBITDA e lucro líquido (antes das participações, grupo 210).
Apoios: margem bruta e de contribuição (da receita líquida), ponto de equilíbrio (despesas
fixas ÷ margem de contribuição % da receita líquida), margem de segurança (farol
margem_seguranca) e as contas de nível 2 com o maior desvio contra e a favor do resultado.

Cartões: resultado nos 12 meses até o do período, sempre contra o orçado (barra vermelha:
mês 2% pior que o orçado; "leitura rápida" na chamada), DRE em cascata, ponto de equilíbrio
e DRE gerencial. Só com PAINEIS_A_DECIDIR (dev): ponte do EBITDA (só contra o orçado),
maiores desvios de nível 3, o nível 3 da DRE e a tabela financeira (12 meses; fundo contra a
média do indicador, com 3% de tolerância; sem ciclo de caixa e dívida líquida, sem fonte).
"""

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from functools import cached_property
from typing import Any, Literal

from psycopg import sql

from app.core.cache import existe
from app.core.db import Conexao, em_uma_ida
from app.farol.parametro import Farol, farol
from app.seguranca.acesso import ContextoConsolidado
from app.telas.calendario import dias_uteis, feriados, ultimo_dia_do_mes
from app.telas.contrato import Status
from app.telas.moldura import MESES, UltimaCarga
from app.telas.paineis.base import Consulta, Conteudo, Opcoes, em_fileiras, escolher, slug
from app.telas.paineis.contrato import (
    Aviso,
    Cartao,
    CartaoBarraPartes,
    CartaoDesvios,
    CartaoDre,
    CartaoGraficos,
    CartaoTabela,
    Celula,
    Chamada,
    ColunaTabela,
    Escala,
    EscalaPartes,
    FiltroPainel,
    GraficoCartao,
    GraficoCascata,
    GraficoCombo,
    GrupoDesvios,
    ItemDesvio,
    ItemValor,
    LinhaDre,
    LinhaTabela,
    NomesCombo,
    OpcaoFiltro,
    ParteBarra,
    PassoCascata,
    PontoCombo,
    TendenciaDre,
)
from app.telas.paineis.formato import CEM, brl, mais, nome_mes, pct, pct_texto, q
from app.telas.paineis.receber_pagar import indicadores_mensais
from app.telas.plano import Plano, ler_plano, normalizar
from app.telas.visao_geral.contra_referencia import MESES_CURTOS, rotulo_eixo
from app.telas.visao_geral.contrato import Trecho, Valor
from app.telas.visao_geral.resultado import (
    PARAMETRO_CENARIO,
    avisar_uma_vez,
    cenario_do_orcamento,
    cenario_escolhido,
    com_as_colunas,
    consulta_agrupados,
    consulta_despesas,
    consulta_devolucoes,
    consulta_lancamentos,
    tabelas_agrupados,
    tabelas_despesas,
    tabelas_devolucoes,
    tabelas_lancamentos,
    tabelas_orcamento,
)
from app.telas.visao_geral.router import recorte
from app.telas.visao_geral.texto import moeda, numero
from app.telas.visao_geral.vendas import (
    COLUNAS_VENDAS,
    TabelaDoCliente,
    consulta_resumo_mensal,
    de_todas,
    filtro_empresa,
    mes_menos,
    tabelas_do_cliente,
)

ZERO = Decimal(0)
MIL = Decimal(1000)

# conta (código do plano, no nível mais fundo que veio) → valor na direção da DRE:
# positivo = receita nos grupos de receita e despesa nos outros
type Contas = dict[str, Decimal]


@dataclass(frozen=True)
class Grupo:
    nome: str
    # soma no resultado (os outros subtraem)
    receita: bool = False


# na ordem da DRE
GRUPOS: Mapping[str, Grupo] = {
    "100": Grupo("Receita bruta", receita=True),
    "110": Grupo("Deduções da receita"),
    "120": Grupo("Custos"),
    "130": Grupo("Despesas variáveis"),
    "140": Grupo("Despesas fixas"),
    "150": Grupo("Depreciação e amortização"),
    "160": Grupo("Receitas financeiras", receita=True),
    "170": Grupo("Despesas financeiras"),
    "180": Grupo("Despesas não operacionais"),
    "190": Grupo("Receitas não operacionais", receita=True),
    "200": Grupo("Impostos sobre o resultado"),
    "210": Grupo("Participações dos sócios"),
}
# a operação, sem Id_Descritivo: conta de nível 3 própria
FATURADO = "100.01.vendas"
CUSTO_VENDAS = "120.01.vendas"
DEVOLUCOES = "110.02.devolucoes"
NOMES_OPERACAO: Mapping[str, str] = {
    FATURADO: "Faturado das vendas",
    CUSTO_VENDAS: "Custo das vendas",
    DEVOLUCOES: "Devoluções das vendas",
}
FOLHA = "140.01"
CMV = "120.01"
CONTA_DEVOLUCOES = "110.02"

# projeção: grupos que andam com as vendas; os outros, pela média dos meses anteriores
GRUPOS_NO_RITMO = frozenset({"100", "110", "120", "130"})
MESES_MEDIA_PROJECAO = 3
MESES_TENDENCIA = 6
MESES_GRAFICO = 12
# quantos meses antes a folga do ponto de equilíbrio é comparada (como na Visão geral)
MESES_FOLGA = 5
# mês pior que o orçado no gráfico de 12 meses
TOLERANCIA_GRAFICO = Decimal("0.02")
# tabela financeira: célula pintada a 3% da média
TOLERANCIA_TABELA = Decimal("0.03")
DESVIOS_POR_GRUPO = 4

VISOES = ("mes", "ano", "projecao")
Comparar = Literal["orcado", "mes-anterior"]
COMPARAR = Opcoes(
    filtro=FiltroPainel(
        id="comparar",
        rotulo="Comparar com",
        opcoes=[
            OpcaoFiltro(id="orcado", rotulo="Orçado"),
            OpcaoFiltro(id="mes-anterior", rotulo="Mês anterior"),
        ],
        padrao="orcado",
    ),
    valores={"mes-anterior": ("mes-anterior",)},
)


# ---------- plano de contas ----------


def codigo_conta(texto: str | None) -> str | None:
    """Id_Descritivo no formato do plano ("140.01.01", até 3 níveis; ver plano.normalizar);
    None se o grupo não é da DRE."""
    codigo = normalizar(texto)
    if codigo is None:
        return None
    partes = codigo.split(".")[:3]
    return ".".join(partes) if partes[0] in GRUPOS else None


def grupo(conta: str) -> str:
    return conta.split(".")[0]


def sinal(conta: str) -> Decimal:
    """+1 nos grupos de receita, −1 nos outros: valor da DRE → valor exibido."""
    return Decimal(1) if GRUPOS[grupo(conta)].receita else Decimal(-1)


def por_codigo(contas: Contas) -> dict[str, Decimal]:
    """Soma de cada código em todos os níveis: "140", "140.01" e "140.01.01"."""
    saida: dict[str, Decimal] = {}
    for conta, valor in contas.items():
        partes = conta.split(".")
        for n in range(1, len(partes) + 1):
            chave = ".".join(partes[:n])
            saida[chave] = saida.get(chave, ZERO) + valor
    return saida


def somar(contas: Iterable[Contas]) -> Contas:
    saida: Contas = {}
    for c in contas:
        for conta, valor in c.items():
            saida[conta] = saida.get(conta, ZERO) + valor
    return saida


def _dentro(conta: str, codigo: str) -> bool:
    return conta == codigo or conta.startswith(codigo + ".")


@dataclass(frozen=True)
class Totais:
    """Grupos e subtotais da DRE de um período (direção da DRE)."""

    grupos: Mapping[str, Decimal]
    folha: Decimal = ZERO
    devolucoes: Decimal = ZERO

    def g(self, codigo: str) -> Decimal:
        return self.grupos.get(codigo, ZERO)

    @property
    def receita_bruta(self) -> Decimal:
        return self.g("100")

    @property
    def receita_liquida(self) -> Decimal:
        return self.g("100") - self.g("110")

    @property
    def lucro_bruto(self) -> Decimal:
        return self.receita_liquida - self.g("120")

    @property
    def margem_contribuicao(self) -> Decimal:
        return self.lucro_bruto - self.g("130")

    @property
    def despesas_fixas(self) -> Decimal:
        return self.g("140")

    @property
    def ebitda(self) -> Decimal:
        return self.margem_contribuicao - self.g("140")

    @property
    def resultado_antes_ir(self) -> Decimal:
        return (
            self.ebitda
            - self.g("150")
            + self.g("160")
            - self.g("170")
            - self.g("180")
            + self.g("190")
        )

    @property
    def lucro_liquido(self) -> Decimal:
        return self.resultado_antes_ir - self.g("200")

    @property
    def lucro_final(self) -> Decimal:
        return self.lucro_liquido - self.g("210")

    def sobre_rl(self, valor: Decimal) -> Decimal | None:
        """% da receita líquida; None sem receita."""
        rl = self.receita_liquida
        return valor / rl * CEM if rl > 0 else None


def totais(contas: Contas) -> Totais:
    grupos: dict[str, Decimal] = {}
    folha = devolucoes = ZERO
    for conta, valor in contas.items():
        g = grupo(conta)
        grupos[g] = grupos.get(g, ZERO) + valor
        if _dentro(conta, FOLHA):
            folha += valor
        if _dentro(conta, CONTA_DEVOLUCOES):
            devolucoes += valor
    return Totais(grupos=grupos, folha=folha, devolucoes=devolucoes)


@dataclass(frozen=True)
class Subtotal:
    id: str
    nome: str
    # aparece depois deste grupo
    depois_de: str
    valor: Callable[[Totais], Decimal]
    # Receita líquida: farol de receita; os outros, pela margem
    base: bool = False
    # só aparece se este grupo tem movimento
    so_com: str | None = None


SUBTOTAIS: tuple[Subtotal, ...] = (
    Subtotal("sub-receita-liquida", "Receita líquida", "110", lambda t: t.receita_liquida, True),
    Subtotal("sub-lucro-bruto", "Lucro bruto", "120", lambda t: t.lucro_bruto),
    Subtotal(
        "sub-margem-contribuicao", "Margem de contribuição", "130", lambda t: t.margem_contribuicao
    ),
    Subtotal("sub-ebitda", "EBITDA", "140", lambda t: t.ebitda),
    Subtotal(
        "sub-resultado-antes-ir",
        "Resultado antes dos impostos",
        "190",
        lambda t: t.resultado_antes_ir,
        so_com="200",
    ),
    Subtotal("sub-lucro-liquido", "Lucro líquido", "200", lambda t: t.lucro_liquido),
    Subtotal(
        "sub-lucro-liquido-final",
        "Lucro líquido final",
        "210",
        lambda t: t.lucro_final,
        so_com="210",
    ),
)


@dataclass(frozen=True)
class Nomes:
    plano: Plano = field(default_factory=Plano)

    def de(self, codigo: str) -> str:
        if codigo in GRUPOS:
            return GRUPOS[codigo].nome
        if codigo in NOMES_OPERACAO:
            return NOMES_OPERACAO[codigo]
        return self.plano.nome(codigo) or f"Conta {codigo}"


# ---------- leitura ----------


def _consulta_orcamento(
    ctx: ContextoConsolidado, tabelas: list[TabelaDoCliente], de: date, ate: date
) -> Consulta:
    """Orçado por mês, conta e cenário (minúsculas; '' sem cenário)."""
    return sql.SQL(
        """
        with o as ({linhas})
        select date_trunc('month', dia)::date as mes, conta,
               coalesce(lower(trim(cenario)), '') as cenario, sum(valor) as valor
          from o
         where conta is not null
           and dia >= %(de)s and dia < %(ate)s::date + interval '1 month'
         group by 1, 2, 3
        """
    ).format(
        linhas=de_todas(
            tabelas,
            {"dia": "data", "conta": "conta", "valor": "valor", "cenario": "cenario"},
            filtro_empresa(ctx),
        )
    ), {"cliente": ctx.cliente_id, "empresas": list(ctx.empresas), "de": de, "ate": ate}


def consultas(
    conn: Conexao, ctx: ContextoConsolidado, de: date, ate: date, com_agrupados: bool = True
) -> dict[str, Consulta]:
    """As leituras da tela, de `de` até o fim do mês de `ate` (uma ida ao banco). Com os
    lançamentos agrupados do cliente, eles no lugar das fontes da operação."""
    c: dict[str, Consulta] = {}
    if com_agrupados and (agrupados := tabelas_agrupados(conn, ctx)):
        # a view da DRE do cliente já traz vendas, CMV, devoluções e despesas
        c["agrupados"] = consulta_agrupados(ctx, agrupados, de, ate)
    else:
        vendas = tabelas_do_cliente(conn, ctx.cliente_id, "f_vendas_", COLUNAS_VENDAS)
        if vendas and existe(conn, "mart.resumo_mensal"):
            c["vendas"] = consulta_resumo_mensal(ctx, vendas, de, ate)
        if t := tabelas_devolucoes(conn, ctx):
            c["devolucoes"] = consulta_devolucoes(ctx, t, de, ate)
        if t := tabelas_lancamentos(conn, ctx):
            c["lancamentos"] = consulta_lancamentos(ctx, t, de, ate)
        if t := tabelas_despesas(conn, ctx):
            c["despesas"] = consulta_despesas(ctx, t, de, ate)
    if t := com_as_colunas(tabelas_orcamento(conn, ctx)):
        c["orcamento"] = _consulta_orcamento(ctx, t, de, ate)
    return c


@dataclass(frozen=True)
class Dados:
    real: dict[date, Contas]
    # cenário → mês → contas (valor absoluto da soma do mês, na direção do fator do plano)
    orcamento: dict[str, dict[date, Contas]]
    # mês → contas lançadas que não estão no plano (ficam fora da DRE)
    fora_do_plano: dict[date, set[str]] = field(default_factory=dict)


def dados_das_linhas(
    linhas: Mapping[str, Iterable[Mapping[str, Any]]], plano: Plano | None = None
) -> Dados:
    plano = plano or Plano()
    real: dict[date, Contas] = {}
    fora: dict[date, set[str]] = {}

    def somar_em(destino: dict[date, Contas], mes: date, conta: str, valor: Decimal) -> None:
        do_mes = destino.setdefault(mes, {})
        do_mes[conta] = do_mes.get(conta, ZERO) + valor

    for r in linhas.get("vendas", []):
        conta = {"faturado": FATURADO, "custo": CUSTO_VENDAS}.get(r["medida"])
        if conta and r["valor"]:
            somar_em(real, r["mes"], conta, r["valor"])
    for r in linhas.get("devolucoes", []):
        if r["valor"]:
            somar_em(real, r["mes"], DEVOLUCOES, r["valor"])
    # lançamentos e despesas vêm com o sinal do contas a pagar (saída positiva); os
    # agrupados, com o do resultado (receita positiva)
    contas_a_pagar = [*linhas.get("lancamentos", []), *linhas.get("despesas", [])]
    agrupados = [{**r, "valor": -r["valor"]} for r in linhas.get("agrupados", []) if r["valor"]]
    for r in [*contas_a_pagar, *agrupados]:
        conta = codigo_conta(r["conta"])
        if conta is None or not r["valor"]:
            continue
        if not plano.vazio and plano.conta(conta) is None:
            fora.setdefault(r["mes"], set()).add(conta)
            continue
        if not plano.na_dre(conta):
            continue
        somar_em(real, r["mes"], conta, -r["valor"] if GRUPOS[grupo(conta)].receita else r["valor"])

    soma: dict[str, dict[date, Contas]] = {}
    for r in linhas.get("orcamento", []):
        conta = codigo_conta(r["conta"])
        if conta is None or not r["valor"] or not plano.na_dre(conta):
            continue
        somar_em(soma.setdefault(r["cenario"], {}), r["mes"], conta, r["valor"])
    orcamento: dict[str, dict[date, Contas]] = {}
    for cenario, meses in soma.items():
        for mes, contas in meses.items():
            orcamento.setdefault(cenario, {})[mes] = {
                conta: abs(valor) * sentido(conta, plano) for conta, valor in contas.items()
            }
    return Dados(real=real, orcamento=orcamento, fora_do_plano=fora)


def sentido(conta: str, plano: Plano) -> Decimal:
    """Direção da DRE de um valor sem sinal: o fator do plano (+1 soma no resultado) levado
    para a direção do grupo; sem fator, +1 (soma ao grupo)."""
    fator = plano.fator(conta)
    if fator is None:
        return Decimal(1)
    return Decimal(fator) * sinal(conta)


# ---------- projeção ----------


def projetar(parcial: Contas, anteriores: Sequence[Contas], fator: Decimal) -> Contas:
    """O mês em aberto no fim: grupos que andam com as vendas × `fator` (dias úteis do mês ÷
    dias úteis passados); as outras contas, o maior entre o lançado e a média dos
    `anteriores`."""
    saida: Contas = {}
    for conta in set(parcial) | {c for a in anteriores for c in a}:
        valor = parcial.get(conta, ZERO)
        if grupo(conta) in GRUPOS_NO_RITMO:
            projetado = valor * fator
        else:
            media = (
                sum((a.get(conta, ZERO) for a in anteriores), ZERO) / len(anteriores)
                if anteriores
                else ZERO
            )
            projetado = max(valor, media)
        if projetado:
            saida[conta] = projetado
    return saida


def fator_projecao(conn: Conexao, ctx: ContextoConsolidado, carga: UltimaCarga) -> Decimal | None:
    """Dias úteis do mês da carga ÷ dias úteis até a véspera dela; None sem dia útil passado
    ou sem dia útil pela frente."""
    aberto = carga.data_snapshot.replace(day=1)
    fim = ultimo_dia_do_mes(aberto)
    fer = feriados(conn, ctx.cliente_id, aberto, fim)
    total = dias_uteis(aberto, fim, fer)
    passados = dias_uteis(aberto, carga.data_snapshot - timedelta(days=1), fer)
    if passados <= 0 or passados >= total:
        return None
    return Decimal(total) / Decimal(passados)


# ---------- período e comparação ----------


@dataclass(frozen=True)
class Periodo:
    visao: str
    meses: tuple[date, ...]
    # "setembro", "jan a set de 2026", "outubro (projeção)"
    texto: str
    # "Setembro", "De jan a set de 2026", "Projeção de outubro"
    titulo: str

    @property
    def fim(self) -> date:
        return self.meses[-1]


def periodo(visao: str, ref: date, meses_do_ano: Sequence[date], aberto: date) -> Periodo:
    if visao == "projecao":
        return Periodo(
            visao, (aberto,), f"{nome_mes(aberto)} (projeção)", f"Projeção de {nome_mes(aberto)}"
        )
    if visao == "ano" and len(meses_do_ano) > 1:
        intervalo = f"{MESES_CURTOS[meses_do_ano[0].month - 1]} a {MESES_CURTOS[ref.month - 1]}"
        return Periodo(
            visao,
            tuple(meses_do_ano),
            f"{intervalo} de {ref.year}",
            f"De {intervalo} de {ref.year}",
        )
    return Periodo(visao, (ref,), nome_mes(ref), MESES[ref.month - 1])


@dataclass(frozen=True)
class Comparacao:
    tipo: Comparar
    # cabeçalho da coluna: "Orçado", "Agosto", "2025"
    rotulo: str
    # no meio de uma frase: "orçado", "agosto", "2025"
    curto: str
    # "do orçado", "de agosto", "do mesmo período de 2025"
    de: str
    # a comparação de cada mês (tendência)
    mensal: Callable[[date], Contas]


def comparacao_orcado(orcado: Mapping[date, Contas]) -> Comparacao:
    return Comparacao(
        tipo="orcado",
        rotulo="Orçado",
        curto="orçado",
        de="do orçado",
        mensal=lambda m: orcado.get(m, {}),
    )


def comparacao_anterior(p: Periodo, real: Mapping[date, Contas]) -> Comparacao:
    if p.visao == "ano":
        ano = p.fim.year - 1
        return Comparacao(
            tipo="mes-anterior",
            rotulo=str(ano),
            curto=str(ano),
            de=f"do mesmo período de {ano}",
            mensal=lambda m: real.get(date(m.year - 1, m.month, 1), {}),
        )
    anterior = mes_menos(p.meses[0], 1)
    return Comparacao(
        tipo="mes-anterior",
        rotulo=MESES[anterior.month - 1],
        curto=nome_mes(anterior),
        de=f"de {nome_mes(anterior)}",
        mensal=lambda m: real.get(mes_menos(m, 1), {}),
    )


def tem_orcado(orcado: Mapping[date, Contas], meses: Iterable[date]) -> bool:
    return all(totais(orcado.get(m, {})).receita_bruta > 0 for m in meses)


@dataclass(frozen=True)
class Calculo:
    """Os números do período contra a comparação."""

    periodo: Periodo
    comp: Comparacao
    # realizado de cada mês (com o mês em aberto projetado, na visão projeção)
    real_mensal: Mapping[date, Contas]
    orcado_mensal: Mapping[date, Contas]
    nomes: Nomes
    fora_do_plano: Mapping[date, set[str]] = field(default_factory=dict)

    @cached_property
    def real(self) -> Contas:
        return somar(self.real_mensal.get(m, {}) for m in self.periodo.meses)

    @cached_property
    def comparado(self) -> Contas:
        return somar(self.comp.mensal(m) for m in self.periodo.meses)

    @cached_property
    def t(self) -> Totais:
        return totais(self.real)

    @cached_property
    def c(self) -> Totais:
        return totais(self.comparado)

    @property
    def sem_comparacao(self) -> bool:
        return self.c.receita_bruta <= 0

    @cached_property
    def meses_tendencia(self) -> list[date]:
        return [mes_menos(self.periodo.fim, k) for k in range(MESES_TENDENCIA - 1, -1, -1)]


# ---------- farol ----------


def status_receita(real: Decimal, comp: Decimal) -> Status:
    if comp <= 0:
        return "saudavel"
    razao = real / comp
    if razao >= 1:
        return "saudavel"
    return "atencao" if razao >= Decimal("0.97") else "critico"


def status_custo(real: Decimal, comp: Decimal, rl: Decimal, rlc: Decimal) -> Status:
    """Peso do custo na receita líquida contra o da comparação (valores da DRE)."""
    if not comp:
        return "atencao" if real else "saudavel"
    if rl <= 0 or rlc <= 0:
        return "saudavel"
    relativo = (abs(real) / rl) / (abs(comp) / rlc) - 1
    if relativo <= Decimal("0.01"):
        return "saudavel"
    return "atencao" if relativo <= Decimal("0.05") else "critico"


def status_subtotal(real: Decimal, comp: Decimal, rl: Decimal, rlc: Decimal) -> Status:
    """Margem do subtotal sobre a receita líquida contra a da comparação, em p.p."""
    if rl <= 0 or rlc <= 0:
        return "saudavel"
    pp = real / rl * CEM - comp / rlc * CEM
    if pp >= Decimal("-0.2"):
        return "saudavel"
    return "atencao" if pp >= Decimal("-1.2") else "critico"


# ---------- DRE ----------


def mil(v: Decimal) -> Decimal:
    return q(v / MIL, 1)


def linha_dre(
    *,
    id: str,
    nome: str,
    nivel: Literal[0, 1, 2, 3],
    real: Decimal,
    comp: Decimal,
    tendencia: tuple[list[Decimal], list[Decimal]],
    status: Status,
    base: Decimal,
    codigo: str | None = None,
    pai: str | None = None,
) -> LinhaDre:
    """Valores exibidos (receita positiva, custo negativo), em R$ mil."""
    diferenca = real - comp
    percentual = diferenca / abs(comp) * CEM if comp else None
    tom: Status | None = None
    if abs(mil(diferenca)) >= Decimal("0.5"):
        tom = "saudavel" if diferenca > 0 else "critico"
    return LinhaDre(
        id=id,
        codigo=codigo,
        nome=nome,
        nivel=nivel,
        pai=pai,
        realizado=mil(real),
        percentual_receita_bruta=q(abs(real) / base * CEM, 1) if base > 0 else ZERO,
        comparacao=mil(comp),
        diferenca=mil(diferenca),
        diferenca_percentual=None if percentual is None else q(percentual, 1),
        tom_diferenca=tom,
        destacar_diferenca=tom is not None and percentual is not None and abs(percentual) >= 3,
        status=status,
        com_desvio=False,
        tendencia=TendenciaDre(
            realizado=[mil(v) for v in tendencia[0]], comparacao=[mil(v) for v in tendencia[1]]
        ),
    )


def linhas_dre(k: Calculo, niveis: Sequence[int]) -> tuple[list[LinhaDre], list[str]]:
    """As linhas da DRE na ordem de exibição, com os subtotais, e os grupos sem movimento
    (nem realizado nem comparação) no período."""
    real, comp = por_codigo(k.real), por_codigo(k.comparado)
    tr = [por_codigo(k.real_mensal.get(m, {})) for m in k.meses_tendencia]
    tc = [por_codigo(k.comp.mensal(m)) for m in k.meses_tendencia]
    ttr = [totais(k.real_mensal.get(m, {})) for m in k.meses_tendencia]
    ttc = [totais(k.comp.mensal(m)) for m in k.meses_tendencia]
    t, c = k.t, k.c
    rl, rlc = t.receita_liquida, c.receita_liquida
    base_cmv = t.receita_bruta - t.devolucoes

    def tem(codigo: str) -> bool:
        return bool(real.get(codigo) or comp.get(codigo))

    def filhos(codigo: str, nivel: int) -> list[str]:
        return sorted(
            x
            for x in set(real) | set(comp)
            if x.count(".") == nivel - 1 and x.startswith(codigo + ".") and tem(x)
        )

    def conta(codigo: str, nivel: Literal[1, 2, 3], pai: str | None) -> LinhaDre:
        s = sinal(codigo)
        rv, cv = real.get(codigo, ZERO), comp.get(codigo, ZERO)
        status: Status
        if k.sem_comparacao:
            status = "saudavel"
        elif GRUPOS[grupo(codigo)].receita:
            status = status_receita(rv, cv)
        else:
            status = status_custo(rv, cv, rl, rlc)
        numerico = all(p.isdigit() for p in codigo.split("."))
        return linha_dre(
            id=codigo,
            codigo=(f"{codigo}.00" if nivel == 1 else codigo) if numerico else None,
            nome=k.nomes.de(codigo),
            nivel=nivel,
            pai=pai,
            real=s * rv,
            comp=s * cv,
            tendencia=(
                [s * x.get(codigo, ZERO) for x in tr],
                [s * x.get(codigo, ZERO) for x in tc],
            ),
            status=status,
            base=base_cmv if _dentro(codigo, CMV) else t.receita_bruta,
        )

    linhas: list[LinhaDre] = []
    sem_movimento: list[str] = []
    for g in GRUPOS:
        if tem(g):
            linhas.append(conta(g, 1, None))
            for n2 in filhos(g, 2) if 2 in niveis else []:
                linhas.append(conta(n2, 2, g))
                for n3 in filhos(n2, 3) if 3 in niveis else []:
                    linhas.append(conta(n3, 3, n2))
        else:
            sem_movimento.append(g)
        for s in SUBTOTAIS:
            if s.depois_de != g or (s.so_com is not None and not tem(s.so_com)):
                continue
            rv, cv = s.valor(t), s.valor(c)
            status: Status
            if k.sem_comparacao:
                status = "saudavel"
            elif s.base:
                status = status_receita(rv, cv)
            else:
                status = status_subtotal(rv, cv, rl, rlc)
            linhas.append(
                linha_dre(
                    id=s.id,
                    nome=s.nome,
                    nivel=0,
                    real=rv,
                    comp=cv,
                    tendencia=([s.valor(x) for x in ttr], [s.valor(x) for x in ttc]),
                    status=status,
                    base=t.receita_bruta,
                )
            )

    # a última linha (Lucro líquido ou Lucro líquido final) em destaque
    ultimo = max(i for i, x in enumerate(linhas) if x.nivel == 0)
    linhas[ultimo] = linhas[ultimo].model_copy(update={"final": True})
    fora = {x.id for x in linhas if x.status != "saudavel"}
    return [
        x.model_copy(
            update={"com_desvio": any(_dentro(f, x.id) for f in fora) if x.nivel else x.id in fora}
        )
        for x in linhas
    ], sem_movimento


def cartao_dre(k: Calculo, a_decidir: bool) -> CartaoDre:
    niveis: list[Literal[1, 2, 3]] = [1, 2, 3] if a_decidir else [1, 2]
    linhas, sem_movimento = linhas_dre(k, niveis)
    rodape = []
    if sem_movimento:
        grupos = ", ".join(f"{g} {GRUPOS[g].nome}" for g in sem_movimento)
        rodape.append(f"Sem movimento no período, por isso fora da tabela: {grupos}.")
    fora = sorted(set().union(*(k.fora_do_plano.get(m, set()) for m in k.periodo.meses)))
    if fora:
        lista = ", ".join(fora[:10]) + (f" e mais {len(fora) - 10}" if len(fora) > 10 else "")
        rodape.append(f"Lançado em conta fora do plano de contas, fora da DRE: {lista}.")
    rodape.append(f"Comparação: {k.comp.curto} (filtro “Comparar com”).")
    return CartaoDre(
        id="dre-gerencial",
        tipo="dre",
        largura=12,
        titulo=f"DRE gerencial de {k.periodo.texto}, pelo plano de contas",
        subtitulo="Clique numa linha para abrir o nível seguinte. Só aparecem as contas com "
        "movimento ou comparação no período.",
        rotulo_conta="Conta (R$ mil)",
        rotulo_comparacao=k.comp.rotulo,
        escala=Escala(unidade="qtd", casas=0),
        nivel_inicial=2,
        niveis=niveis,
        linhas=linhas,
        rodape=rodape,
    )


# ---------- topo ----------


def _contra(valor: Decimal, comp: Decimal, k: Calculo) -> str:
    """ "2,2% acima do orçado"."""
    if comp <= 0:
        return f"sem {k.comp.curto} para comparar"
    variacao = (valor / comp - 1) * CEM
    lado = "acima" if variacao >= 0 else "abaixo"
    return f"{pct_texto(abs(variacao))} {lado} {k.comp.de}"


def _sobre_rb(valor: Decimal, t: Totais) -> str:
    return pct_texto(valor / t.receita_bruta * CEM) if t.receita_bruta > 0 else "–"


def destaques(k: Calculo) -> list[ItemValor]:
    t, c = k.t, k.c
    rl, rlc = t.receita_liquida, c.receita_liquida
    itens = [
        ItemValor(
            rotulo="Receita líquida",
            valor=brl(rl),
            contexto=_contra(rl, rlc, k),
            status=None if k.sem_comparacao else status_receita(rl, rlc),
        )
    ]
    subtotais: tuple[tuple[str, Callable[[Totais], Decimal]], ...] = (
        ("EBITDA", lambda x: x.ebitda),
        ("Lucro líquido", lambda x: x.lucro_liquido),
    )
    for rotulo, valor in subtotais:
        real, comp = valor(t), valor(c)
        contexto = f"{_sobre_rb(real, t)} da receita bruta"
        if not k.sem_comparacao:
            contexto += f"; {k.comp.curto} {_sobre_rb(comp, c)}"
        itens.append(
            ItemValor(
                rotulo=rotulo,
                valor=brl(real),
                contexto=contexto,
                status=None if k.sem_comparacao else status_subtotal(real, comp, rl, rlc),
            )
        )
    return itens


def ponto_equilibrio(t: Totais) -> tuple[Decimal, Decimal] | None:
    """(ponto de equilíbrio, margem de segurança em %); None sem margem de contribuição
    positiva (não existe ponto de equilíbrio)."""
    rl, mc = t.receita_liquida, t.margem_contribuicao
    if rl <= 0 or mc <= 0:
        return None
    ponto = t.despesas_fixas / (mc / rl)
    return ponto, (rl - ponto) / rl * CEM


def desvios(k: Calculo, nivel: int) -> list[tuple[str, Decimal]]:
    """(código, efeito no resultado) das contas do nível, do pior para o melhor."""
    real, comp = por_codigo(k.real), por_codigo(k.comparado)
    efeitos = []
    for codigo in set(real) | set(comp):
        if codigo.count(".") != nivel - 1:
            continue
        efeito = sinal(codigo) * (real.get(codigo, ZERO) - comp.get(codigo, ZERO))
        if abs(efeito) >= Decimal("0.5"):
            efeitos.append((codigo, efeito))
    return sorted(efeitos, key=lambda x: x[1])


def apoios(k: Calculo, f_seguranca: Farol) -> list[ItemValor]:
    t = k.t
    itens: list[ItemValor] = []
    for rotulo, valor in (
        ("Margem bruta", t.lucro_bruto),
        ("Margem de contribuição", t.margem_contribuicao),
    ):
        margem = t.sobre_rl(valor)
        if margem is not None:
            itens.append(ItemValor(rotulo=rotulo, valor=pct(margem), contexto="da receita líquida"))
    pe = ponto_equilibrio(t)
    if pe is not None:
        ponto, seguranca = pe
        itens += [
            ItemValor(
                rotulo="Ponto de equilíbrio", valor=brl(ponto), contexto="de receita líquida"
            ),
            ItemValor(
                rotulo="Margem de segurança",
                valor=pct(seguranca, 0),
                status=f_seguranca.status(seguranca),
            ),
        ]
    if not k.sem_comparacao:
        efeitos = desvios(k, 2)
        if efeitos and efeitos[0][1] < 0:
            codigo, efeito = efeitos[0]
            itens.append(
                ItemValor(
                    rotulo="Maior ofensor",
                    valor=f"{k.nomes.de(codigo)} {mais(efeito)}",
                    status="critico",
                )
            )
        if efeitos and efeitos[-1][1] > 0:
            codigo, efeito = efeitos[-1]
            itens.append(
                ItemValor(
                    rotulo="Maior favorável",
                    valor=f"{k.nomes.de(codigo)} {mais(efeito)}",
                    status="saudavel",
                )
            )
    return itens


# ---------- cartões ----------


@dataclass(frozen=True)
class Metrica:
    nome: str
    valor: Callable[[Totais], Decimal]
    maior_melhor: bool
    # linha do % da receita bruta
    com_linha: bool


METRICAS_12_MESES = (
    Metrica("Receita bruta", lambda t: t.receita_bruta, True, False),
    Metrica("Margem de contribuição", lambda t: t.margem_contribuicao, True, True),
    Metrica("Despesas fixas", lambda t: t.despesas_fixas, False, True),
    Metrica("EBITDA", lambda t: t.ebitda, True, True),
    Metrica("Lucro líquido", lambda t: t.lucro_liquido, True, True),
)


def pior(valor: Decimal, referencia: Decimal, maior_melhor: bool) -> bool:
    """Pior que a referência por mais de TOLERANCIA_GRAFICO dela."""
    folga = abs(referencia) * TOLERANCIA_GRAFICO
    return valor < referencia - folga if maior_melhor else valor > referencia + folga


def meses_do_grafico(k: Calculo) -> list[date]:
    candidatos = [mes_menos(k.periodo.fim, n) for n in range(MESES_GRAFICO - 1, -1, -1)]
    return [m for m in candidatos if totais(k.real_mensal.get(m, {})).receita_bruta > 0]


def leitura_rapida(
    meses: list[date], real: list[Totais], orc: list[Totais | None]
) -> Chamada | None:
    com_orcado = [(m, r, o) for m, r, o in zip(meses, real, orc, strict=True) if o is not None]
    if len(com_orcado) < 2:
        return None
    receita = len([1 for _, r, o in com_orcado if o and r.receita_bruta >= o.receita_bruta])
    ebitda = len([1 for _, r, o in com_orcado if o and pior(r.ebitda, o.ebitda, True)])
    n = len(com_orcado)
    texto = (
        f"a receita bruta veio acima do orçado em {receita} dos {n} meses; o EBITDA ficou "
        f"abaixo do orçado em {ebitda}."
    )
    inicio, fim = (
        real[0].sobre_rl(real[0].margem_contribuicao),
        real[-1].sobre_rl(real[-1].margem_contribuicao),
    )
    if inicio is not None and fim is not None:
        texto += (
            f" A margem de contribuição foi de {numero(inicio, 1)}% em {rotulo_eixo(meses[0])} "
            f"para {numero(fim, 1)}% em {rotulo_eixo(meses[-1])}."
        )
    _, ultimo_real, ultimo_orc = com_orcado[-1]
    status: Status = "saudavel"
    if ultimo_orc is not None and pior(ultimo_real.ebitda, ultimo_orc.ebitda, True):
        status = "critico"
    elif ebitda:
        status = "atencao"
    return Chamada(
        status=status, texto=[Trecho(texto="Leitura rápida: ", destaque=True), Trecho(texto=texto)]
    )


def cartao_doze_meses(k: Calculo) -> CartaoGraficos | None:
    meses = meses_do_grafico(k)
    if len(meses) < 2:
        return None
    real = [totais(k.real_mensal[m]) for m in meses]
    orc: list[Totais | None] = []
    for m in meses:
        orcado_mes = totais(k.orcado_mensal.get(m, {}))
        orc.append(orcado_mes if orcado_mes.receita_bruta > 0 else None)
    com_orcado = any(o is not None for o in orc)
    projetado = k.periodo.visao == "projecao"
    graficos = []
    for metrica in METRICAS_12_MESES:
        pontos = []
        for i, (m, r, o) in enumerate(zip(meses, real, orc, strict=True)):
            v = metrica.valor(r)
            ref = metrica.valor(o) if o is not None else None
            titulo = rotulo_eixo(m) + (" (projeção)" if projetado and i == len(meses) - 1 else "")
            pontos.append(
                PontoCombo(
                    rotulo=MESES_CURTOS[m.month - 1],
                    titulo=titulo,
                    valor=q(v),
                    referencia=None if ref is None else q(ref),
                    status=(
                        "critico"
                        if ref is not None and pior(v, ref, metrica.maior_melhor)
                        else None
                    ),
                    linha=(
                        q(v / r.receita_bruta * CEM, 1)
                        if metrica.com_linha and r.receita_bruta > 0
                        else None
                    ),
                )
            )
        graficos.append(
            GraficoCartao(
                titulo=metrica.nome,
                grafico=GraficoCombo(
                    tipo="combo",
                    unidade="brl",
                    nomes=NomesCombo(
                        valor="Realizado",
                        referencia="Orçado" if com_orcado else None,
                        linha="% da receita bruta" if metrica.com_linha else None,
                    ),
                    escala_linha=Escala(unidade="pct", casas=1) if metrica.com_linha else None,
                    pontos=pontos,
                ),
            )
        )
    n = len(meses)
    return CartaoGraficos(
        id="resultado-12-meses",
        tipo="graficos",
        largura=12,
        colunas=3,
        titulo="Resultado mês a mês contra o orçado" if com_orcado else "Resultado mês a mês",
        subtitulo=(
            f"Últimos {n} meses. O traço sobre cada barra é o orçado; barra vermelha é mês pior "
            "que o orçado; a linha de cima é o percentual da receita bruta."
            if com_orcado
            else f"Últimos {n} meses. A linha de cima é o percentual da receita bruta."
        ),
        chamada=leitura_rapida(meses, real, orc),
        graficos=graficos,
    )


class Cascata:
    """Monta os passos de uma cascata, somando de onde cada barra parte."""

    def __init__(self, piso: Decimal = ZERO) -> None:
        self.piso = piso
        self.atual = ZERO
        self.passos: list[PassoCascata] = []

    def total(self, rotulo: str, valor: Decimal) -> None:
        self.atual = valor
        self.passos.append(
            PassoCascata(
                rotulo=rotulo, de=q(self.piso), ate=q(valor), valor=q(valor), estilo="total"
            )
        )

    def passo(self, rotulo: str, valor: Decimal) -> None:
        if abs(valor) < Decimal("0.5"):
            return
        novo = self.atual + valor
        self.passos.append(
            PassoCascata(
                rotulo=rotulo,
                de=q(self.atual),
                ate=q(novo),
                valor=q(valor),
                estilo="positivo" if valor > 0 else "negativo",
            )
        )
        self.atual = novo


def cartao_cascata(k: Calculo) -> CartaoGraficos | None:
    t = k.t
    if t.receita_bruta <= 0:
        return None
    c = Cascata()
    c.total("Receita", t.receita_bruta)
    c.passo("Deduções", -t.g("110"))
    c.passo("Custos", -t.g("120"))
    c.passo("Variáveis", -t.g("130"))
    c.passo("Fixas", -t.g("140"))
    c.total("EBITDA", t.ebitda)
    c.passo("D&A", -t.g("150"))
    c.passo("Financeiro", t.g("160") - t.g("170"))
    c.passo("Não operacional", t.g("190") - t.g("180"))
    c.passo("IR/CSLL", -t.g("200"))
    c.total("Lucro", t.lucro_liquido)
    return CartaoGraficos(
        id="dre-cascata",
        tipo="graficos",
        largura=7,
        titulo="DRE em cascata: da receita ao lucro",
        subtitulo=f"{k.periodo.titulo}. Para onde vai cada real vendido",
        graficos=[
            GraficoCartao(grafico=GraficoCascata(tipo="cascata", unidade="brl", passos=c.passos))
        ],
    )


def cartao_ponto_equilibrio(k: Calculo, f_seguranca: Farol) -> CartaoBarraPartes | None:
    t = k.t
    pe = ponto_equilibrio(t)
    if pe is None:
        return None
    ponto, folga = pe
    rl = t.receita_liquida
    margem = t.margem_contribuicao / rl * CEM
    if ponto <= rl:
        proporcao = q(ponto / rl, 3)
        partes = [
            ParteBarra(
                rotulo=f"Equilíbrio {moeda(ponto)}", proporcao=proporcao, estilo="serie-clara"
            ),
            ParteBarra(
                rotulo=f"Folga {numero(folga)}%",
                proporcao=1 - proporcao,
                estilo=f_seguranca.status(folga),
            ),
        ]
        fim = f"Receita líquida de {k.periodo.texto}: {moeda(rl)}"
    else:
        proporcao = q(rl / ponto, 3)
        partes = [
            ParteBarra(
                rotulo=f"Receita líquida {moeda(rl)}", proporcao=proporcao, estilo="serie-clara"
            ),
            ParteBarra(
                rotulo=f"Falta {moeda(ponto - rl)}", proporcao=1 - proporcao, estilo="critico"
            ),
        ]
        fim = f"Ponto de equilíbrio: {moeda(ponto)}"
    inicio_texto = (
        f"Com margem de contribuição de {numero(margem)}%, cada R$ 100 vendidos deixam "
        f"R$ {numero(margem)} para pagar as despesas fixas de {moeda(t.despesas_fixas)}. "
        f"A empresa empata em {moeda(ponto)}"
    )
    if folga >= 0:
        texto = [
            Trecho(texto=f"{inicio_texto} e pode perder até "),
            Trecho(texto=f"{numero(folga)}% das vendas", destaque=True),
            Trecho(texto=" antes de dar prejuízo."),
        ]
    else:
        texto = [
            Trecho(texto=f"{inicio_texto}: a receita precisa crescer "),
            Trecho(texto=f"{numero(ponto / rl * CEM - CEM)}%", destaque=True),
            Trecho(texto=" para pagar as despesas fixas."),
        ]
    if k.periodo.visao != "ano":
        antes = mes_menos(k.periodo.fim, MESES_FOLGA)
        anterior = ponto_equilibrio(totais(k.real_mensal.get(antes, {})))
        if anterior is not None:
            texto.append(
                Trecho(texto=f" Em {nome_mes(antes)}, essa folga era de {numero(anterior[1])}%.")
            )
    return CartaoBarraPartes(
        id="ponto-equilibrio",
        tipo="barra-partes",
        largura=5,
        titulo="Ponto de equilíbrio",
        subtitulo="Quanto a empresa precisa vender para pagar todas as contas",
        partes=partes,
        escala=EscalaPartes(inicio="0", fim=fim),
        texto=texto,
    )


def piso_da_escala(valores: Iterable[Decimal]) -> Decimal | None:
    """Início da escala para as diferenças aparecerem: metade da amplitude abaixo do menor
    valor, arredondado para baixo em 2 algarismos; None se chegaria a zero."""
    vs = list(valores)
    menor, maior = min(vs), max(vs)
    if menor <= 0:
        return None
    piso = menor - (maior - menor) / 2
    if piso <= 0:
        return None
    passo = Decimal(10) ** (len(str(int(piso))) - 2)
    return (piso // passo) * passo if passo >= 1 else q(piso, 0)


def cartao_ponte(k: Calculo) -> CartaoGraficos | None:
    """Do EBITDA orçado ao realizado (só contra o orçado)."""
    t, c = k.t, k.c
    if k.comp.tipo != "orcado" or k.sem_comparacao:
        return None
    efeitos = [
        ("Receita", t.receita_liquida - c.receita_liquida),
        ("Custo da mercadoria", -(t.g("120") - c.g("120"))),
        ("Despesas variáveis", -(t.g("130") - c.g("130"))),
        ("Folha", -(t.folha - c.folha)),
        ("Outras fixas", -((t.g("140") - t.folha) - (c.g("140") - c.folha))),
    ]
    pontos = [c.ebitda]
    for _, v in efeitos:
        pontos.append(pontos[-1] + v)
    piso = piso_da_escala(pontos)
    cascata = Cascata(piso or ZERO)
    cascata.total("Orçado", c.ebitda)
    for rotulo, v in efeitos:
        cascata.passo(rotulo, v)
    cascata.total("Realizado", t.ebitda)
    diferenca = t.ebitda - c.ebitda
    lado = "a menos" if diferenca < 0 else "a mais"
    return CartaoGraficos(
        id="ponte-ebitda",
        tipo="graficos",
        largura=7,
        titulo="Ponte do EBITDA: do orçado ao realizado",
        subtitulo=f"O que explica os {moeda(diferenca)} {lado} em {k.periodo.texto}",
        nota=None
        if piso is None
        else f"Escala a partir de {moeda(piso)} para mostrar as diferenças.",
        graficos=[
            GraficoCartao(
                grafico=GraficoCascata(
                    tipo="cascata", unidade="brl", piso=piso, passos=cascata.passos
                )
            )
        ],
    )


def cartao_desvios(k: Calculo) -> CartaoDesvios | None:
    if k.sem_comparacao:
        return None
    efeitos = desvios(k, 3)
    piores = [e for e in efeitos if e[1] < 0][:DESVIOS_POR_GRUPO]
    melhores = [e for e in reversed(efeitos) if e[1] > 0][:DESVIOS_POR_GRUPO]
    if not piores and not melhores:
        return None

    def itens(lista: list[tuple[str, Decimal]]) -> list[ItemDesvio]:
        return [
            ItemDesvio(nome=k.nomes.de(c), detalhe=GRUPOS[grupo(c)].nome, valor=q(v))
            for c, v in lista
        ]

    grupos = []
    if piores:
        grupos.append(
            GrupoDesvios(titulo="Pioram o resultado", status="critico", itens=itens(piores))
        )
    if melhores:
        grupos.append(
            GrupoDesvios(titulo="Melhoram o resultado", status="saudavel", itens=itens(melhores))
        )
    return CartaoDesvios(
        id="maiores-desvios",
        tipo="desvios",
        largura=5,
        titulo=f"Maiores desvios contra {'o orçado' if k.comp.tipo == 'orcado' else k.comp.curto}",
        subtitulo=f"Contas de nível 3 que mais mexeram no resultado de {k.periodo.texto}",
        escala=Escala(unidade="brl"),
        grupos=grupos,
    )


@dataclass(frozen=True)
class LinhaFinanceira:
    nome: str
    valores: list[Decimal | None]
    valor: Callable[[Decimal], Valor]
    maior_melhor: bool


def _celulas(linha: LinhaFinanceira) -> list[Celula]:
    presentes = [v for v in linha.valores if v is not None]
    media = sum(presentes, ZERO) / len(presentes) if presentes else None
    celulas = [Celula(valor=linha.nome)]
    for v in linha.valores:
        if v is None or media is None:
            celulas.append(Celula(valor="–", tom="apagado"))
            continue
        d = (v - media) * (1 if linha.maior_melhor else -1)
        folga = abs(media) * TOLERANCIA_TABELA
        fundo: Status | None = "saudavel" if d > folga else "critico" if d < -folga else None
        celulas.append(Celula(valor=linha.valor(v), fundo=fundo))
    celulas.append(
        Celula(valor=linha.valor(media), tom="apagado")
        if media is not None
        else Celula(valor="–", tom="apagado")
    )
    return celulas


def _dias(v: Decimal) -> Valor:
    return Valor(valor=q(v, 1), unidade="dias", casas=0)


def cartao_tabela_financeira(
    k: Calculo, conn: Conexao, ctx: ContextoConsolidado
) -> CartaoTabela | None:
    meses = meses_do_grafico(k)
    if len(meses) < 2:
        return None
    t = [totais(k.real_mensal[m]) for m in meses]

    def margem(f: Callable[[Totais], Decimal]) -> list[Decimal | None]:
        return [x.sobre_rl(f(x)) for x in t]

    linhas = [
        LinhaFinanceira("Receita bruta", [x.receita_bruta for x in t], brl, True),
        LinhaFinanceira("Margem bruta", margem(lambda x: x.lucro_bruto), pct, True),
        LinhaFinanceira(
            "Margem de contribuição", margem(lambda x: x.margem_contribuicao), pct, True
        ),
        LinhaFinanceira(
            "Despesas fixas sobre a receita", margem(lambda x: x.despesas_fixas), pct, False
        ),
        LinhaFinanceira("Margem EBITDA", margem(lambda x: x.ebitda), pct, True),
        LinhaFinanceira("Margem líquida", margem(lambda x: x.lucro_liquido), pct, True),
    ]
    # o mês em aberto (projeção) não tem posição fechada dos títulos
    fechados = [m for m in meses if not (k.periodo.visao == "projecao" and m == k.periodo.fim)]
    titulos = indicadores_mensais(conn, ctx, fechados[-1]) if fechados else None
    if titulos is not None:
        for nome, serie, valor, maior_melhor in (
            ("Prazo médio de recebimento", titulos.recebimento, _dias, False),
            ("Prazo médio de pagamento", titulos.pagamento, _dias, True),
            ("Inadimplência", titulos.inadimplencia, pct, False),
        ):
            if serie:
                linhas.append(
                    LinhaFinanceira(nome, [serie.get(m) for m in meses], valor, maior_melhor)
                )
    return CartaoTabela(
        id="tabela-financeira",
        tipo="tabela",
        variante="compacta",
        largura=12,
        titulo="Tabela financeira mês a mês",
        subtitulo=f"Principais indicadores dos últimos {len(meses)} meses. Fundo pela média do "
        "período: melhor ou pior que ela.",
        colunas=[
            ColunaTabela(rotulo="Indicador"),
            *(ColunaTabela(rotulo=MESES_CURTOS[m.month - 1]) for m in meses),
            ColunaTabela(rotulo="Média"),
        ],
        linhas=[LinhaTabela(celulas=_celulas(x)) for x in linhas],
    )


# ---------- montagem ----------


def opcoes_visao(ref: date, aberto: date | None) -> Opcoes:
    opcoes = [
        OpcaoFiltro(id="mes", rotulo=f"{MESES[ref.month - 1]} (fechado)"),
        OpcaoFiltro(id="ano", rotulo=f"Acumulado de {ref.year}"),
    ]
    if aberto is not None:
        opcoes.append(OpcaoFiltro(id="projecao", rotulo=f"Projeção de {nome_mes(aberto)}"))
    return Opcoes(
        filtro=FiltroPainel(id="visao", rotulo="Visão", opcoes=opcoes, padrao="mes"),
        valores={o.id: (o.id,) for o in opcoes},
    )


def opcoes_cenario(cenarios: Sequence[str], padrao: str) -> Opcoes:
    return Opcoes(
        filtro=FiltroPainel(
            id="cenario",
            rotulo="Orçamento",
            opcoes=[OpcaoFiltro(id=slug(c), rotulo=c.capitalize()) for c in sorted(cenarios)],
            padrao=slug(padrao),
        ),
        valores={slug(c): (c,) for c in cenarios},
    )


def montar(
    ctx: ContextoConsolidado,
    escolhido: date | None,
    carga: UltimaCarga,
    pedidos: Mapping[str, str | None],
    a_decidir: bool = False,
) -> Conteudo:
    conn = ctx.sessao.conn
    comparar: Comparar = "mes-anterior" if escolher(COMPARAR, pedidos.get("comparar")) else "orcado"
    if not tabelas_lancamentos(conn, ctx) and not tabelas_agrupados(conn, ctx):
        # como na Visão geral: só com vendas, o EBITDA seria o faturado
        return _vazio(pedidos)

    r = recorte(escolhido, carga.data_snapshot)
    aberto = carga.data_snapshot.replace(day=1)
    limite = r.ultimo_fechado
    projetavel = not r.fechado
    de = min(date(limite.year - 1, 1, 1), mes_menos(limite, MESES_GRAFICO + MESES_TENDENCIA))
    ate = aberto if projetavel else limite
    linhas = em_uma_ida(conn, consultas(conn, ctx, de, ate))
    if "agrupados" in linhas and not linhas["agrupados"]:
        # tabela de agrupados vazia (ex.: a carga dela falhou): a regra sem ela
        linhas = em_uma_ida(conn, consultas(conn, ctx, de, ate, com_agrupados=False))
    plano = ler_plano(conn)
    dados = dados_das_linhas(linhas, plano)
    fechados = sorted(
        m for m, c in dados.real.items() if m <= limite and totais(c).receita_bruta > 0
    )
    if not fechados:
        return _vazio(pedidos)
    ref = fechados[-1]

    real = dict(dados.real)
    projecao: Contas | None = None
    if projetavel and totais(dados.real.get(aberto, {})).receita_bruta > 0:
        fator = fator_projecao(conn, ctx, carga)
        if fator is not None:
            anteriores = [
                dados.real[m]
                for k in range(1, MESES_MEDIA_PROJECAO + 1)
                if (m := mes_menos(aberto, k)) in dados.real
            ]
            projecao = projetar(dados.real[aberto], anteriores, fator)
    # o mês em aberto só entra (projetado) na visão projeção
    real.pop(aberto, None)

    visao_o = opcoes_visao(ref, aberto if projecao is not None else None)
    visao = (escolher(visao_o, pedidos.get("visao")) or ("mes",))[0]
    if visao == "projecao" and projecao is not None:
        real[aberto] = projecao

    cenario_o, cenario = _cenario(conn, ctx, dados, ref)
    escolha = escolher(cenario_o, pedidos.get("cenario"))
    if escolha:
        cenario = escolha[0]
    orcado = dados.orcamento.get(cenario, {}) if cenario is not None else {}

    p = periodo(visao, ref, [m for m in fechados if m.year == ref.year], aberto)
    aviso = None
    if comparar == "orcado" and tem_orcado(orcado, p.meses):
        comp = comparacao_orcado(orcado)
    else:
        comp = comparacao_anterior(p, real)
        if comparar == "orcado":
            aviso = Aviso(
                tom="info",
                texto=[
                    Trecho(texto=f"Sem orçado para {p.texto}: a comparação é com {comp.curto}.")
                ],
            )
    k = Calculo(
        periodo=p,
        comp=comp,
        real_mensal=real,
        orcado_mensal=orcado,
        nomes=Nomes(plano),
        fora_do_plano=dados.fora_do_plano,
    )
    f_seguranca = farol(conn, ctx.cliente_id, "margem_seguranca", ref)

    cartoes: list[Cartao | None] = [
        cartao_doze_meses(k),
        cartao_cascata(k),
        cartao_ponto_equilibrio(k, f_seguranca),
    ]
    if a_decidir:
        cartoes += [cartao_ponte(k), cartao_desvios(k)]
    cartoes.append(cartao_dre(k, a_decidir))
    if a_decidir:
        cartoes.append(cartao_tabela_financeira(k, conn, ctx))
    return Conteudo(
        filtros=[o.filtro for o in (visao_o, cenario_o) if o is not None],
        aviso=aviso,
        destaques=destaques(k),
        apoios=apoios(k, f_seguranca),
        cartoes=em_fileiras([c for c in cartoes if c is not None]),
    )


def _vazio(pedidos: Mapping[str, str | None]) -> Conteudo:
    """Sem DRE: nada a mostrar, e nenhum filtro próprio vale (`comparar` já foi conferido)."""
    for id_filtro, valor in pedidos.items():
        if id_filtro != "comparar":
            escolher(None, valor)
    return Conteudo()


def _cenario(
    conn: Conexao, ctx: ContextoConsolidado, dados: Dados, ref: date
) -> tuple[Opcoes | None, str | None]:
    """O filtro de cenário (só com mais de um) e o cenário padrão; None sem orçado."""
    nomeados = [c for c in dados.orcamento if c]
    if not nomeados:
        return None, "" if "" in dados.orcamento else None
    escolhido = cenario_escolhido(conn, ctx, ref)
    padrao = cenario_do_orcamento(nomeados, escolhido)
    if padrao is None:
        avisar_uma_vez(
            (ctx.cliente_id, escolhido, tuple(sorted(nomeados))),
            "Orçamento do cliente %s sem cenário escolhido (%s em %s; cenários: %s): sem "
            "orçado no Resultado. Cadastre o cenário em cfg.parametro_cliente.",
            ctx.cliente_id,
            repr(escolhido) if escolhido else "nenhum",
            PARAMETRO_CENARIO,
            ", ".join(sorted(nomeados)),
        )
        return None, None
    if len(nomeados) < 2:
        return None, padrao
    return opcoes_cenario(nomeados, padrao), padrao
