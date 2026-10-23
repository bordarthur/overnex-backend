"""Configuração dos testes.

Os testes marcados com `db` rodam contra o Supabase local (`supabase start` +
`supabase db reset`), conectando como api_site. O seed tem a base "Padrão LANN".
"""

import os

os.environ.setdefault(
    "DATABASE_URL", "postgresql://api_site:api_site_local@127.0.0.1:54322/postgres"
)
os.environ["AMBIENTE"] = "dev"
os.environ["AUTH_FAKE"] = "1"
# os testes mudam dados entre uma requisição e outra, sem carga nova
os.environ["VISAO_GERAL_CACHE_SEGUNDOS"] = "0"

from collections.abc import Iterator

import psycopg
import pytest
from fastapi.testclient import TestClient

# Conexão administrativa (postgres) só para conferir efeitos, como o log de acesso.
DATABASE_URL_ADMIN = os.environ.get(
    "DATABASE_URL_ADMIN", "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
)

# IDs do supabase/seed.sql
CLIENTE_A = "c1000000-0000-4000-8000-00000000000a"
CLIENTE_B = "c1000000-0000-4000-8000-00000000000b"
EMPRESA_A1 = "e1000000-0000-4000-8000-0000000000a1"
EMPRESA_A2 = "e1000000-0000-4000-8000-0000000000a2"
EMPRESA_B1 = "e1000000-0000-4000-8000-0000000000b1"
USUARIO_A = "d1000000-0000-4000-8000-00000000000a"
OID_A = "0d000000-0000-4000-8000-00000000000a"
OID_B = "0d000000-0000-4000-8000-00000000000b"
OID_DESCONHECIDO = "0d000000-0000-4000-8000-0000000000ff"


def auth(oid: str) -> dict[str, str]:
    return {"Authorization": f"Bearer fake:{oid}"}


@pytest.fixture(scope="session")
def client() -> Iterator[TestClient]:
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def admin_conn() -> Iterator[psycopg.Connection[tuple[object, ...]]]:
    with psycopg.connect(DATABASE_URL_ADMIN, autocommit=True) as conn:
        yield conn
