"""Títulos a receber (f_receitas_pj) e a pagar (f_despesas_pj).

A receber: cada pedido vira 1 a 3 parcelas, pelo prazo do cliente. O pagamento depende do
perfil do cliente (pontual, tardio, ruim ou calote) e é sorteado por título, então já está
decidido desde a emissão: o arquivo do dia mostra pago só o que foi pago antes da data da
carga. Calote: a partir de ~45 dias antes da data do calote, nada é pago; 40% desses
clientes pagam tudo de 100 a 160 dias depois do vencimento, e o resto vira baixa por perda
um ano depois (pagamento com valor pago zero).

A pagar: compras das indústrias pela margem esperada das linhas, frete de compra, folha e
encargos (da folha do mês), impostos e comissões (do faturamento realizado), despesas fixas,
empréstimos e investimentos. Só entra o que foi emitido antes da data da carga.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from demo.cadastros import Cadastro
from demo.calendario import (
    anterior_util,
    dia_do_mes,
    dia_util_do_mes,
    inicio_mes,
    meses,
    proximo_util,
    somar_meses,
    ultimo_dia,
)
from demo.folha import folha_do_mes
from demo.modelo import (
    COFINS,
    COMBUSTIVEL,
    COMISSAO,
    EMPRESAS,
    FORTALEZA,
    FRETE_COMPRAS,
    FRETE_TERCEIROS,
    HORIZONTE,
    ICMS,
    INICIO,
    IRPJ_CSLL,
    LINHAS,
    PIS,
    RECIFE,
    TARIFA_BOLETO,
    Empresa,
    inflacao,
    margem,
    receita_esperada,
)
from demo.sorteio import lognormal, rng
from demo.vendas import ItemVenda, Pedido

# juros de mora ao dia (~1% ao mês)
JUROS_DIA = 0.00033


@dataclass
class TituloReceber:
    numdoc: str
    emissao: date
    vencimento: date
    pagamento: date | None
    valor: float
    valor_pago: float | None
    juro: float
    cliente: str
    vendedor: str
    empresa: Empresa
    observacao: str
    # parte de cada linha no pedido (para abrir o recebimento no fluxo de caixa)
    por_linha: dict[str, float] = field(default_factory=dict)


@dataclass
class TituloPagar:
    numdoc: str
    numlanc: str
    emissao: date
    vencimento: date
    pagamento: date | None
    conta: str
    fornecedor: str
    valor: float
    valor_pago: float | None
    juro: float
    empresa: Empresa
    observacao: str


def _parcelas(total: float, n: int) -> list[float]:
    base = round(total / n, 2)
    return [base] * (n - 1) + [round(total - base * (n - 1), 2)]


def gerar_receber(pedidos: list[Pedido], hoje: date) -> list[TituloReceber]:
    titulos: list[TituloReceber] = []
    for p in pedidos:
        c = p.cliente
        partes = {linha: v / p.valor for linha, v in p.por_linha.items()}
        for i, (prazo, valor) in enumerate(
            zip(c.prazos, _parcelas(p.valor, len(c.prazos)), strict=True)
        ):
            numdoc = f"{p.numero}-{i + 1}"
            vencimento = proximo_util(p.data + timedelta(days=prazo))
            r = rng("pagamento", numdoc)
            perda = False
            pagamento = vencimento
            if c.calote is not None and vencimento >= c.calote - timedelta(days=45):
                if c.recupera:
                    pagamento = vencimento + timedelta(days=r.randint(100, 160))
                else:
                    perda = True
            elif c.atraso_fixo is not None:
                pagamento = vencimento + timedelta(days=r.randint(*c.atraso_fixo))
            elif c.perfil == "pontual":
                pagamento = vencimento + timedelta(days=r.choice((-2, -1, 0, 0, 0, 0, 0, 1, 2)))
            elif c.perfil == "tardio":
                pagamento = vencimento + timedelta(days=r.randint(2, 9))
            elif r.random() < 0.04:
                perda = True
            else:
                pagamento = vencimento + timedelta(days=r.randint(6, 30))
            if perda:
                pagamento = vencimento + timedelta(days=365)
            pagamento = max(p.data, proximo_util(pagamento))
            aberto = pagamento >= hoje
            atraso = max(0, (pagamento - vencimento).days)
            juro = 0.0 if perda or aberto else round(valor * JUROS_DIA * atraso, 2)
            titulos.append(
                TituloReceber(
                    numdoc,
                    p.data,
                    vencimento,
                    None if aberto else pagamento,
                    valor,
                    None if aberto else (0.0 if perda else round(valor + juro, 2)),
                    juro,
                    c.codigo,
                    p.vendedor,
                    p.empresa,
                    "Baixa por perda" if perda and not aberto else "Venda a prazo",
                    partes,
                )
            )
    return titulos


class _Pagar:
    """Acumula os títulos a pagar emitidos antes de `hoje`, com o pagamento decidido."""

    def __init__(self, hoje: date) -> None:
        self.hoje = hoje
        self.titulos: list[TituloPagar] = []
        self._seq: dict[tuple[str, int, date], int] = defaultdict(int)

    def lancar(
        self,
        conta: str,
        fornecedor: str,
        empresa: Empresa,
        emissao: date,
        vencimento: date,
        valor: float,
        observacao: str,
        atrasa: bool = False,
    ) -> None:
        if emissao >= self.hoje or valor <= 0:
            return
        chave = (conta, empresa.numero, emissao)
        self._seq[chave] += 1
        seq = self._seq[chave]
        numdoc = f"{conta}{empresa.numero}{emissao:%y%m%d}{seq:02d}"
        r = rng("pagar", numdoc)
        pagamento = proximo_util(vencimento)
        if atrasa and r.random() < 0.04:
            pagamento = proximo_util(pagamento + timedelta(days=r.randint(1, 6)))
        aberto = pagamento >= self.hoje
        juro = 0.0
        if not aberto and pagamento > vencimento and atrasa:
            juro = round(valor * JUROS_DIA * (pagamento - vencimento).days, 2)
        self.titulos.append(
            TituloPagar(
                numdoc,
                f"L{numdoc}",
                emissao,
                vencimento,
                None if aberto else pagamento,
                conta,
                fornecedor,
                round(valor, 2),
                None if aberto else round(valor + juro, 2),
                juro,
                empresa,
                observacao,
            )
        )


# (conta, fornecedor, valor em Fortaleza, valor em Recife, dia da emissão, dia do
# vencimento, variação): valores do MES_BASE, corrigidos pela inflação
FIXAS = (
    ("501001", "S01", 52_000, 28_000, 1, 10, 0.0),
    ("501002", "S02", 24_000, 12_000, 5, 18, 0.12),
    ("501003", "S03", 3_200, 1_500, 5, 15, 0.10),
    ("501004", "S04", 4_800, 2_300, 1, 12, 0.02),
    ("501005", "S05", 7_500, 0, 1, 10, 0.0),
    ("501006", "S06", 9_000, 4_000, 1, 10, 0.0),
    ("501007", "S07", 6_500, 3_800, 1, 10, 0.0),
    ("501008", "S08", 2_500, 1_200, 12, 28, 0.30),
    ("501009", "S09", 5_800, 3_100, 1, 15, 0.0),
    ("501010", "S10", 9_000, 4_000, 8, 25, 0.40),
)


@dataclass(frozen=True)
class Emprestimo:
    data: date
    valor: float
    parcelas: int
    taxa: float

    def prestacoes(self) -> list[tuple[date, float, float]]:
        """(mês, juros, amortização) de cada parcela (tabela Price)."""
        pmt = self.valor * self.taxa / (1 - (1 + self.taxa) ** -self.parcelas)
        saldo = self.valor
        saida = []
        for j in range(1, self.parcelas + 1):
            juros = saldo * self.taxa
            amortizacao = pmt - juros
            saldo -= amortizacao
            saida.append((somar_meses(inicio_mes(self.data), j), juros, amortizacao))
        return saida


def emprestimos() -> list[Emprestimo]:
    """Um empréstimo a cada 26 meses, desde março de 2025."""
    saida = []
    k = 0
    while (m := somar_meses(date(2025, 3, 1), 26 * k)) < HORIZONTE:
        saida.append(Emprestimo(m.replace(day=10), 3_000_000 * 1.08**k, 36, 0.0135))
        k += 1
    return saida


@dataclass(frozen=True)
class Investimento:
    data: date
    conta: str
    fornecedor: str
    empresa: Empresa
    valor: float
    prazos: tuple[int, ...]
    descricao: str


def investimentos() -> list[Investimento]:
    """Um caminhão a cada 7 meses (desde junho de 2025) e equipamentos a cada 11."""
    saida = []
    k = 0
    while (m := somar_meses(date(2025, 6, 1), 7 * k)) < HORIZONTE:
        empresa = RECIFE if k % 3 == 2 else FORTALEZA
        saida.append(
            Investimento(
                m.replace(day=10),
                "701001",
                "S22",
                empresa,
                round(420_000 * inflacao(m), -3),
                (30, 60, 90),
                "Caminhão para entregas",
            )
        )
        k += 1
    k = 0
    while (m := somar_meses(date(2025, 2, 1), 11 * k)) < HORIZONTE:
        saida.append(
            Investimento(
                m.replace(day=14),
                "701002",
                "S23",
                FORTALEZA,
                round(95_000 * inflacao(m), -3),
                (30, 60),
                "Empilhadeira e porta-paletes",
            )
        )
        k += 1
    return saida


def gerar_pagar(
    cad: Cadastro,
    itens: list[ItemVenda],
    receber: list[TituloReceber],
    hoje: date,
) -> list[TituloPagar]:
    pagar = _Pagar(hoje)
    faturado: dict[tuple[str, date], float] = defaultdict(float)
    cmv: dict[tuple[str, str, date], float] = defaultdict(float)
    for it in itens:
        faturado[(it.empresa.id, inicio_mes(it.data))] += it.valor
        cmv[(it.empresa.id, it.linha, inicio_mes(it.data))] += it.custo
    boletos: dict[tuple[str, date], int] = defaultdict(int)
    for t in receber:
        boletos[(t.empresa.id, inicio_mes(t.emissao))] += 1
    mes_hoje = inicio_mes(hoje)
    ultimo = somar_meses(mes_hoje, 2)

    for m in meses(INICIO, ultimo):
        fechado = m < mes_hoje
        proximo = somar_meses(m, 1)
        fim = ultimo_dia(m)
        for e in EMPRESAS:
            _compras(cad, pagar, e, m, cmv)
            # despesas fixas e variáveis pela curva esperada
            for conta, forn, v_for, v_rec, dia_emi, dia_venc, var in FIXAS:
                valor = (v_for if e is FORTALEZA else v_rec) * inflacao(m)
                valor *= lognormal(rng("fixa", conta, e.id, m), var) if var else 1.0
                pagar.lancar(
                    conta,
                    forn,
                    e,
                    dia_do_mes(m, dia_emi),
                    dia_do_mes(m, dia_venc),
                    valor,
                    "Despesa do mês",
                )
            esperado = receita_esperada(m, e)
            segundas = [d for d in _dias(m) if d.weekday() == 0]
            for d in segundas:
                pagar.lancar(
                    "502002",
                    "S11",
                    e,
                    d,
                    d + timedelta(days=7),
                    esperado * COMBUSTIVEL / len(segundas) * lognormal(rng("diesel", e.id, d), 0.1),
                    "Abastecimento da frota",
                )
            frota = (18_000 if e is FORTALEZA else 9_000) * inflacao(m)
            for dia in (7, 21):
                d = dia_do_mes(m, dia)
                pagar.lancar(
                    "502005",
                    "S14",
                    e,
                    d,
                    d + timedelta(days=15),
                    frota / 2 * lognormal(rng("oficina", e.id, d), 0.25),
                    "Manutenção da frota",
                )
            for d in (dia_do_mes(m, 15), fim):
                pagar.lancar(
                    "502003",
                    "S12",
                    e,
                    d,
                    d + timedelta(days=20),
                    esperado * FRETE_TERCEIROS / 2 * lognormal(rng("frete", e.id, d), 0.15),
                    "Fretes de entrega",
                )
            if fechado:
                receita = faturado[(e.id, m)]
                for conta, forn, taxa, venc in (
                    ("502001", "S17", COMISSAO, dia_do_mes(proximo, 10)),
                    ("401001", "S15", ICMS, anterior_util(dia_do_mes(proximo, 20))),
                    ("401002", "S16", PIS, anterior_util(dia_do_mes(proximo, 25))),
                    ("401003", "S16", COFINS, anterior_util(dia_do_mes(proximo, 25))),
                    ("401004", "S16", IRPJ_CSLL, anterior_util(ultimo_dia(proximo))),
                ):
                    pagar.lancar(conta, forn, e, fim, venc, receita * taxa, "Apuração do mês")
                pagar.lancar(
                    "502004",
                    "S13",
                    e,
                    fim,
                    dia_do_mes(proximo, 5),
                    boletos[(e.id, m)] * TARIFA_BOLETO,
                    "Tarifas de boletos emitidos",
                )
            _folha(cad, pagar, e, m)
    for emp in emprestimos():
        for m, juros, amortizacao in emp.prestacoes():
            if m > ultimo:
                break
            obs = f"Parcela do empréstimo de {emp.data:%m/%Y}"
            pagar.lancar("601001", "S13", FORTALEZA, m, m.replace(day=15), juros, obs)
            pagar.lancar("601002", "S13", FORTALEZA, m, m.replace(day=15), amortizacao, obs)
    for inv in investimentos():
        if inv.data > ultimo:
            continue
        for prazo, valor in zip(inv.prazos, _parcelas(inv.valor, len(inv.prazos)), strict=True):
            pagar.lancar(
                inv.conta,
                inv.fornecedor,
                inv.empresa,
                inv.data,
                inv.data + timedelta(days=prazo),
                valor,
                inv.descricao,
            )
    return pagar.titulos


def _dias(m: date) -> list[date]:
    fim = ultimo_dia(m)
    return [m + timedelta(days=i) for i in range((fim - m).days + 1)]


def _compras(
    cad: Cadastro, pagar: _Pagar, e: Empresa, m: date, cmv: dict[tuple[str, str, date], float]
) -> None:
    """Compras do mês: o CMV realizado da linha no mês anterior, levado ao mês pela curva
    esperada (o mês anterior já fechou quando o mês começa: o valor não muda dia a dia), e
    dividido entre as marcas. No primeiro mês, a curva esperada."""
    anterior = somar_meses(m, -1)
    ajuste = receita_esperada(m, e) / receita_esperada(anterior, e)
    total = 0.0
    for linha in LINHAS:
        base = cmv.get((e.id, linha.nome, anterior))
        if base is None:
            base = receita_esperada(m, e) * linha.participacao * (1 - margem(linha, m))
        else:
            base *= ajuste
        valor_linha = base * lognormal(rng("compra", e.id, linha.nome, m), 0.06)
        total += valor_linha
        marcas = [f for f in cad.fornecedores if f.linha == linha.nome]
        pesos = [
            sum(p.popularidade for p in cad.produtos if p.fornecedor == f.codigo) for f in marcas
        ]
        for forn, peso in zip(marcas, pesos, strict=True):
            valor = valor_linha * peso / sum(pesos)
            r = rng("notas", forn.codigo, e.id, m)
            n = 2 if valor < 60_000 else 4
            dias = sorted(r.sample(range(1, 27), n))
            for dia in dias:
                emissao = proximo_util(dia_do_mes(m, dia))
                nota = valor / n * lognormal(r, 0.08)
                for prazo, parcela in zip(
                    forn.prazos, _parcelas(nota, len(forn.prazos)), strict=True
                ):
                    pagar.lancar(
                        "201001",
                        forn.codigo,
                        e,
                        emissao,
                        emissao + timedelta(days=prazo),
                        parcela,
                        f"Compra de mercadoria - {linha.nome}",
                        atrasa=True,
                    )
    for dia in (10, 25):
        d = dia_do_mes(m, dia)
        pagar.lancar(
            "201002",
            "S12",
            e,
            d,
            d + timedelta(days=15),
            total * FRETE_COMPRAS / 2,
            "Frete sobre compras",
        )


def _folha(cad: Cadastro, pagar: _Pagar, e: Empresa, m: date) -> None:
    """Títulos da folha de `m`: salários, encargos e benefícios (estes, do mês seguinte)."""
    soma: dict[str, float] = defaultdict(float)
    for linha in folha_do_mes(cad, m):
        if linha.empresa is e:
            soma[linha.lancamento] += linha.valor
    beneficios: float = 0.0
    for linha in folha_do_mes(cad, somar_meses(m, 1)):
        if linha.empresa is e and linha.lancamento in ("ALIMENTAÇÃO", "TRANSPORTE"):
            beneficios += linha.valor
    fim = ultimo_dia(m)
    proximo = somar_meses(m, 1)
    obs = f"Folha de {m:%m/%Y}"
    pagar.lancar("301001", "S17", e, fim, dia_util_do_mes(proximo, 5), soma["SALÁRIO"], obs)
    pagar.lancar("301008", "S17", e, fim, fim + timedelta(days=10), soma["RESCISÃO"], obs)
    pagar.lancar("301002", "S18", e, fim, anterior_util(dia_do_mes(proximo, 20)), soma["FGTS"], obs)
    pagar.lancar("301003", "S19", e, fim, anterior_util(dia_do_mes(proximo, 20)), soma["INSS"], obs)
    pagar.lancar(
        "301005", "S21", e, dia_do_mes(m, 25), dia_do_mes(proximo, 10), soma["PLANO DE SAÚDE"], obs
    )
    pagar.lancar(
        "301004",
        "S20",
        e,
        dia_do_mes(m, 20),
        dia_util_do_mes(proximo, 1),
        beneficios,
        f"Benefícios de {proximo:%m/%Y}",
    )
    pagar.lancar("301007", "S17", e, m, dia_do_mes(m, 5), soma["FÉRIAS"], obs)
    if m.month == 11:
        venc = anterior_util(dia_do_mes(m, 30))
        pagar.lancar("301006", "S17", e, m, venc, soma["13º SALÁRIO"], "13º salário, 1ª parcela")
    if m.month == 12:
        venc = anterior_util(dia_do_mes(m, 20))
        pagar.lancar("301006", "S17", e, m, venc, soma["13º SALÁRIO"], "13º salário, 2ª parcela")
