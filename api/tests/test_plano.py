"""Plano de contas padrão (cfg.plano_conta) e o filtro da DRE da Visão geral — sem banco."""

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from app.telas.plano import Plano, normalizar, plano_das_linhas
from app.telas.visao_geral.resultado import dre_por_mes, grupos_da_operacao

D = Decimal
SET = date(2026, 9, 1)


def _conta(
    codigo: str, nivel_2: str, fator: int, competencia: str | None, caixa: str | None = "FCO"
) -> dict[str, Any]:
    return {
        "id_descritivo": codigo,
        "descritivo": f"Conta {codigo}",
        "id_nivel_2": (normalizar(codigo) or "").rsplit(".", 1)[0],
        "nivel_2": nivel_2,
        "fator": fator,
        "caixa": caixa,
        "competencia": competencia,
    }


PLANO = plano_das_linhas(
    [
        _conta("110.01.02", "Descontos", -1, "CO"),
        _conta("110.03.08", "Impostos Retidos na Fonte", -1, "CO/CA"),
        _conta("110.03;14", "Impostos Sobre a Venda", 1, "CO/CA", None),
        _conta("110.03.01", "Impostos Sobre a Venda", -1, "CO/CA"),
        _conta("100.03.06", "Outras Receitas", 1, "CA"),
        _conta("140.01.01", "Despesas com Pessoal", -1, "CO/CA"),
        _conta("400.01.01", "Emprestimo Concedido", -1, "CA", "FCF"),
        _conta("900.01.01", "Transferencia entre contas", 1, None, None),
    ]
)


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("110.03;14", "110.03.14"),
        (" 140.01.01 ", "140.01.01"),
        ("1400101", "140.01.01"),
        ("100", "100"),
        ("", None),
        (None, None),
        ("sem número", None),
    ],
)
def test_normalizar(texto: str | None, esperado: str | None) -> None:
    assert normalizar(texto) == esperado


def test_competencia_decide_dre_e_caixa() -> None:
    # CO: só DRE; CA: só caixa; CO/CA: os dois; sem competência: nenhum
    assert PLANO.na_dre("110.01.02")
    assert not PLANO.no_caixa("110.01.02")
    assert not PLANO.na_dre("100.03.06")
    assert PLANO.no_caixa("100.03.06")
    assert PLANO.na_dre("140.01.01")
    assert PLANO.no_caixa("140.01.01")
    assert not PLANO.na_dre("400.01.01")
    assert PLANO.no_caixa("400.01.01")
    assert not PLANO.na_dre("900.01.01")
    assert not PLANO.no_caixa("900.01.01")
    # conta fora do plano não entra em nenhum
    assert not PLANO.na_dre("140.09.99")
    assert not PLANO.no_caixa("140.09.99")
    # sem plano cadastrado, toda conta entra
    assert Plano().na_dre("140.09.99")
    assert Plano().no_caixa("140.09.99")


def test_fator_e_nomes() -> None:
    assert PLANO.fator("110.03.14") == 1
    assert PLANO.fator("110.03.01") == -1
    assert PLANO.fator("140.09.99") is None
    assert PLANO.nome("140.01.01") == "Conta 140.01.01"
    # o nível 2 com nomes diferentes no plano: vale o mais comum
    assert PLANO.nome("110.03") == "Impostos Sobre a Venda"
    assert PLANO.nome("999.99") is None


def test_visao_geral_so_soma_o_que_o_plano_poe_na_dre() -> None:
    operacao = grupos_da_operacao(
        {SET: {"faturado": D(1000), "custo": D(600)}},
        {},
        [
            (SET, "140.01.01", D(100)),
            # só caixa e fora do plano: fora
            (SET, "100.03.06", D(-500)),
            (SET, "140.09.99", D(70)),
            # só competência: entra
            (SET, "110.01.02", D(30)),
        ],
        PLANO,
    )
    assert operacao.grupos[SET] == {"100": D(1000), "120": D(600), "140": D(100), "110": D(30)}
    assert operacao.folha[SET] == D(100)

    real = dre_por_mes({}, {}, operacao)
    assert real[SET]["receita_liquida"] == D(970)
    assert real[SET]["ebitda"] == D(270)

    # sem plano, a regra antiga: tudo dos grupos da DRE
    antigo = grupos_da_operacao({}, {}, [(SET, "100.03.06", D(-500))])
    assert antigo.grupos[SET] == {"100": D(500)}


def test_lancamentos_nao_pegam_a_tabela_de_agrupados(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.telas.visao_geral import resultado
    from app.telas.visao_geral.vendas import TabelaDoCliente

    def tabelas(conn: Any, cliente: Any, prefixo: str, padrao: Any) -> list[TabelaDoCliente]:
        nomes = ["f_lancamentos_pj_cortes", "f_lancamentos_agrupados_pj"]
        return [
            TabelaDoCliente(n, dict(padrao), frozenset(), frozenset(padrao.values()))
            for n in nomes
            if n.startswith(prefixo)
        ]

    monkeypatch.setattr(resultado, "tabelas_do_cliente", tabelas)
    ctx: Any = type("Ctx", (), {"cliente_id": None})()
    assert [t.tabela for t in resultado.tabelas_lancamentos(None, ctx)] == [  # type: ignore[arg-type]
        "f_lancamentos_pj_cortes"
    ]
    assert [t.tabela for t in resultado.tabelas_agrupados(None, ctx)] == [  # type: ignore[arg-type]
        "f_lancamentos_agrupados_pj"
    ]
