"""Carga de ponta a ponta contra o Supabase local (Postgres + Storage)."""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import SecretStr

from carga.config import Settings
from carga.main import ResultadoCarga, executar
from tests.conftest import (
    CAMINHO_A,
    CAMINHO_B,
    CLIENTE_A,
    CLIENTE_B,
    EMPRESA_A1,
    EMPRESA_A2,
    EMPRESA_B1,
    USUARIO_A,
    USUARIO_B,
    AdminConn,
    Bucket,
    Linha,
    gerar_parquet,
)

pytestmark = [pytest.mark.integracao, pytest.mark.usefixtures("mart_restaurado")]

D = date(2026, 10, 13)  # mais novo que o seed (12/10 para A, 11/10 para B)

LINHAS_A: list[Linha] = [
    ("ALFA IND", date(2026, 1, 5), 1, Decimal("100.10")),
    ("ALFA IND", date(2026, 1, 20), 1, Decimal("200.20")),
    ("ALFA IND", date(2026, 2, 3), 1, Decimal("50.00")),
    ("ALFA IND", date(2026, 2, 3), 3, Decimal("-20.005")),  # arredonda no fim, não por linha
    ("ALFA IND", date(2026, 2, 28), 3, Decimal("-0.005")),
    ("ALFA COM", date(2026, 1, 10), 2, Decimal("-10.50")),
]
ESPERADO_A = {
    (EMPRESA_A1, date(2026, 1, 1), 1): Decimal("300.30"),
    (EMPRESA_A1, date(2026, 2, 1), 1): Decimal("50.00"),
    (EMPRESA_A1, date(2026, 2, 1), 3): Decimal("-20.01"),
    (EMPRESA_A2, date(2026, 1, 1), 2): Decimal("-10.50"),
}

# Valor em float: 0.1 + 0.1 + 0.1 em float dá 0.30000000000000004; em numeric, 0.30.
LINHAS_B: list[Linha] = [
    ("BETA SERV", date(2026, 3, 1), 7, 0.1),
    ("BETA SERV", date(2026, 3, 15), 7, 0.1),
    ("BETA SERV", date(2026, 3, 31), 7, 0.1),
    ("BETA SERV", date(2026, 4, 2), 8, 1234567.891),
]
ESPERADO_B = {
    (EMPRESA_B1, date(2026, 3, 1), 7): Decimal("0.30"),
    (EMPRESA_B1, date(2026, 4, 1), 8): Decimal("1234567.89"),
}

Mart = dict[tuple[str, date, int], Decimal]


def mart(admin: AdminConn, cliente_id: str) -> Mart:
    linhas = admin.execute(
        """
        select l.empresa_id::text, l.mes, l.id_descritivo, l.valor
          from mart.lancamento l
          join cfg.empresa e on e.id = l.empresa_id
         where e.cliente_id = %s
        """,
        (cliente_id,),
    ).fetchall()
    return {(r[0], r[1], r[2]): r[3] for r in linhas}


def carga_fontes(admin: AdminConn, resultado: ResultadoCarga) -> dict[str, tuple[object, ...]]:
    """cliente_id → (status, data_snapshot, linhas_lidas, linhas_gravadas, mensagem)."""
    linhas = admin.execute(
        """
        select cliente_id::text, status, data_snapshot, linhas_lidas, linhas_gravadas, mensagem
          from mart.carga_fonte where carga_id = %s
        """,
        (resultado.carga_id,),
    ).fetchall()
    return {r[0]: tuple(r[1:]) for r in linhas}


def status_carga(admin: AdminConn, resultado: ResultadoCarga) -> str:
    linha = admin.execute(
        "select status from mart.carga where id = %s", (resultado.carga_id,)
    ).fetchone()
    assert linha is not None
    return str(linha[0])


def atualizado_em(admin: AdminConn, usuario_id: str, cliente_id: str) -> date | None:
    """A mesma consulta da API (FIN-01), como api_site e com o RLS do usuário."""
    with admin.transaction():
        admin.execute("set local role api_site")
        admin.execute("select set_config('app.usuario_id', %s, true)", (usuario_id,))
        linha = admin.execute(
            """
            select cf.data_snapshot
              from mart.carga_fonte cf
              join cfg.fonte_carga f on f.id = cf.fonte_id
             where cf.cliente_id = %s and cf.status = 'ok' and f.tipo = 'lancamento'
             order by cf.inicio desc, cf.id desc
             limit 1
            """,
            (cliente_id,),
        ).fetchone()
    return linha[0] if linha else None


def subir_os_dois(bucket: Bucket, data: date = D) -> None:
    bucket.subir(CAMINHO_A, gerar_parquet(LINHAS_A, data))
    bucket.subir(CAMINHO_B, gerar_parquet(LINHAS_B, data, valor_float=True))


# --- caminho feliz ------------------------------------------------------------------


def test_caminho_feliz(settings: Settings, bucket: Bucket, admin: AdminConn) -> None:
    subir_os_dois(bucket)

    resultado = executar(settings)

    assert resultado.status == "ok", resultado
    assert status_carga(admin, resultado) == "ok"
    assert mart(admin, CLIENTE_A) == ESPERADO_A
    assert mart(admin, CLIENTE_B) == ESPERADO_B
    assert carga_fontes(admin, resultado) == {
        CLIENTE_A: ("ok", D, len(LINHAS_A), len(ESPERADO_A), None),
        CLIENTE_B: ("ok", D, len(LINHAS_B), len(ESPERADO_B), None),
    }
    assert atualizado_em(admin, USUARIO_A, CLIENTE_A) == D


def test_idempotente(settings: Settings, bucket: Bucket, admin: AdminConn) -> None:
    subir_os_dois(bucket)

    primeira = executar(settings)
    depois_da_primeira = (mart(admin, CLIENTE_A), mart(admin, CLIENTE_B))
    segunda = executar(settings)

    assert primeira.status == segunda.status == "ok"
    assert (mart(admin, CLIENTE_A), mart(admin, CLIENTE_B)) == depois_da_primeira
    assert depois_da_primeira == (ESPERADO_A, ESPERADO_B)


# --- falhas de data: mart intacto ---------------------------------------------------


def test_exigir_snapshot_novo_com_mesma_data_falha(
    settings: Settings, bucket: Bucket, admin: AdminConn
) -> None:
    subir_os_dois(bucket)
    assert executar(settings).status == "ok"
    antes = (mart(admin, CLIENTE_A), mart(admin, CLIENTE_B))

    resultado = executar(settings.model_copy(update={"exigir_snapshot_novo": True}))

    assert resultado.status == "erro"
    assert status_carga(admin, resultado) == "erro"
    assert (mart(admin, CLIENTE_A), mart(admin, CLIENTE_B)) == antes
    for status, data, _lidas, gravadas, mensagem in carga_fontes(admin, resultado).values():
        assert (status, data, gravadas) == ("erro", D, None)
        assert "não foi renovado" in str(mensagem)


def test_exigir_snapshot_novo_com_data_nova_passa(
    settings: Settings, bucket: Bucket, admin: AdminConn
) -> None:
    subir_os_dois(bucket)  # D é mais novo que a última carga ok do seed
    resultado = executar(settings.model_copy(update={"exigir_snapshot_novo": True}))
    assert resultado.status == "ok"


def test_data_snapshot_misturado(settings: Settings, bucket: Bucket, admin: AdminConn) -> None:
    antes_a = mart(admin, CLIENTE_A)
    datas = [D] * (len(LINHAS_A) - 1) + [date(2026, 10, 12)]
    bucket.subir(CAMINHO_A, gerar_parquet(LINHAS_A, datas))
    bucket.subir(CAMINHO_B, gerar_parquet(LINHAS_B, D, valor_float=True))

    resultado = executar(settings)

    assert resultado.status == "erro"
    assert mart(admin, CLIENTE_A) == antes_a
    status, data, *_, mensagem = carga_fontes(admin, resultado)[CLIENTE_A]
    assert (status, data) == ("erro", None)
    assert "misturado" in str(mensagem)


def test_data_esperada_diferente(settings: Settings, bucket: Bucket, admin: AdminConn) -> None:
    antes = (mart(admin, CLIENTE_A), mart(admin, CLIENTE_B))
    subir_os_dois(bucket)

    resultado = executar(settings.model_copy(update={"data_esperada": date(2026, 10, 14)}))

    assert resultado.status == "erro"
    assert (mart(admin, CLIENTE_A), mart(admin, CLIENTE_B)) == antes
    for status, *_, mensagem in carga_fontes(admin, resultado).values():
        assert status == "erro"
        assert "DATA_ESPERADA é 2026-10-14" in str(mensagem)


def test_data_esperada_igual_passa(settings: Settings, bucket: Bucket) -> None:
    subir_os_dois(bucket)
    assert executar(settings.model_copy(update={"data_esperada": D})).status == "ok"


# --- falhas de conteúdo: mart intacto -----------------------------------------------


def test_coluna_faltando(settings: Settings, bucket: Bucket, admin: AdminConn) -> None:
    antes_a = mart(admin, CLIENTE_A)
    bucket.subir(CAMINHO_A, gerar_parquet(LINHAS_A, D, sem_colunas=["Valor", "Data"]))
    bucket.subir(CAMINHO_B, gerar_parquet(LINHAS_B, D, valor_float=True))

    resultado = executar(settings)

    assert mart(admin, CLIENTE_A) == antes_a
    status, *_, mensagem = carga_fontes(admin, resultado)[CLIENTE_A]
    assert status == "erro"
    assert mensagem == (
        "colunas faltando: Data, Valor. "
        "Colunas do arquivo: Empresa, Id_Descritivo, Conta, _data_snapshot"
    )


def test_empresa_sem_mapeamento(settings: Settings, bucket: Bucket, admin: AdminConn) -> None:
    antes_a = mart(admin, CLIENTE_A)
    linhas = [*LINHAS_A, ("ALFA NOVA", date(2026, 1, 1), 1, Decimal(1))]
    bucket.subir(CAMINHO_A, gerar_parquet(linhas, D))
    bucket.subir(CAMINHO_B, gerar_parquet(LINHAS_B, D, valor_float=True))

    resultado = executar(settings)

    assert mart(admin, CLIENTE_A) == antes_a
    status, *_, mensagem = carga_fontes(admin, resultado)[CLIENTE_A]
    assert status == "erro"
    assert "Empresa sem mapeamento" in str(mensagem)
    assert "'ALFA NOVA'" in str(mensagem)


def test_empresa_de_outro_cliente_no_arquivo_nao_tem_mapeamento(
    settings: Settings, bucket: Bucket, admin: AdminConn
) -> None:
    """O mapa é por cliente: o nome de uma empresa de B no arquivo de A não é aceito."""
    antes = (mart(admin, CLIENTE_A), mart(admin, CLIENTE_B))
    bucket.subir(
        CAMINHO_A, gerar_parquet([*LINHAS_A, ("BETA SERV", date(2026, 1, 1), 1, Decimal(1))], D)
    )
    bucket.subir(CAMINHO_B, gerar_parquet(LINHAS_B, date(2026, 10, 1), valor_float=True))

    resultado = executar(settings.model_copy(update={"data_esperada": D}))

    assert "'BETA SERV'" in str(carga_fontes(admin, resultado)[CLIENTE_A][-1])
    assert (mart(admin, CLIENTE_A), mart(admin, CLIENTE_B)) == antes


def test_empresa_que_sumiu_do_arquivo_sai_do_mart(
    settings: Settings, bucket: Bucket, admin: AdminConn
) -> None:
    assert any(chave[0] == EMPRESA_A2 for chave in mart(admin, CLIENTE_A))  # seed tem A2
    so_ind = [linha for linha in LINHAS_A if linha[0] == "ALFA IND"]
    bucket.subir(CAMINHO_A, gerar_parquet(so_ind, D))
    bucket.subir(CAMINHO_B, gerar_parquet(LINHAS_B, D, valor_float=True))

    resultado = executar(settings)

    assert resultado.status == "ok"
    assert mart(admin, CLIENTE_A) == {k: v for k, v in ESPERADO_A.items() if k[0] == EMPRESA_A1}


# --- várias fontes ------------------------------------------------------------------


def test_duas_fontes_uma_falha(settings: Settings, bucket: Bucket, admin: AdminConn) -> None:
    antes_b = mart(admin, CLIENTE_B)
    atualizado_b = atualizado_em(admin, USUARIO_B, CLIENTE_B)
    bucket.subir(CAMINHO_A, gerar_parquet(LINHAS_A, D))
    bucket.subir(CAMINHO_B, gerar_parquet(LINHAS_B, D, sem_colunas=["Empresa"], valor_float=True))

    resultado = executar(settings)

    assert resultado.status == "erro"
    assert status_carga(admin, resultado) == "erro"
    fontes = carga_fontes(admin, resultado)
    assert fontes[CLIENTE_A][0] == "ok"
    assert fontes[CLIENTE_B][0] == "erro"
    # A gravou; B ficou como estava
    assert mart(admin, CLIENTE_A) == ESPERADO_A
    assert mart(admin, CLIENTE_B) == antes_b
    # o "atualizado em" de A avança; o de B não muda
    assert atualizado_em(admin, USUARIO_A, CLIENTE_A) == D
    assert atualizado_em(admin, USUARIO_B, CLIENTE_B) == atualizado_b == date(2026, 10, 11)


def test_arquivo_inexistente(settings: Settings, bucket: Bucket, admin: AdminConn) -> None:
    antes_b = mart(admin, CLIENTE_B)
    bucket.subir(CAMINHO_A, gerar_parquet(LINHAS_A, D))  # B não sobe

    resultado = executar(settings)

    assert resultado.status == "erro"
    assert mart(admin, CLIENTE_B) == antes_b
    status, data, lidas, gravadas, mensagem = carga_fontes(admin, resultado)[CLIENTE_B]
    assert (status, data, lidas, gravadas) == ("erro", None, None, None)
    assert mensagem == (
        f"arquivo não existe no bucket: overnex-bucket/{CAMINHO_B} (o snapshot dessa tabela rodou?)"
    )
    assert mart(admin, CLIENTE_A) == ESPERADO_A


def test_chave_errada(settings: Settings, bucket: Bucket, admin: AdminConn) -> None:
    subir_os_dois(bucket)
    resultado = executar(
        settings.model_copy(update={"supabase_secret_key": SecretStr("chave-errada")})
    )
    assert resultado.status == "erro"
    for status, *_, mensagem in carga_fontes(admin, resultado).values():
        assert status == "erro"
        assert "acesso negado ao Storage" in str(mensagem)
