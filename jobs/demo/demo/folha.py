"""Folha de pagamento (f_folha_pj_cortes): uma linha por pessoa, mês e lançamento.

Quem está em cada posto em cada mês vem do cadastro (sorteado até HORIZONTE), então a folha
de qualquer mês, até de um mês futuro, é sempre a mesma: os títulos a pagar dos benefícios
do mês seguinte saem dela também.
"""

from dataclasses import dataclass
from datetime import date

from demo.cadastros import Cadastro, Ocupante, Posto
from demo.calendario import meses_entre, somar_meses
from demo.modelo import DISSIDIO, HORIZONTE, MES_BASE, Empresa

# lançamento → (Id_Descritivo, descritivo no plano, conta do ERP do título a pagar)
LANCAMENTOS = {
    "SALÁRIO": ("140.01.01", "Salários", "301001"),
    "13º SALÁRIO": ("140.01.02", "13º salários", "301006"),
    "FÉRIAS": ("140.01.03", "Férias", "301007"),
    "INSS": ("140.01.04", "INSS", "301003"),
    "FGTS": ("140.01.05", "FGTS", "301002"),
    "ALIMENTAÇÃO": ("140.01.07", "Alimentação", "301004"),
    "TRANSPORTE": ("140.01.08", "Transporte", "301004"),
    "PLANO DE SAÚDE": ("140.01.16", "Plano de saúde", "301005"),
    "RESCISÃO": ("140.01.11", "Indenização e rescisão", "301008"),
}
ALIMENTACAO = 600.0
TRANSPORTE = 230.0
PLANO_DE_SAUDE = 420.0
FGTS = 0.08
INSS = 0.278


@dataclass(frozen=True)
class LinhaFolha:
    mes: date
    pessoa: Ocupante
    posto: Posto
    lancamento: str
    valor: float

    @property
    def empresa(self) -> Empresa:
        return self.posto.empresa

    @property
    def conta(self) -> str:
        return LANCAMENTOS[self.lancamento][0]

    @property
    def conta_erp(self) -> str:
        return LANCAMENTOS[self.lancamento][2]


def _dissidios(m: date) -> int:
    """Quantos marços desde o MES_BASE até `m` (negativo antes)."""

    def ate(x: date) -> int:
        return x.year - (1 if x.month < 3 else 0)

    return ate(m) - ate(MES_BASE)


def salario(posto: Posto, pessoa: Ocupante, m: date) -> float:
    base = posto.cargo.salario * pessoa.salario_fator * (1 + DISSIDIO) ** _dissidios(m)
    return float(round(base, -1))


def folha_do_mes(cad: Cadastro, m: date) -> list[LinhaFolha]:
    reajuste = (1 + DISSIDIO) ** _dissidios(m)
    linhas: list[LinhaFolha] = []
    for posto in cad.postos:
        pessoa = posto.ocupante(m)
        if pessoa is None:
            continue
        s = salario(posto, pessoa, m)
        valores = {
            "SALÁRIO": s,
            "ALIMENTAÇÃO": ALIMENTACAO * reajuste,
            "FGTS": s * FGTS,
            "INSS": s * INSS,
        }
        if pessoa.transporte:
            valores["TRANSPORTE"] = TRANSPORTE * reajuste
        if posto.cargo.salario >= 3500:
            valores["PLANO DE SAÚDE"] = PLANO_DE_SAUDE * reajuste
        if m.month in (11, 12):
            valores["13º SALÁRIO"] = s / 2
        casa = meses_entre(pessoa.admissao, m)
        if casa > 0 and casa % 12 == 0:
            valores["FÉRIAS"] = s / 3
        if somar_meses(m, 1) == pessoa.saida and pessoa.saida < HORIZONTE:
            valores["RESCISÃO"] = s * (0.6 + 0.15 * casa / 12)
        linhas.extend(
            LinhaFolha(m, pessoa, posto, lanc, round(v, 2)) for lanc, v in valores.items()
        )
    return linhas
