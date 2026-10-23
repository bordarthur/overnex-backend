from enum import StrEnum
from functools import lru_cache
from typing import Self

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Ambiente(StrEnum):
    DEV = "dev"
    HOMOLOG = "homolog"
    PROD = "prod"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Conexão do role api_site (no Supabase, pelo pooler em modo transação).
    database_url: SecretStr
    entra_tenant_id: str = ""
    entra_audience: str = ""
    # Padrão seguro: sem AMBIENTE explícito, comporta-se como produção.
    ambiente: Ambiente = Ambiente.PROD
    auth_fake: bool = False
    # Visão geral com as seções fictícias de app/telas/visao_geral/demo.json (só dev).
    visao_geral_demo: bool = False
    # Prazo da resposta pronta da Visão geral em memória (0 desliga; ver telas/visao_geral).
    visao_geral_cache_segundos: int = 900

    db_pool_min: int = 1
    db_pool_max: int = 10

    @model_validator(mode="after")
    def _validar(self) -> Self:
        if self.auth_fake and self.ambiente is not Ambiente.DEV:
            raise ValueError("AUTH_FAKE só é permitido com AMBIENTE=dev.")
        if self.visao_geral_demo and self.ambiente is not Ambiente.DEV:
            raise ValueError("VISAO_GERAL_DEMO só é permitido com AMBIENTE=dev.")
        if not self.auth_fake and not (self.entra_tenant_id and self.entra_audience):
            raise ValueError("ENTRA_TENANT_ID e ENTRA_AUDIENCE são obrigatórios sem AUTH_FAKE.")
        return self

    @property
    def entra_configurado(self) -> bool:
        return bool(self.entra_tenant_id and self.entra_audience)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # valores vêm do ambiente
