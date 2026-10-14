"""Inventário do bucket — com o Storage simulado por httpx.MockTransport."""

import json
from datetime import date
from decimal import Decimal

import httpx

from carga.config import ConfigStorage
from carga.inspecionar import Objeto, descrever, inventario, listar
from tests.conftest import gerar_parquet

D = date(2026, 10, 13)


def _config() -> ConfigStorage:
    return ConfigStorage(
        supabase_url="https://xyz.supabase.co",
        supabase_secret_key="sb_secret_teste",  # type: ignore[arg-type]
    )


def _arquivo(nome: str) -> dict[str, object]:
    return {"name": nome, "id": "uuid", "metadata": {"size": 123}}


def _pasta(nome: str) -> dict[str, object]:
    return {"name": nome, "id": None, "metadata": None}


# pasta → conteúdo, como o Storage devolve (uma "pasta" por chamada)
BUCKET = {
    "dataflow": [_pasta("dbo_a"), _pasta("dbo_b")],
    "dataflow/dbo_a": [_arquivo("dbo_a.parquet")],
    "dataflow/dbo_b": [_arquivo("dbo_b.parquet"), _arquivo("leia-me.txt")],
}


def _cliente(vistos: list[dict[str, object]]) -> httpx.Client:
    def responder(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/storage/v1/object/list/overnex-bucket"
        assert request.headers["apikey"] == "sb_secret_teste"
        corpo = json.loads(request.content)
        vistos.append(corpo)
        return httpx.Response(200, json=BUCKET.get(corpo["prefix"], []))

    return httpx.Client(transport=httpx.MockTransport(responder))


def test_listar_percorre_as_pastas() -> None:
    vistos: list[dict[str, object]] = []
    objetos = list(listar(_config(), "dataflow/", _cliente(vistos)))
    assert objetos == [
        Objeto("dataflow/dbo_a/dbo_a.parquet", 123),
        Objeto("dataflow/dbo_b/dbo_b.parquet", 123),
        Objeto("dataflow/dbo_b/leia-me.txt", 123),
    ]
    assert [v["prefix"] for v in vistos] == ["dataflow", "dataflow/dbo_a", "dataflow/dbo_b"]


def test_descrever_mostra_colunas_tipos_e_empresas() -> None:
    dados = gerar_parquet(
        [
            ("1 Nilo Maia", D, 1, Decimal("1.00")),
            ("1 Nilo Maia", D, 2, Decimal("2.00")),
            ("5 Nilo Maia", D, 1, Decimal("3.00")),
        ],
        D,
    )
    texto = descrever("dataflow/x/x.parquet", dados)
    assert "- linhas: 3" in texto
    assert "- _data_snapshot: 2026-10-13" in texto
    assert "| 1 | `Empresa` | string | 0 |" in texto
    assert "| 4 | `Valor` | decimal128(18, 4) | 0 |" in texto
    assert "Valores distintos de `Empresa` (2):" in texto
    assert "create table mart.x (" in texto
    assert '"Valor"          numeric(18, 4)' in texto
    assert "- `'1 Nilo Maia'`: 2 linhas" in texto


def test_inventario_ignora_nao_parquet_e_segue_apos_erro() -> None:
    bom = gerar_parquet([("1 Nilo Maia", D, 1, Decimal(1))], D)

    def baixar(caminho: str) -> bytes:
        return bom if caminho.endswith("dbo_a.parquet") else b"corrompido"

    texto = inventario(_config(), "dataflow", _cliente([]), baixar)
    assert "2 arquivo(s) Parquet." in texto
    assert "leia-me.txt" not in texto
    assert "### `dataflow/dbo_a/dbo_a.parquet`" in texto
    assert "### `dataflow/dbo_b/dbo_b.parquet`\n\nERRO ao ler:" in texto
