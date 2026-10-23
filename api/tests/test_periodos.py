"""Filtro "Período" da Visão geral: meses com dados, mês escolhido e data da posição — sem
banco."""

from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.farol.parametro import PADROES
from app.telas.moldura import MESES_NA_LISTA, MesComDados, meses_disponiveis
from app.telas.visao_geral.router import Recorte, mes_escolhido, recorte
from app.telas.visao_geral.vendas import MesDeVendas, kpis_vendas, mes_do_resumo

D = Decimal
SNAPSHOT = date(2026, 10, 21)


def test_meses_do_mais_recente_para_o_mais_antigo() -> None:
    # agosto repetido; novembro depois do snapshot fica de fora; outubro entra pelo snapshot
    meses = [date(2026, 8, 1), date(2026, 9, 1), date(2026, 8, 1), date(2026, 11, 1)]

    assert meses_disponiveis(meses, SNAPSHOT) == [
        MesComDados(mes=date(2026, 10, 1), dados_ate=SNAPSHOT),
        MesComDados(mes=date(2026, 9, 1), dados_ate=date(2026, 9, 30)),
        MesComDados(mes=date(2026, 8, 1), dados_ate=date(2026, 8, 31)),
    ]


def test_no_maximo_24_meses_e_sem_snapshot() -> None:
    # jan/2024 a jun/2026
    meses = [date(2024 + i // 12, i % 12 + 1, 1) for i in range(30)]

    lista = meses_disponiveis(meses, None)

    assert len(lista) == MESES_NA_LISTA
    assert lista[0] == MesComDados(mes=date(2026, 6, 1), dados_ate=date(2026, 6, 30))
    assert lista[-1].mes == date(2024, 7, 1)
    assert meses_disponiveis([], None) == []


def test_recorte_sem_mes_ou_no_mes_do_snapshot() -> None:
    padrao = recorte(None, SNAPSHOT)
    assert padrao == Recorte(mes=None, posicao=SNAPSHOT, fechado=False)
    # Resultado: o último mês fechado antes do snapshot
    assert padrao.ultimo_fechado == date(2026, 9, 1)

    aberto = recorte(date(2026, 10, 1), SNAPSHOT)
    assert aberto == Recorte(mes=date(2026, 10, 1), posicao=SNAPSHOT, fechado=False)
    assert aberto.ultimo_fechado == date(2026, 9, 1)


def test_recorte_de_mes_fechado() -> None:
    r = recorte(date(2026, 8, 1), SNAPSHOT)

    # posição dos títulos no último dia do mês; o Resultado é do próprio mês
    assert r == Recorte(mes=date(2026, 8, 1), posicao=date(2026, 8, 31), fechado=True)
    assert r.ultimo_fechado == date(2026, 8, 1)
    # virada de ano
    assert recorte(date(2025, 12, 1), SNAPSHOT).posicao == date(2025, 12, 31)


def test_mes_escolhido_so_entre_os_disponiveis() -> None:
    disponiveis = meses_disponiveis([date(2026, 9, 1)], SNAPSHOT)

    assert mes_escolhido("2026-09", disponiveis) == date(2026, 9, 1)
    with pytest.raises(HTTPException) as erro:
        mes_escolhido("2025-01", disponiveis)
    assert erro.value.status_code == 422
    assert erro.value.detail == "Mês sem dados."


def test_mes_fora_da_janela_sai_do_resumo() -> None:
    julho = date(2026, 7, 1)
    mensal = {julho: {"faturado": D(500), "custo": D(400), "pedidos": D(5), "clientes": D(3)}}

    assert mes_do_resumo(julho, mensal) == MesDeVendas(
        mes=julho,
        ultimo_dia=date(2026, 7, 31),
        faturado=D(500),
        custo=D(400),
        clientes=3,
        clientes_produtos=0,
        pedidos=5,
    )
    assert mes_do_resumo(date(2026, 6, 1), mensal) is None


def test_mes_fechado_sem_projecao_nem_a_realizar() -> None:
    kpis = kpis_vendas(
        date(2026, 8, 1), date(2026, 8, 31), D(999), D(1000), None, PADROES["vendas_meta"]
    )

    assert [k.rotulo for k in kpis] == ["Meta de agosto", "Faturado em agosto"]
    assert kpis[1].contexto == "vendas até 31/08"
