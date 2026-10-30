"""Valores e textos prontos dos painéis (o front só formata pela unidade)."""

from collections.abc import Sequence
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from app.telas.contrato import Status
from app.telas.moldura import MESES
from app.telas.visao_geral.contrato import Valor
from app.telas.visao_geral.texto import moeda, numero

SEMANA = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")
SEMANA_NOME = ("Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo")
CEM = Decimal(100)


def q(v: Decimal, casas: int = 2) -> Decimal:
    """Arredonda o que vai para gráfico e tabela (o JSON fica menor)."""
    return v.quantize(Decimal(1).scaleb(-casas), rounding=ROUND_HALF_UP)


def brl(v: Decimal, *, compacto: bool | None = None, sufixo: str | None = None) -> Valor:
    return Valor(valor=q(v), unidade="brl", compacto=compacto, sufixo=sufixo)


def pct(v: Decimal, casas: int = 1) -> Valor:
    return Valor(valor=q(v, casas + 1), unidade="pct", casas=casas)


def pp(v: Decimal, casas: int = 1) -> Valor:
    return Valor(valor=q(v, casas + 1), unidade="pp", casas=casas)


def qtd(v: Decimal, casas: int = 0, sufixo: str | None = None) -> Valor:
    return Valor(valor=q(v, casas + 1), unidade="qtd", casas=casas, sufixo=sufixo)


def dias(v: Decimal) -> Valor:
    return Valor(valor=q(v, 1), unidade="dias", casas=0)


def mais(v: Decimal) -> str:
    """Valor com sinal: +R$ 120 mil, -R$ 50 mil."""
    return ("-" if v < 0 else "+") + moeda(v)


def pct_texto(v: Decimal, casas: int = 1, sinal: bool = False) -> str:
    """12,3% (com `sinal`, +12,3%)."""
    return ("+" if sinal and v > 0 else "") + numero(v, casas) + "%"


def nome_mes(mes: date) -> str:
    """outubro"""
    return MESES[mes.month - 1].lower()


def mes_ano(mes: date) -> str:
    """outubro de 2025"""
    return f"{nome_mes(mes)} de {mes.year}"


def moeda_mil(v: Decimal) -> str:
    """Como `casasMil: 1` no front: R$ 54,7 mil; fora dos milhares, como `moeda`."""
    if Decimal(1000) <= abs(v) < Decimal(1_000_000):
        return f"R$ {numero(abs(v) / Decimal(1000), 1)} mil"
    return moeda(v)


def lista(nomes: Sequence[str]) -> str:
    """ "Ana", "Ana e Bruno", "Ana, Bruno e Carla"."""
    if len(nomes) <= 1:
        return "".join(nomes)
    return ", ".join(nomes[:-1]) + " e " + nomes[-1]


def contra_referencia(valor: Decimal, referencia: Decimal) -> Status:
    """Farol de um valor em que maior é melhor, contra a referência (meta ou média): 100%
    ou mais, saudável; de 90% a 100%, atenção; abaixo, crítico (limites do mockup)."""
    razao = valor / referencia * CEM
    if razao >= CEM:
        return "saudavel"
    return "atencao" if razao >= 90 else "critico"
