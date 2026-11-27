"""Estoque de hoje por produto e filial (f_estoque_pj): a quantidade, como no DW da PJ (o
custo sai do cadastro, en_custounitario).

O DW guarda só a posição do dia, então cada carga sorteia a de hoje a partir das vendas: a
média por dia dos últimos 90 dias (até a véspera) vezes uma cobertura em dias. Sorteio por
dia, filial e produto (sorteio.py), para o painel Estoque ter de tudo:
- a maioria com cobertura normal, de 4 a 14 dias (a demo tem 12 dias ideais, parâmetro
  estoque.dias_ideais no cadastro de produção);
- RUPTURA dos produtos sem estoque (vendem e não têm);
- EXCESSO com 25 a 60 dias;
- produto sem venda nos 90 dias e com venda no ano fica parado com estoque (metade deles).
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

from demo.modelo import EMPRESAS, Empresa
from demo.sorteio import rng
from demo.vendas import ItemVenda

DIAS_MEDIA = 90
RUPTURA = 0.05
EXCESSO = 0.12
COBERTURA_NORMAL = (4.0, 14.0)
COBERTURA_EXCESSO = (25.0, 60.0)
COBERTURA_PARADO = (20.0, 60.0)


@dataclass(frozen=True)
class LinhaEstoque:
    empresa: Empresa
    produto: str
    saldo: float


def gerar_estoque(vendas: list[ItemVenda], hoje: date) -> list[LinhaEstoque]:
    """Uma linha por filial e produto vendido nela em 12 meses."""
    inicio_media = hoje - timedelta(days=DIAS_MEDIA)
    inicio_ano = hoje - timedelta(days=365)
    recente: dict[tuple[str, str], float] = defaultdict(float)
    no_ano: dict[tuple[str, str], float] = defaultdict(float)
    for it in vendas:
        if inicio_ano <= it.data < hoje:
            chave = (it.empresa.id, it.produto)
            no_ano[chave] += it.quantidade
            if it.data >= inicio_media:
                recente[chave] += it.quantidade

    por_id = {e.id: e for e in EMPRESAS}
    saida: list[LinhaEstoque] = []
    for chave in sorted(no_ano):
        empresa_id, produto = chave
        r = rng("estoque", hoje.isoformat(), empresa_id, produto)
        sorteio = r.random()
        por_dia = recente[chave] / DIAS_MEDIA
        if por_dia <= 0:
            # parado: vendeu no ano, não nos 90 dias
            if sorteio < 0.5:
                continue
            saldo = no_ano[chave] / 365 * r.uniform(*COBERTURA_PARADO)
        elif sorteio < RUPTURA:
            saldo = 0.0
        elif sorteio < RUPTURA + EXCESSO:
            saldo = por_dia * r.uniform(*COBERTURA_EXCESSO)
        else:
            saldo = por_dia * r.uniform(*COBERTURA_NORMAL)
        saida.append(LinhaEstoque(por_id[empresa_id], produto, round(saldo, 1)))
    return saida
