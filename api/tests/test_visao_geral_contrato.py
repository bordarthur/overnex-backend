"""Contrato da Visão geral e partes da moldura — sem banco."""

import json
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from app.core.config import Ambiente, Settings
from app.main import create_app
from app.telas.moldura import Cliente, rotulo_mes, sigla
from app.telas.visao_geral.contrato import Secao, Valor, VisaoGeralPayload
from app.telas.visao_geral.router import DEMO, secoes_demo
from app.telas.visao_geral.texto import inteiro, moeda


def _settings(**kwargs: Any) -> Settings:
    base: dict[str, Any] = {
        "database_url": "postgresql://x@localhost/x",
        "ambiente": Ambiente.DEV,
        "auth_fake": True,
    }
    return Settings(**(base | kwargs))


def test_demo_e_a_fixture_do_front_e_volta_igual() -> None:
    # demo.json é cópia da fixture do front: se o contrato Pydantic divergir do
    # TypeScript, a validação ou a ida e volta quebram aqui.
    original = json.loads(DEMO.read_text("utf-8"))
    secoes = secoes_demo()

    assert len(secoes) == len(original["secoes"])
    volta = TypeAdapter(list[Secao]).dump_python(
        secoes, mode="json", by_alias=True, exclude_defaults=True
    )
    assert volta == original["secoes"]


def test_payload_completo_da_fixture_valida() -> None:
    original = json.loads(DEMO.read_text("utf-8"))
    payload = VisaoGeralPayload.model_validate(original)
    corpo = payload.model_dump(mode="json", by_alias=True, exclude_defaults=True)

    assert corpo["versaoContrato"] == "1.0"
    assert corpo["cliente"] == {"nome": "Cliente Demo", "sigla": "CD", "logoUrl": None}
    assert corpo["dadosAte"] == "2026-10-12"


def test_opcional_sem_valor_sai_do_json_e_nulo_obrigatorio_fica() -> None:
    v = Valor(valor=Decimal(1), unidade="brl")
    assert v.model_dump(mode="json", by_alias=True, exclude_defaults=True) == {
        "valor": 1.0,
        "unidade": "brl",
    }
    c = Cliente(nome="X", sigla="X", logo_url=None)
    assert c.model_dump(mode="json", by_alias=True, exclude_defaults=True) == {
        "nome": "X",
        "sigla": "X",
        "logoUrl": None,
    }


def test_campo_desconhecido_recusado() -> None:
    with pytest.raises(ValidationError):
        Valor.model_validate({"valor": 1, "unidade": "brl", "moeda": "BRL"})


@pytest.mark.parametrize(
    ("nome", "esperado"),
    [
        ("Cliente Demo", "CD"),
        ("Padrão LANN Alfa", "PA"),
        ("Nilo Maia", "NM"),
        ("Overnex", "OV"),
        ("", ""),
    ],
)
def test_sigla(nome: str, esperado: str) -> None:
    assert sigla(nome) == esperado


def test_rotulo_mes() -> None:
    assert rotulo_mes(date(2026, 3, 15)) == "Março de 2026"


def test_visao_geral_demo_so_em_dev() -> None:
    assert _settings(visao_geral_demo=True).visao_geral_demo
    with pytest.raises(ValidationError, match="VISAO_GERAL_DEMO"):
        _settings(
            visao_geral_demo=True,
            ambiente=Ambiente.PROD,
            auth_fake=False,
            entra_tenant_id="t",
            entra_audience="a",
        )


def test_openapi_tem_as_rotas_do_front_em_camel_case() -> None:
    esquema = create_app(_settings()).openapi()

    assert "/api/sessao" in esquema["paths"]
    rota = esquema["paths"]["/api/telas/visao-geral"]["get"]
    empresa = next(p for p in rota["parameters"] if p["name"] == "empresa")
    assert empresa["required"] is False

    componentes = esquema["components"]["schemas"]
    assert {"versaoContrato", "dadosAte", "atualizadoEm"} <= set(
        componentes["VisaoGeralPayload"]["properties"]
    )
    assert "deltaRuim" in componentes["IndicadorPeriodo"]["properties"]


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [
        ("6540000", "R$ 6,54 mi"),
        ("-16647519.88", "R$ 16,65 mi"),
        ("360000", "R$ 360 mil"),
        ("2995", "R$ 3 mil"),
        ("950.4", "R$ 950"),
    ],
)
def test_moeda_compacta_como_no_front(valor: str, esperado: str) -> None:
    assert moeda(Decimal(valor)) == esperado


def test_inteiro_com_separador_de_milhar() -> None:
    assert inteiro(14641) == "14.641"
