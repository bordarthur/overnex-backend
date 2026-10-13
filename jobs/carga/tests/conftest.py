"""Configuração dos testes.

Os testes `integracao` rodam contra o Supabase local (`supabase start` + `supabase db reset`):
Postgres em 127.0.0.1:54322 (role carga) e Storage em 127.0.0.1:54321, bucket overnex-bucket.
Os Parquet são gerados aqui com pyarrow; nenhum arquivo real de cliente entra no repositório.

A chave secret local vem de SUPABASE_SECRET_KEY ou, se não definida, de `supabase status -o env`.
"""

import io
import os
import shutil
import subprocess
from collections.abc import Iterator, Sequence
from datetime import date
from decimal import Decimal
from typing import Any

import httpx
import psycopg
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from psycopg.rows import TupleRow
from pydantic import SecretStr

from carga.config import Settings

DATABASE_URL_CARGA = os.environ.get(
    "DATABASE_URL_CARGA", "postgresql://carga:carga_local@127.0.0.1:54322/postgres"
)
DATABASE_URL_ADMIN = os.environ.get(
    "DATABASE_URL_ADMIN", "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
)
SUPABASE_URL = os.environ.get("SUPABASE_URL", "http://127.0.0.1:54321")
BUCKET = os.environ.get("SUPABASE_BUCKET", "overnex-bucket")

# IDs e caminhos do supabase/seed.sql
CLIENTE_A = "c1000000-0000-4000-8000-00000000000a"
CLIENTE_B = "c1000000-0000-4000-8000-00000000000b"
EMPRESA_A1 = "e1000000-0000-4000-8000-0000000000a1"
EMPRESA_A2 = "e1000000-0000-4000-8000-0000000000a2"
EMPRESA_B1 = "e1000000-0000-4000-8000-0000000000b1"
USUARIO_A = "d1000000-0000-4000-8000-00000000000a"
USUARIO_B = "d1000000-0000-4000-8000-00000000000b"
_TABELA_A = "dbo_f_lancamentos_agrupados_padrao_alfa"
_TABELA_B = "dbo_f_lancamentos_agrupados_padrao_beta"
CAMINHO_A = f"dataflow/{_TABELA_A}/{_TABELA_A}.parquet"
CAMINHO_B = f"dataflow/{_TABELA_B}/{_TABELA_B}.parquet"

Linha = tuple[str, date, int, Decimal | float]
AdminConn = psycopg.Connection[TupleRow]


def gerar_parquet(
    linhas: Sequence[Linha],
    data_snapshot: date | Sequence[date],
    *,
    sem_colunas: Sequence[str] = (),
    valor_float: bool = False,
) -> bytes:
    """Parquet no formato do snapshot (zstd + _data_snapshot)."""
    datas = (
        [data_snapshot] * len(linhas) if isinstance(data_snapshot, date) else list(data_snapshot)
    )
    colunas: dict[str, pa.Array[Any]] = {
        "Empresa": pa.array([e for e, *_ in linhas], pa.string()),
        "Data": pa.array([d for _, d, *_ in linhas], pa.date32()),
        "Id_Descritivo": pa.array([i for _, _, i, _ in linhas], pa.int64()),
        "Valor": pa.array(
            [float(v) if valor_float else v for *_, v in linhas],
            pa.float64() if valor_float else pa.decimal128(18, 4),
        ),
        "Conta": pa.array(["3.1.01"] * len(linhas), pa.string()),  # coluna extra, ignorada
        "_data_snapshot": pa.array(datas, pa.date32()),
    }
    tabela = pa.table({k: v for k, v in colunas.items() if k not in sem_colunas})
    buffer = io.BytesIO()
    pq.write_table(tabela, buffer, compression="zstd")
    return buffer.getvalue()


def _chave_local() -> str:
    chave = os.environ.get("SUPABASE_SECRET_KEY")
    if chave:
        return chave
    cli = shutil.which("supabase")
    if cli:
        saida = subprocess.run(
            [cli, "status", "-o", "env"], capture_output=True, text=True, check=False
        ).stdout
        valores = dict(linha.split("=", 1) for linha in saida.splitlines() if "=" in linha)
        for nome in ("SECRET_KEY", "SERVICE_ROLE_KEY"):
            if valores.get(nome):
                return valores[nome].strip().strip('"')
    pytest.fail("Defina SUPABASE_SECRET_KEY com a chave secret local (veja `supabase status`).")


@pytest.fixture(scope="session")
def chave() -> str:
    return _chave_local()


@pytest.fixture
def settings(chave: str) -> Settings:
    return Settings(
        database_url_carga=SecretStr(DATABASE_URL_CARGA),
        supabase_url=SUPABASE_URL,
        supabase_secret_key=SecretStr(chave),
        supabase_bucket=BUCKET,
        exigir_snapshot_novo=False,
        data_esperada=None,
    )


class Bucket:
    def __init__(self, chave: str) -> None:
        self._cliente = httpx.Client(
            base_url=f"{SUPABASE_URL}/storage/v1",
            headers={"apikey": chave, "Authorization": f"Bearer {chave}"},
            timeout=30,
        )

    def subir(self, caminho: str, dados: bytes) -> None:
        r = self._cliente.post(
            f"/object/{BUCKET}/{caminho}",
            content=dados,
            headers={"x-upsert": "true", "Content-Type": "application/octet-stream"},
        )
        assert r.is_success, f"upload falhou: {r.status_code} {r.text}"

    def apagar(self, *caminhos: str) -> None:
        r = self._cliente.request("DELETE", f"/object/{BUCKET}", json={"prefixes": list(caminhos)})
        assert r.is_success, f"delete falhou: {r.status_code} {r.text}"

    def fechar(self) -> None:
        self._cliente.close()


@pytest.fixture
def bucket(chave: str) -> Iterator[Bucket]:
    b = Bucket(chave)
    b.apagar(CAMINHO_A, CAMINHO_B)
    yield b
    b.apagar(CAMINHO_A, CAMINHO_B)
    b.fechar()


@pytest.fixture(scope="session")
def admin() -> Iterator[AdminConn]:
    with psycopg.connect(DATABASE_URL_ADMIN, autocommit=True) as conn:
        yield conn


_TABELAS_MART = ("mart.carga", "mart.carga_fonte", "mart.lancamento")


@pytest.fixture
def mart_restaurado(admin: AdminConn) -> Iterator[None]:
    """Guarda o mart antes do teste e devolve exatamente igual depois (a API usa o seed)."""
    copia: dict[str, tuple[list[str], list[TupleRow]]] = {}
    for tabela in _TABELAS_MART:
        cur = admin.execute(f"select * from {tabela}")  # noqa: S608 — nome fixo acima
        assert cur.description is not None
        copia[tabela] = ([c.name for c in cur.description], cur.fetchall())
    yield
    with admin.transaction():
        admin.execute("delete from mart.carga_fonte")
        admin.execute("delete from mart.carga")
        admin.execute("delete from mart.lancamento")
        for tabela in _TABELAS_MART:
            colunas, linhas = copia[tabela]
            if not linhas:
                continue
            lista = ", ".join(colunas)
            marcadores = ", ".join(["%s"] * len(colunas))
            with admin.cursor() as cur:
                cur.executemany(
                    f"insert into {tabela} ({lista}) overriding system value "  # noqa: S608
                    f"values ({marcadores})",
                    linhas,
                )
