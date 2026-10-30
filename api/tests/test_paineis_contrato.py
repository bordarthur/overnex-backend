"""Contrato dos painéis comerciais — sem banco."""

import json

import pytest
from pydantic import ValidationError

from app.telas.paineis.contrato import CartaoTabela, PainelPayload
from app.telas.paineis.router import DEMO, demo


@pytest.mark.parametrize(
    "tela", ["vendas", "equipe-comercial", "clientes", "resultado", "receber-pagar", "pessoas"]
)
def test_fixture_do_front_valida_e_volta_igual(tela: str) -> None:
    # demo/<tela>.json é cópia da fixture do front: se o contrato Pydantic divergir do
    # TypeScript (src/contracts/painel.ts), a validação ou a ida e volta quebram aqui.
    original = json.loads((DEMO / f"{tela}.json").read_text("utf-8"))
    payload = demo(tela)  # type: ignore[arg-type]

    assert payload.tela == tela
    assert payload.model_dump(mode="json", by_alias=True, exclude_defaults=True) == original


def test_tipo_de_cartao_desconhecido_recusado() -> None:
    original = json.loads((DEMO / "vendas.json").read_text("utf-8"))
    original["cartoes"][0]["tipo"] = "pizza"
    with pytest.raises(ValidationError):
        PainelPayload.model_validate(original)


def test_largura_fora_da_grade_recusada() -> None:
    with pytest.raises(ValidationError):
        CartaoTabela.model_validate(
            {"id": "x", "tipo": "tabela", "largura": 9, "titulo": "X", "colunas": [], "linhas": []}
        )
