"""Contrato das telas: o JSON que o front desenha sem calcular nada."""

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, PlainSerializer

Status = Literal["saudavel", "atencao", "critico"]
Formato = Literal["moeda", "percentual", "numero"]

# Decimal no banco; número no JSON (o front formata).
Valor = Annotated[Decimal, PlainSerializer(float, return_type=float, when_used="json")]


class _Modelo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Trecho(_Modelo):
    texto: str
    destaque: bool = False


class Alerta(_Modelo):
    trechos: list[Trecho]


class Hero(_Modelo):
    rotulo: str
    valor: Valor | None
    formato: Formato
    status: Status | None = None


class Apoio(_Modelo):
    rotulo: str
    valor: Valor | None
    formato: Formato
    status: Status | None = None


class Ponto(_Modelo):
    rotulo: str
    valor: Valor | None


class Visual(_Modelo):
    tipo: Literal["linha", "barras", "tabela"]
    titulo: str
    pontos: list[Ponto]


class ScreenPayload(_Modelo):
    codigo_tela: str
    titulo: str
    empresa_id: UUID
    mes: date | None
    # data_snapshot da última carga 'ok' do cliente; None se nunca houve carga
    atualizado_em: date | None
    hero: Hero
    apoios: list[Apoio]
    visuais: list[Visual]
    alertas: list[Alerta]
