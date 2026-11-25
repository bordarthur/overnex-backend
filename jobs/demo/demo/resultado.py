"""DRE (f_lancamentos_agrupados_pj) e orçamento (f_orcamento_pj), pelo plano da LANN.

Realizado (valor com o sinal do resultado: receita positiva, custo e despesa negativos), por
filial e mês de competência:
- receita e CMV de cada linha, das vendas;
- impostos, devoluções, descontos, comissões, tarifas de boleto e IRPJ/CSLL pelas taxas
  sobre o faturado (no mês em aberto, só os que acompanham a venda);
- despesas com título (frete de compra, frota, despesas fixas, juros de empréstimo) pelo mês
  da emissão;
- folha e depreciação só nos meses fechados;
- juros de clientes, juros de mora, rendimento e tarifas bancárias pelo fluxo de caixa.

Orçado (cenário "Base", valores sem sinal): a curva esperada de venda (ORCADO_VENDAS), a
margem de base das linhas, as despesas fixas sem variação e a folha do mês com 2% a menos.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from demo.cadastros import Cadastro
from demo.calendario import inicio_mes, meses, somar_meses
from demo.fluxo import Movimento
from demo.folha import folha_do_mes
from demo.modelo import (
    COFINS,
    COMBUSTIVEL,
    COMISSAO,
    CONTA,
    DEPRECIACAO,
    DESCONTOS,
    DESCONTOS_CONCEDIDOS,
    DEVOLUCOES,
    DEVOLUCOES_VENDA,
    EMPRESAS,
    FORTALEZA,
    FRETE_COMPRAS,
    FRETE_TERCEIROS,
    ICMS,
    INICIO,
    IRPJ_CSLL,
    JUROS_CLIENTES,
    JUROS_MORA,
    LINHAS,
    PIS,
    RENDIMENTO,
    TARIFA_BOLETO,
    VALOR_MEDIO_PEDIDO,
    Empresa,
    inflacao,
    receita_esperada,
)
from demo.sorteio import lognormal, rng
from demo.titulos import FIXAS, TituloPagar, TituloReceber, emprestimos, investimentos
from demo.vendas import ItemVenda

# contas do a pagar que entram na DRE pela emissão (as outras vêm por taxa, pela folha ou
# ficam fora: compra de mercadoria, amortização, investimentos, retirada)
PELO_TITULO = frozenset({"201002", "502002", "502003", "502005", "601001"} | {f[0] for f in FIXAS})
# contas que vêm do fluxo de caixa
PELO_CAIXA = frozenset({JUROS_CLIENTES, JUROS_MORA, RENDIMENTO, "170.01.02"})
# sobre o faturado: (conta, taxa, também no mês em aberto)
TAXAS = (
    ("110.03.04", ICMS, True),
    ("110.03.01", PIS, True),
    ("110.03.02", COFINS, True),
    ("130.01.01", COMISSAO, True),
    ("200.01.01", IRPJ_CSLL, False),
)
DEPRECIACAO_BASE = 30_000.0
# a venda realizada fica ~5% abaixo da curva (rampa dos vendedores novos e descontos): o
# orçado a 98% dela deixa o realizado perto do orçado, ora acima, ora abaixo
ORCADO_VENDAS = 0.98
PARTE_FORTALEZA = 0.65


@dataclass(frozen=True)
class LinhaDre:
    mes: date
    empresa: Empresa
    conta: str
    valor: float


def depreciacao(m: date, empresa: Empresa) -> float:
    """Base fixa (65% em Fortaleza) mais 1/60 ao mês do que foi investido antes do mês."""
    parte = PARTE_FORTALEZA if empresa is FORTALEZA else 1 - PARTE_FORTALEZA
    investido = sum(i.valor for i in investimentos() if i.empresa is empresa and i.data < m)
    return DEPRECIACAO_BASE * parte * inflacao(m) + investido / 60


def gerar_dre(
    cad: Cadastro,
    itens: list[ItemVenda],
    receber: list[TituloReceber],
    pagar: list[TituloPagar],
    movimentos: list[Movimento],
    hoje: date,
) -> list[LinhaDre]:
    soma: dict[tuple[date, str, str], float] = defaultdict(float)
    faturado: dict[tuple[date, str], float] = defaultdict(float)
    linhas_por_nome = {linha.nome: linha for linha in LINHAS}
    for it in itens:
        m = inicio_mes(it.data)
        linha = linhas_por_nome[it.linha]
        soma[(m, it.empresa.id, linha.conta_receita)] += it.valor
        soma[(m, it.empresa.id, linha.conta_cmv)] -= it.custo
        faturado[(m, it.empresa.id)] += it.valor
    boletos: dict[tuple[date, str], int] = defaultdict(int)
    for t in receber:
        boletos[(inicio_mes(t.emissao), t.empresa.id)] += 1
    for p in pagar:
        if p.conta in PELO_TITULO:
            soma[(inicio_mes(p.emissao), p.empresa.id, CONTA[p.conta].id_descritivo)] -= p.valor
    for mov in movimentos:
        if mov.conta in PELO_CAIXA:
            soma[(inicio_mes(mov.data), mov.empresa.id, mov.conta)] += mov.valor

    mes_hoje = inicio_mes(hoje)
    for m in meses(INICIO, mes_hoje):
        fechado = m < mes_hoje
        for e in EMPRESAS:
            receita = faturado[(m, e.id)]
            for conta, taxa, em_aberto in TAXAS:
                if fechado or em_aberto:
                    soma[(m, e.id, conta)] -= receita * taxa
            ruido = lognormal(rng("devolucoes", e.id, m), 0.25)
            soma[(m, e.id, DEVOLUCOES_VENDA)] -= receita * DEVOLUCOES * ruido
            soma[(m, e.id, DESCONTOS_CONCEDIDOS)] -= receita * DESCONTOS
            soma[(m, e.id, "130.02.01")] -= boletos[(m, e.id)] * TARIFA_BOLETO
            if fechado:
                soma[(m, e.id, DEPRECIACAO)] -= depreciacao(m, e)
                for linha_folha in folha_do_mes(cad, m):
                    if linha_folha.empresa is e:
                        soma[(m, e.id, linha_folha.conta)] -= linha_folha.valor

    empresas = {e.id: e for e in EMPRESAS}
    return [
        LinhaDre(m, empresas[e], conta, round(valor, 2))
        for (m, e, conta), valor in sorted(soma.items())
        if round(valor, 2) != 0
    ]


@dataclass(frozen=True)
class LinhaOrcamento:
    mes: date
    empresa: Empresa
    conta: str
    valor: float


def gerar_orcamento(cad: Cadastro, hoje: date) -> list[LinhaOrcamento]:
    """Orçado de INICIO até dezembro do ano de daqui a 2 meses."""
    fim = date(somar_meses(hoje, 2).year, 12, 1)
    saida: list[LinhaOrcamento] = []
    juros: dict[date, float] = defaultdict(float)
    for emp in emprestimos():
        for m, j, _ in emp.prestacoes():
            juros[m] += j
    for m in meses(INICIO, fim):
        folha = folha_do_mes(cad, m)
        for e in EMPRESAS:
            valores: dict[str, float] = defaultdict(float)
            esperado = receita_esperada(m, e) * ORCADO_VENDAS
            for linha in LINHAS:
                valores[linha.conta_receita] += esperado * linha.participacao
                valores[linha.conta_cmv] += esperado * linha.participacao * (1 - linha.margem)
            for conta, taxa, _ in TAXAS:
                valores[conta] += esperado * taxa
            valores[DEVOLUCOES_VENDA] += esperado * DEVOLUCOES
            valores[DESCONTOS_CONCEDIDOS] += esperado * DESCONTOS
            valores["130.03.01"] += esperado * COMBUSTIVEL
            valores["130.03.04"] += esperado * FRETE_TERCEIROS
            valores["130.03.03"] += (18_000 if e is FORTALEZA else 9_000) * inflacao(m)
            valores["130.02.01"] += esperado / VALOR_MEDIO_PEDIDO * 1.8 * TARIFA_BOLETO
            valores["120.04.04"] += esperado * 0.69 * FRETE_COMPRAS
            for conta, _, v_for, v_rec, _, _, _ in FIXAS:
                valor = v_for if e is FORTALEZA else v_rec
                valores[CONTA[conta].id_descritivo] += valor * inflacao(m)
            for linha_folha in folha:
                if linha_folha.empresa is e:
                    valores[linha_folha.conta] += linha_folha.valor * 0.98
            valores[DEPRECIACAO] += depreciacao(m, e)
            if e is FORTALEZA:
                valores["170.01.05"] += juros[m]
            saida.extend(
                LinhaOrcamento(m, e, conta, round(valor, -2))
                for conta, valor in sorted(valores.items())
                if round(valor, -2) > 0
            )
    return saida
