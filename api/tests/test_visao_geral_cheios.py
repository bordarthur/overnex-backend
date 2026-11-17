"""Valores cheios na Visão geral: KPIs e números grandes em reais sem abreviar."""

from decimal import Decimal

from app.telas.visao_geral.cheios import cheio, secoes_cheias
from app.telas.visao_geral.contrato import (
    CartaoInadimplencia,
    CartaoPagarLancado,
    CartaoProximosDias,
    Secao,
    Valor,
)
from app.telas.visao_geral.router import secoes_demo


def _secao(secoes: list[Secao], id_: str) -> Secao:
    return next(s for s in secoes if s.id == id_)


def test_cheio_so_muda_reais() -> None:
    reais = Valor(valor=Decimal(6_900_000), unidade="brl", casas_mil=1)
    assert cheio(reais) == Valor(valor=Decimal(6_900_000), unidade="brl", compacto=False)
    pct = Valor(valor=Decimal("94.8"), unidade="pct", casas=1)
    assert cheio(pct) is pct
    ja_cheio = Valor(valor=Decimal(2995), unidade="brl", compacto=False)
    assert cheio(ja_cheio) is ja_cheio


def test_kpis_e_velocimetros_em_reais_saem_cheios() -> None:
    antes = secoes_demo()
    depois = secoes_cheias(antes)

    for s in depois:
        for k in s.kpis:
            assert k.valor.compacto is (False if k.valor.unidade == "brl" else None), k.rotulo
        for i in s.indicadores:
            for p in (i.periodos.mes, i.periodos.ano):
                if p.valor.unidade == "brl":
                    assert p.valor.compacto is False, i.nome
    # o número e o resto da seção não mudam
    assert [k.valor.valor for s in depois for k in s.kpis] == [
        k.valor.valor for s in antes for k in s.kpis
    ]
    assert [s.resumo for s in depois] == [s.resumo for s in antes]
    # as seções de origem ficam como estavam (o demo fica em cache)
    assert _secao(antes, "vendas").kpis[0].valor.compacto is None


def test_cartoes_do_financeiro() -> None:
    antes = _secao(secoes_demo(), "financeiro")
    depois = _secao(secoes_cheias(secoes_demo()), "financeiro")
    por_tipo = {c.tipo: c for c in depois.cartoes}

    inad = por_tipo["inadimplencia"]
    assert isinstance(inad, CartaoInadimplencia)
    assert all(i.valor.compacto is False for i in inad.carteira)

    proximos = por_tipo["proximos-dias"]
    assert isinstance(proximos, CartaoProximosDias)
    assert proximos.saldo is not None
    assert proximos.saldo.compacto is False
    for j in proximos.janelas:
        assert all(f.valor.compacto is False for f in j.fim.values())
        # o que vai para as barras continua abreviado
        assert j.receber.compacto is None
        assert j.pagar.compacto is None

    # cartão sem número grande em reais: igual
    lancado = por_tipo["pagar-lancado"]
    assert isinstance(lancado, CartaoPagarLancado)
    assert lancado == next(c for c in antes.cartoes if c.tipo == "pagar-lancado")
