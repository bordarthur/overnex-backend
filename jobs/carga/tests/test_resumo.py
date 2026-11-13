"""Resumo mensal (carga/resumo.py) — sem banco."""

from datetime import date
from decimal import Decimal

import pyarrow as pa
import pytest

from carga.erros import ErroCarga
from carga.resumo import SEM_DIMENSAO, Modelo, Total, calcular, modelo_da_tabela


def _modelo(tabela: str, colunas_modelo: dict[str, str] | None = None) -> Modelo:
    modelo = modelo_da_tabela(tabela, colunas_modelo)
    assert modelo is not None
    return modelo


VENDAS = _modelo("f_vendas_nilo_maia")

# colunas da PJ Cortes (outro DW): mesmos papéis, outros nomes
COLUNAS_PJ = {
    "data": "en_dataemissao",
    "valor": "en_valortotal",
    "custo": "en_custototal",
    "pedido": "en_numpedido",
    "cliente": "en_codigocliente",
    "produto": "en_codigoproduto",
    "abertura": "it_grupo",
}


def _vendas(**extra: list[object]) -> pa.Table:
    colunas: dict[str, list[object]] = {
        "en_datemicao": [
            "28/08/2026 00:00:00",
            "01/09/2026 00:00:00",
            "01/09/2026 00:00:00",
            "15/09/2026 10:30:00",
            None,
        ],
        "en_VALTOT": [999.0, 600.1, 400.2, 1000.0, 5000.0],
        "en_numdoc": ["P0", "P1", "P1", "P2", "P3"],
        "en_codcli": ["C9", "C1", "C1", "C2", "C3"],
        "en_codigo": ["X", "X", "Y", "X", "X"],
        "en_precus": [800.0, 500.0, 300.0, 700.0, 4000.0],
        "en_ramo": ["MERCEARIA", "MERCEARIA", "CONSTRUTORA", None, "MERCEARIA"],
    }
    return pa.table(colunas | extra)


def test_modelo_pelo_prefixo() -> None:
    assert modelo_da_tabela("f_vendas_pj") == VENDAS
    assert modelo_da_tabela("f_estoque_nilo_maia") is None


def test_colunas_do_modelo_trocam_so_as_informadas() -> None:
    modelo = _modelo("f_vendas_pj", {"valor": "en_valortotal", "abertura": "it_grupo"})
    assert modelo.coluna_data == "en_datemicao"
    assert "en_valortotal" in modelo.colunas
    assert "en_VALTOT" not in modelo.colunas
    assert {m.por for m in modelo.medidas} == {None, "it_grupo"}


def test_papel_desconhecido_falha() -> None:
    with pytest.raises(ErroCarga, match="papéis desconhecidos"):
        modelo_da_tabela("f_vendas_pj", {"vlaor": "en_valortotal"})


def test_mesmos_totais_com_os_nomes_da_pj() -> None:
    nomes = {
        padrao: COLUNAS_PJ[papel]
        for papel, padrao in [
            ("data", "en_datemicao"),
            ("valor", "en_VALTOT"),
            ("custo", "en_precus"),
            ("pedido", "en_numdoc"),
            ("cliente", "en_codcli"),
            ("produto", "en_codigo"),
            ("abertura", "en_ramo"),
        ]
    }
    dados = _vendas()
    pj = dados.rename_columns([nomes[c] for c in dados.column_names])

    assert calcular(pj, _modelo("f_vendas_pj", COLUNAS_PJ), None) == calcular(dados, VENDAS, None)


def test_totais_por_mes_sem_empresa() -> None:
    totais = [t for t in calcular(_vendas(), VENDAS, None) if not t.dimensao]

    agosto, setembro = date(2026, 8, 1), date(2026, 9, 1)
    assert totais == [
        Total(None, "clientes", agosto, Decimal("1.00")),
        Total(None, "clientes_produtos", agosto, Decimal("1.00")),
        Total(None, "custo", agosto, Decimal("800.00")),
        Total(None, "faturado", agosto, Decimal("999.00")),
        Total(None, "pedidos", agosto, Decimal("1.00")),
        # linha sem data fica de fora; float somado sem erro binário
        Total(None, "clientes", setembro, Decimal("2.00")),
        # C1 comprou X e Y; C2, X
        Total(None, "clientes_produtos", setembro, Decimal("3.00")),
        Total(None, "custo", setembro, Decimal("1500.00")),
        Total(None, "faturado", setembro, Decimal("2000.30")),
        Total(None, "pedidos", setembro, Decimal("2.00")),
    ]


def test_totais_por_ramo() -> None:
    abertos = [t for t in calcular(_vendas(), VENDAS, None) if t.dimensao]
    setembro = date(2026, 9, 1)

    custo = {(t.mes, t.dimensao): t.valor for t in abertos if t.medida == "custo_ramo"}
    assert custo == {
        (date(2026, 8, 1), "MERCEARIA"): Decimal("800.00"),
        (setembro, "MERCEARIA"): Decimal("500.00"),
        (setembro, "CONSTRUTORA"): Decimal("300.00"),
        # ramo vazio não se mistura com o total do mês (dimensão "")
        (setembro, SEM_DIMENSAO): Decimal("700.00"),
    }
    faturado = {(t.mes, t.dimensao): t.valor for t in abertos if t.medida == "faturado_ramo"}
    assert faturado[(setembro, "MERCEARIA")] == Decimal("600.10")


def test_totais_por_empresa() -> None:
    dados = _vendas(EMPRESA=["A", "A", "B", "B", "B"])
    totais = calcular(dados, VENDAS, "EMPRESA")

    faturado = {
        (t.empresa, t.mes): t.valor for t in totais if t.medida == "faturado" and not t.dimensao
    }
    assert faturado == {
        ("A", date(2026, 8, 1)): Decimal("999.00"),
        ("A", date(2026, 9, 1)): Decimal("600.10"),
        ("B", date(2026, 9, 1)): Decimal("1400.20"),
    }


def test_papeis_opcionais_aceitos_e_faturado_por_vendedor() -> None:
    modelo = _modelo("f_vendas_pj", {"vendedor": "en_codven", "linha": "en_ramo"})
    assert "faturado_vendedor" in {m.nome for m in modelo.medidas}
    # sem o papel, a medida não existe
    assert "faturado_vendedor" not in {m.nome for m in VENDAS.medidas}

    dados = _vendas(en_codven=["V1", "V1", "V2", "V2", "V1"])
    por_vendedor = {
        (t.mes, t.dimensao): t.valor
        for t in calcular(dados, modelo, None)
        if t.medida == "faturado_vendedor"
    }
    assert por_vendedor == {
        (date(2026, 8, 1), "V1"): Decimal("999.00"),
        (date(2026, 9, 1), "V1"): Decimal("600.10"),
        (date(2026, 9, 1), "V2"): Decimal("1400.20"),
    }


def test_data_em_formato_desconhecido_falha() -> None:
    dados = _vendas().set_column(
        0, "en_datemicao", pa.array(["set/2026", "x", "y", "z", None], pa.string())
    )
    with pytest.raises(ErroCarga, match="formato não reconhecido"):
        calcular(dados, VENDAS, None)
