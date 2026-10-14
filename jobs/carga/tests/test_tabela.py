"""Carga do tipo 'tabela' de ponta a ponta (Postgres + Storage locais).

Usa mart.f_estoque_nilo_maia, que o seed preenche para os dois clientes, e as fontes
'tabela' do seed (inativas; cada teste ativa a que precisa).
"""

import io
from datetime import date
from decimal import Decimal
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from carga.config import Settings
from carga.main import ResultadoCarga, executar
from tests.conftest import (
    CLIENTE_A,
    CLIENTE_B,
    EMPRESA_A1,
    EMPRESA_A2,
    AdminConn,
    Bucket,
)

pytestmark = [pytest.mark.integracao, pytest.mark.usefixtures("mart_restaurado")]

D = date(2026, 10, 13)
CAMINHO_A = "dataflow/dbo_f_estoque_padrao_alfa/dbo_f_estoque_padrao_alfa.parquet"


def estoque(
    linhas: list[tuple[str, str, float]],
    *,
    extra: dict[str, list[Any]] | None = None,
    sem: tuple[str, ...] = (),
) -> bytes:
    """Parquet no formato do f_estoque (empresa, produto, saldo)."""
    n = len(linhas)
    colunas: dict[str, pa.Array[Any]] = {
        "en_filial": pa.array(["1"] * n),
        "en_codigoproduto": pa.array([p for _, p, _ in linhas]),
        "en_saldoestoque": pa.array([s for *_, s in linhas], pa.float64()),
        "EMPRESA": pa.array([e for e, _, _ in linhas]),
        "QTD_RESERVADA": pa.array([0.0] * n),
        "QTD_BLOQUEADA": pa.array([0.0] * n),
        "QTD_PENDENTE": pa.array([0.0] * n),
        "ESTOQUE_DISPONIVEL": pa.array([s for *_, s in linhas], pa.float64()),
        "CUSTOFIN": pa.array([0.1] * n, pa.float64()),
        "_data_snapshot": pa.array([D] * n, pa.date32()),
    }
    for nome, valores in (extra or {}).items():
        colunas[nome] = pa.array(valores)
    tabela = pa.table({k: v for k, v in colunas.items() if k not in sem})
    buffer = io.BytesIO()
    pq.write_table(tabela, buffer, compression="zstd")
    return buffer.getvalue()


def ativar_so_estoque_de_a(admin: AdminConn, **campos: Any) -> None:
    """Deixa ativa só a fonte 'tabela' do cliente A (com os ajustes pedidos)."""
    admin.execute("update cfg.fonte_carga set ativo = false")
    sets = ", ".join(f"{c} = %({c})s" for c in campos)
    admin.execute(
        f"update cfg.fonte_carga set ativo = true{', ' + sets if sets else ''} "  # noqa: S608
        "where cliente_id = %(cliente)s and tipo = 'tabela'",
        {"cliente": CLIENTE_A, **campos},
    )


def linhas(admin: AdminConn, cliente: str) -> list[tuple[Any, ...]]:
    return admin.execute(
        """
        select _linha, _empresa_id::text, "en_codigoproduto", "en_saldoestoque", "CUSTOFIN"
          from mart.f_estoque_nilo_maia
         where _cliente_id = %s
         order by _linha
        """,
        (cliente,),
    ).fetchall()


def fonte(admin: AdminConn, resultado: ResultadoCarga) -> tuple[Any, ...]:
    linha = admin.execute(
        """
        select status, data_snapshot, linhas_lidas, linhas_gravadas, mensagem
          from mart.carga_fonte where carga_id = %s
        """,
        (resultado.carga_id,),
    ).fetchone()
    assert linha is not None
    return tuple(linha)


def test_caminho_feliz(settings: Settings, bucket: Bucket, admin: AdminConn) -> None:
    ativar_so_estoque_de_a(admin)
    antes_b = linhas(admin, CLIENTE_B)
    bucket.subir(
        CAMINHO_A,
        estoque([("ALFA IND", "N1", 1.5), ("ALFA COM", "N2", 0.1), ("ALFA IND", "N3", 2.0)]),
    )

    resultado = executar(settings)

    assert resultado.status == "ok", resultado
    assert fonte(admin, resultado) == ("ok", D, 3, 3, None)
    # as linhas antigas de A (P001, P002…) saíram; entraram as do arquivo, na ordem
    assert linhas(admin, CLIENTE_A) == [
        (1, EMPRESA_A1, "N1", Decimal("1.5"), Decimal("0.1")),
        (2, EMPRESA_A2, "N2", Decimal("0.1"), Decimal("0.1")),
        (3, EMPRESA_A1, "N3", Decimal("2.0"), Decimal("0.1")),
    ]
    assert linhas(admin, CLIENTE_B) == antes_b


def test_idempotente(settings: Settings, bucket: Bucket, admin: AdminConn) -> None:
    ativar_so_estoque_de_a(admin)
    bucket.subir(CAMINHO_A, estoque([("ALFA IND", "N1", 1.0)]))
    executar(settings)
    primeira = linhas(admin, CLIENTE_A)
    assert executar(settings).status == "ok"
    assert linhas(admin, CLIENTE_A) == primeira


def test_coluna_nova_no_arquivo_e_ignorada_com_aviso(
    settings: Settings, bucket: Bucket, admin: AdminConn
) -> None:
    ativar_so_estoque_de_a(admin)
    bucket.subir(CAMINHO_A, estoque([("ALFA IND", "N1", 1.0)], extra={"COLUNA_NOVA": ["x"]}))

    resultado = executar(settings)

    status, *_, mensagem = fonte(admin, resultado)
    assert status == "ok"
    assert mensagem == (
        "colunas novas no arquivo, ainda sem coluna em mart.f_estoque_nilo_maia (ignoradas): "
        "COLUNA_NOVA"
    )
    assert len(linhas(admin, CLIENTE_A)) == 1


def test_coluna_faltando(settings: Settings, bucket: Bucket, admin: AdminConn) -> None:
    ativar_so_estoque_de_a(admin)
    antes = linhas(admin, CLIENTE_A)
    bucket.subir(CAMINHO_A, estoque([("ALFA IND", "N1", 1.0)], sem=("CUSTOFIN",)))

    resultado = executar(settings)

    status, *_, mensagem = fonte(admin, resultado)
    assert status == "erro"
    assert str(mensagem).startswith("colunas faltando: CUSTOFIN. Colunas do arquivo: en_filial")
    assert linhas(admin, CLIENTE_A) == antes


def test_empresa_sem_mapeamento(settings: Settings, bucket: Bucket, admin: AdminConn) -> None:
    ativar_so_estoque_de_a(admin)
    antes = linhas(admin, CLIENTE_A)
    bucket.subir(CAMINHO_A, estoque([("ALFA IND", "N1", 1.0), ("1 Nilo Maia", "N2", 1.0)]))

    resultado = executar(settings)

    status, *_, mensagem = fonte(admin, resultado)
    assert status == "erro"
    assert "EMPRESA sem mapeamento" in str(mensagem)
    assert "'1 Nilo Maia'" in str(mensagem)
    assert linhas(admin, CLIENTE_A) == antes


def test_sem_coluna_de_empresa_grava_para_o_cliente_inteiro(
    settings: Settings, bucket: Bucket, admin: AdminConn
) -> None:
    ativar_so_estoque_de_a(admin, coluna_empresa=None)
    bucket.subir(CAMINHO_A, estoque([("qualquer coisa", "N1", 1.0)]))

    resultado = executar(settings)

    assert resultado.status == "ok"
    assert [(r[0], r[1]) for r in linhas(admin, CLIENTE_A)] == [(1, None)]


def test_tabela_inexistente(settings: Settings, bucket: Bucket, admin: AdminConn) -> None:
    ativar_so_estoque_de_a(admin, tabela_destino="f_nao_existe")
    bucket.subir(CAMINHO_A, estoque([("ALFA IND", "N1", 1.0)]))

    resultado = executar(settings)

    status, *_, mensagem = fonte(admin, resultado)
    assert status == "erro"
    assert "a tabela mart.f_nao_existe não existe" in str(mensagem)


def test_tipo_incompativel(settings: Settings, bucket: Bucket, admin: AdminConn) -> None:
    ativar_so_estoque_de_a(admin)
    antes = linhas(admin, CLIENTE_A)
    # en_saldoestoque é numeric na tabela; texto não converte
    dados = estoque([("ALFA IND", "N1", 1.0)], sem=("en_saldoestoque",))
    tabela = pq.read_table(io.BytesIO(dados)).append_column("en_saldoestoque", pa.array(["muito"]))
    buffer = io.BytesIO()
    pq.write_table(tabela, buffer)
    bucket.subir(CAMINHO_A, buffer.getvalue())

    resultado = executar(settings)

    status, *_, mensagem = fonte(admin, resultado)
    assert status == "erro"
    assert "valor incompatível com o tipo de uma coluna de mart.f_estoque_nilo_maia" in str(
        mensagem
    )
    assert linhas(admin, CLIENTE_A) == antes
