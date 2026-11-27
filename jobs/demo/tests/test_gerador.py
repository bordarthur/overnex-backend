"""O gerador: o passado não muda de um dia para o outro, as tabelas batem entre si e os
números ficam numa faixa plausível. Gera duas vezes (~10 s cada)."""

from collections import defaultdict
from datetime import date, timedelta
from functools import cache

import pyarrow as pa
import pytest

from demo.calendario import inicio_mes, util
from demo.gerar import Dados, gerar, gerar_dados
from demo.modelo import EMPRESAS, INICIO, SALDO_INICIAL
from demo.tabelas import ESQUEMAS, esquema

HOJE = date(2026, 11, 25)
DEPOIS = HOJE + timedelta(days=9)


@cache
def dados(hoje: date) -> Dados:
    return gerar_dados(hoje)


def test_vendas_em_dias_uteis_ate_a_vespera() -> None:
    d = dados(HOJE)
    assert d.vendas
    assert all(INICIO <= v.data < HOJE and util(v.data) for v in d.vendas)
    assert max(v.data for v in d.vendas) == HOJE - timedelta(days=1)


def test_estoque_de_hoje_tem_de_tudo() -> None:
    d = dados(HOJE)
    assert d.estoque == gerar_dados(HOJE).estoque  # o mesmo sorteio no mesmo dia
    assert {e.empresa for e in d.estoque} == set(EMPRESAS)
    assert all(e.saldo >= 0 for e in d.estoque)
    zerados = sum(1 for e in d.estoque if e.saldo == 0)
    # ruptura de ~5% dos produtos vendidos nos 90 dias
    assert 0 < zerados < 0.15 * len(d.estoque)
    # um produto por filial
    assert len({(e.empresa, e.produto) for e in d.estoque}) == len(d.estoque)


def test_passado_nao_muda_no_dia_seguinte() -> None:
    antes, depois = dados(HOJE), dados(DEPOIS)
    assert [v for v in depois.vendas if v.data < HOJE] == antes.vendas
    mes = inicio_mes(HOJE)
    assert [f for f in depois.folha if f.mes < mes] == antes.folha
    assert [x for x in depois.dre if x.mes < mes] == [x for x in antes.dre if x.mes < mes]
    titulos = {t.numdoc: t for t in depois.receber}
    for t in antes.receber:
        novo = titulos[t.numdoc]
        assert (novo.valor, novo.vencimento) == (t.valor, t.vencimento)
        if t.pagamento is not None:
            assert novo.pagamento == t.pagamento
    pagos_antes = {p.numdoc: p.pagamento for p in antes.pagar}
    for p in depois.pagar:
        if pagos_antes.get(p.numdoc) is not None:
            assert p.pagamento == pagos_antes[p.numdoc]


def test_receber_soma_o_pedido() -> None:
    d = dados(HOJE)
    vendido: dict[str, float] = defaultdict(float)
    for v in d.vendas:
        vendido[v.pedido] += v.valor
    titulos: dict[str, float] = defaultdict(float)
    for t in d.receber:
        titulos[t.numdoc.rsplit("-", 1)[0]] += t.valor
    assert titulos.keys() == vendido.keys()
    assert all(abs(titulos[p] - vendido[p]) < 0.01 for p in vendido)


def test_dre_bate_com_vendas_e_folha() -> None:
    d = dados(HOJE)
    receita: dict[tuple[date, str], float] = defaultdict(float)
    for v in d.vendas:
        receita[(inicio_mes(v.data), v.empresa.id)] += v.valor
    folha: dict[tuple[date, str], float] = defaultdict(float)
    for f in d.folha:
        folha[(f.mes, f.empresa.id)] += f.valor
    dre_receita: dict[tuple[date, str], float] = defaultdict(float)
    dre_folha: dict[tuple[date, str], float] = defaultdict(float)
    for x in d.dre:
        if x.conta.startswith("100.01"):
            dre_receita[(x.mes, x.empresa.id)] += x.valor
        if x.conta.startswith("140.01"):
            dre_folha[(x.mes, x.empresa.id)] -= x.valor
    assert all(abs(dre_receita[k] - v) < 1 for k, v in receita.items())
    assert all(abs(dre_folha[k] - v) < 1 for k, v in folha.items())


def test_numeros_plausiveis() -> None:
    d = dados(HOJE)
    grupos: dict[date, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for x in d.dre:
        grupos[x.mes][x.conta[:3]] += x.valor
    for m, g in grupos.items():
        if m >= inicio_mes(HOJE):
            continue
        receita_liquida = g["100"] + g["110"]
        ebitda = receita_liquida + g["120"] + g["130"] + g["140"]
        assert 7e6 < g["100"] < 14e6, m
        assert 0.02 < ebitda / receita_liquida < 0.10, m
    saldo = SALDO_INICIAL
    por_mes: dict[date, float] = defaultdict(float)
    for mov in d.fluxo.movimentos:
        por_mes[inicio_mes(mov.data)] += mov.valor
    for m in sorted(por_mes):
        saldo += por_mes[m]
        assert saldo > 0, f"caixa negativo no fim de {m:%m/%Y}"
    aberto = [t for t in d.receber if t.pagamento is None]
    vencido = sum(t.valor for t in aberto if t.vencimento + timedelta(days=4) < HOJE)
    assert 0.03 < vencido / sum(t.valor for t in aberto) < 0.15


@pytest.mark.parametrize("tabela", sorted(ESQUEMAS))
def test_arquivos(tabela: str) -> None:
    tabelas = _tabelas()
    t = tabelas[tabela]
    assert t.num_rows > 0
    assert t.schema.equals(esquema(tabela))
    assert set(t.column("_data_snapshot").to_pylist()) == {HOJE}
    origens = {e.origem for e in EMPRESAS}
    for coluna in ("EMPRESA", "Empresa"):
        if coluna in t.column_names and tabela not in ("d_bitem_pj", "d_fornecedor_pj"):
            assert set(t.column(coluna).to_pylist()) <= origens


@cache
def _tabelas() -> dict[str, pa.Table]:
    return dict(gerar(HOJE))


def test_data_fora_do_periodo() -> None:
    with pytest.raises(ValueError, match="fora do período"):
        gerar(INICIO)
