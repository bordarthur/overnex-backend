"""Fluxo de caixa realizado (f_fluxo_pj) desde INICIO_FLUXO, com o saldo dia a dia.

Entra o que foi pago e recebido nos títulos (recebimento aberto pelas linhas do pedido; juros
à parte), os empréstimos tomados e três movimentos sem título que dependem do saldo:
- tarifas bancárias no 5º dia útil;
- rendimento da aplicação no último dia útil, sobre o saldo acima de R$ 500 mil;
- retirada dos sócios no dia 12 (ou no útil seguinte): 80% do que passa de SALDO_ALVO, em
  múltiplos de R$ 10 mil. É o que segura o caixa perto do alvo ao longo dos anos. A retirada
  vira também um título a pagar (já pago), como no ERP.

O saldo de cada dia depende só dos dias anteriores, então o passado não muda de uma carga
para a outra.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

from demo.calendario import dia_util_do_mes, dias_uteis, inicio_mes, proximo_util
from demo.modelo import (
    CONTA,
    FORTALEZA,
    INICIO_FLUXO,
    JUROS_CLIENTES,
    JUROS_MORA,
    LINHA_POR_NOME,
    RENDIMENTO,
    SALDO_INICIAL,
    Empresa,
    inflacao,
)
from demo.titulos import TituloPagar, TituloReceber, emprestimos

SALDO_ALVO = 2_300_000.0
BANCO = "Banco Mandacaru"


@dataclass(frozen=True)
class Movimento:
    data: date
    empresa: Empresa
    conta: str
    codigo: str
    descricao: str
    valor: float


@dataclass(frozen=True)
class Fluxo:
    movimentos: list[Movimento]
    retiradas: list[TituloPagar]


def gerar_fluxo(receber: list[TituloReceber], pagar: list[TituloPagar], hoje: date) -> Fluxo:
    por_dia: dict[date, list[Movimento]] = defaultdict(list)

    def no_periodo(d: date | None) -> bool:
        return d is not None and INICIO_FLUXO <= d < hoje

    for t in receber:
        if not no_periodo(t.pagamento) or not t.valor_pago or t.pagamento is None:
            continue
        for linha, parte in t.por_linha.items():
            por_dia[t.pagamento].append(
                Movimento(
                    t.pagamento,
                    t.empresa,
                    LINHA_POR_NOME[linha].conta_receita,
                    "101001",
                    "Recebimento de clientes",
                    t.valor * parte,
                )
            )
        if t.juro:
            por_dia[t.pagamento].append(
                Movimento(
                    t.pagamento, t.empresa, JUROS_CLIENTES, "101002", "Juros de clientes", t.juro
                )
            )
    for p in pagar:
        if not no_periodo(p.pagamento) or p.pagamento is None:
            continue
        conta = CONTA[p.conta]
        por_dia[p.pagamento].append(
            Movimento(p.pagamento, p.empresa, conta.id_descritivo, p.conta, conta.nome, -p.valor)
        )
        if p.juro:
            por_dia[p.pagamento].append(
                Movimento(p.pagamento, p.empresa, JUROS_MORA, p.conta, "Juros de mora", -p.juro)
            )
    for emp in emprestimos():
        d = proximo_util(emp.data)
        if no_periodo(d):
            por_dia[d].append(
                Movimento(
                    d,
                    FORTALEZA,
                    CONTA["602001"].id_descritivo,
                    "602001",
                    "Empréstimo bancário",
                    emp.valor,
                )
            )

    movimentos: list[Movimento] = []
    retiradas: list[TituloPagar] = []
    saldo = SALDO_INICIAL
    d = INICIO_FLUXO
    while d < hoje:
        m = inicio_mes(d)
        uteis = dias_uteis(m)
        do_dia = list(por_dia.get(d, []))
        if d == dia_util_do_mes(m, 5):
            valor = round(3_800 * inflacao(m), 2)
            do_dia.append(
                Movimento(d, FORTALEZA, "170.01.02", "601003", "Tarifas bancárias", -valor)
            )
        if d == proximo_util(m.replace(day=12)):
            retirada = (saldo - SALDO_ALVO) * 0.8 // 10_000 * 10_000
            if retirada > 0:
                numdoc = f"8010011{d:%y%m%d}01"
                retiradas.append(
                    TituloPagar(
                        numdoc,
                        f"L{numdoc}",
                        d,
                        d,
                        d,
                        "801001",
                        "S24",
                        retirada,
                        retirada,
                        0.0,
                        FORTALEZA,
                        "Distribuição aos sócios",
                    )
                )
                do_dia.append(
                    Movimento(d, FORTALEZA, "210.01.01", "801001", "Retirada de sócios", -retirada)
                )
        if d == uteis[-1]:
            rendimento = round(max(0.0, saldo - 500_000) * 0.0075, 2)
            do_dia.append(
                Movimento(d, FORTALEZA, RENDIMENTO, "101003", "Rendimento de aplicação", rendimento)
            )
        for mov in do_dia:
            saldo += mov.valor
        movimentos.extend(do_dia)
        d += timedelta(days=1)
    return Fluxo(movimentos, retiradas)


@dataclass(frozen=True)
class LinhaFluxo:
    """Uma linha de f_fluxo_pj: a soma do dia por filial, conta do plano e conta do ERP."""

    data: date
    empresa: Empresa
    conta: str
    codigo: str
    descricao: str
    valor: float


def agrupar(movimentos: list[Movimento]) -> list[LinhaFluxo]:
    soma: dict[tuple[date, str, str, str, str], float] = defaultdict(float)
    empresas: dict[str, Empresa] = {}
    for mov in movimentos:
        sinal = "E" if mov.valor >= 0 else "S"
        soma[(mov.data, mov.empresa.id, mov.conta, mov.codigo, mov.descricao + sinal)] += mov.valor
        empresas[mov.empresa.id] = mov.empresa
    return [
        LinhaFluxo(d, empresas[e], conta, codigo, descricao[:-1], round(valor, 2))
        for (d, e, conta, codigo, descricao), valor in sorted(soma.items())
        if round(valor, 2) != 0
    ]
