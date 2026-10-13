"""Storage, tipos do arquivo, fontes e conexão — sem banco e sem Storage de verdade."""

import uuid
from datetime import date, datetime
from decimal import Decimal

import httpx
import pyarrow as pa
import pytest

from carga.config import Settings
from carga.db import conectar
from carga.erros import ErroCarga
from carga.fontes import Fonte, fontes_em_conflito
from carga.lancamento import abrir_parquet, ler_data_snapshot, preparar
from carga.storage import baixar
from tests.conftest import gerar_parquet

D = date(2026, 10, 13)


def _settings(**kwargs: object) -> Settings:
    base: dict[str, object] = {
        "database_url_carga": "postgresql://carga@127.0.0.1:5432/postgres",
        "supabase_url": "https://xyz.supabase.co/",
        "supabase_secret_key": "sb_secret_teste",
    }
    return Settings(**(base | kwargs))  # type: ignore[arg-type]


# --- config -----------------------------------------------------------------------


def test_config_padroes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_ESPERADA", "")  # input vazio do workflow = não definido
    s = _settings()
    assert s.supabase_bucket == "overnex-bucket"
    assert s.exigir_snapshot_novo is False
    assert s.data_esperada is None
    assert s.supabase_url == "https://xyz.supabase.co"


def test_config_le_ambiente(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXIGIR_SNAPSHOT_NOVO", "true")
    monkeypatch.setenv("DATA_ESPERADA", "2026-10-13")
    s = _settings()
    assert s.exigir_snapshot_novo is True
    assert s.data_esperada == D


def test_pooler_em_modo_transacao_recusado() -> None:
    s = _settings(
        database_url_carga="postgresql://carga.ref:x@aws-0.pooler.supabase.com:6543/postgres"
    )
    with pytest.raises(ErroCarga, match="modo sessão"):
        conectar(s)


# --- storage ----------------------------------------------------------------------


def _cliente(
    status: int, corpo: bytes = b"", vistos: list[httpx.Request] | None = None
) -> httpx.Client:
    def responder(request: httpx.Request) -> httpx.Response:
        if vistos is not None:
            vistos.append(request)
        return httpx.Response(status, content=corpo)

    return httpx.Client(transport=httpx.MockTransport(responder))


def test_storage_baixa_com_chave_nos_dois_headers() -> None:
    vistos: list[httpx.Request] = []
    dados = baixar(_settings(), "dataflow/t/t.parquet", _cliente(200, b"PAR1", vistos))
    assert dados == b"PAR1"
    req = vistos[0]
    assert (
        str(req.url)
        == "https://xyz.supabase.co/storage/v1/object/overnex-bucket/dataflow/t/t.parquet"
    )
    assert req.headers["apikey"] == "sb_secret_teste"
    assert req.headers["authorization"] == "Bearer sb_secret_teste"


@pytest.mark.parametrize(
    ("status", "corpo"),
    [
        (404, b""),
        (400, b'{"statusCode":"404","error":"not_found","message":"Object not found"}'),
    ],
)
def test_storage_404(status: int, corpo: bytes) -> None:
    with pytest.raises(
        ErroCarga, match=r"arquivo não existe no bucket: overnex-bucket/x/x\.parquet"
    ):
        baixar(_settings(), "x/x.parquet", _cliente(status, corpo))


@pytest.mark.parametrize(
    ("status", "corpo"),
    [(401, b""), (403, b""), (400, b'{"statusCode":"403","error":"Unauthorized"}')],
)
def test_storage_chave_errada(status: int, corpo: bytes) -> None:
    with pytest.raises(ErroCarga, match=r"acesso negado ao Storage.*SUPABASE_SECRET_KEY"):
        baixar(_settings(), "x/x.parquet", _cliente(status, corpo))


def test_storage_outro_erro() -> None:
    with pytest.raises(ErroCarga, match="HTTP 500"):
        baixar(_settings(), "x/x.parquet", _cliente(500, b"boom"))


# --- arquivo ----------------------------------------------------------------------

LINHAS = [("ALFA IND", date(2026, 1, 5), 1, Decimal("10.10"))]


def test_parquet_invalido() -> None:
    with pytest.raises(ErroCarga, match="Parquet válido"):
        abrir_parquet(b"isto nao e parquet")


def test_ler_data_snapshot() -> None:
    assert ler_data_snapshot(abrir_parquet(gerar_parquet(LINHAS, D))) == D


def test_sem_coluna_data_snapshot() -> None:
    arquivo = abrir_parquet(gerar_parquet(LINHAS, D, sem_colunas=["_data_snapshot"]))
    with pytest.raises(ErroCarga, match="não tem a coluna _data_snapshot"):
        ler_data_snapshot(arquivo)


def _tabela(**colunas: pa.Array) -> pa.Table:  # type: ignore[type-arg]
    base: dict[str, pa.Array] = {  # type: ignore[type-arg]
        "Empresa": pa.array(["A"]),
        "Data": pa.array([date(2026, 1, 5)], pa.date32()),
        "Id_Descritivo": pa.array([1], pa.int64()),
        "Valor": pa.array([Decimal("1.00")], pa.decimal128(18, 2)),
    }
    return pa.table(base | colunas)


def test_preparar_aceita_timestamp_e_converte_para_data() -> None:
    t = preparar(_tabela(Data=pa.array([datetime(2026, 1, 5, 13, 30)], pa.timestamp("us"))))
    assert t.column("Data").type == pa.date32()
    assert t.column("Data").to_pylist() == [date(2026, 1, 5)]


def test_preparar_aceita_id_decimal_inteiro_e_valor_float() -> None:
    preparar(
        _tabela(
            Id_Descritivo=pa.array([Decimal(7)], pa.decimal128(10, 0)),
            Valor=pa.array([0.1], pa.float64()),
        )
    )


@pytest.mark.parametrize(
    ("colunas", "mensagem"),
    [
        ({"Empresa": pa.array([1])}, "Empresa precisa ser texto"),
        ({"Data": pa.array(["2026-01-05"])}, "Data precisa ser data"),
        ({"Id_Descritivo": pa.array([1.5])}, "Id_Descritivo precisa ser inteiro"),
        ({"Valor": pa.array(["1,00"])}, "Valor precisa ser numérico"),
        ({"Valor": pa.array([float("nan")])}, "NaN ou infinito"),
        ({"Valor": pa.array([float("inf")])}, "NaN ou infinito"),
        ({"Empresa": pa.array([None], pa.string())}, r"nulos em: Empresa \(1\)"),
    ],
)
def test_preparar_recusa(colunas: dict[str, pa.Array], mensagem: str) -> None:  # type: ignore[type-arg]
    with pytest.raises(ErroCarga, match=mensagem):
        preparar(_tabela(**colunas))


# --- fontes -----------------------------------------------------------------------


def test_fontes_em_conflito() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    fontes = [
        Fonte(1, a, "lancamento", "x/1.parquet"),
        Fonte(2, a, "lancamento", "x/2.parquet"),
        Fonte(3, b, "lancamento", "x/3.parquet"),
    ]
    assert fontes_em_conflito(fontes) == {1, 2}
