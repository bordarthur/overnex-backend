from datetime import date, datetime
from zoneinfo import ZoneInfo

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    # env_ignore_empty: variável vazia (ex.: input não preenchido no workflow) = não definida
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        hide_input_in_errors=True,
    )

    supabase_url: str
    # chave secret do projeto: só no ambiente dos jobs, nunca na API
    supabase_secret_key: SecretStr
    supabase_bucket: str = "overnex-bucket"
    # pasta dos arquivos no bucket (cfg.fonte_carga.caminho_bucket = <prefixo>/<tabela>.parquet)
    prefixo_demo: str = "demo/distribuidora-overnex"
    fuso: str = "America/Fortaleza"
    # AAAA-MM-DD; vazio = hoje no fuso
    data_snapshot: date | None = None

    @field_validator("supabase_url")
    @classmethod
    def _sem_barra_final(cls, valor: str) -> str:
        valor = valor.strip().rstrip("/")
        if not valor.startswith(("http://", "https://")):
            raise ValueError("SUPABASE_URL precisa começar com http:// ou https://")
        return valor

    @field_validator("prefixo_demo")
    @classmethod
    def _prefixo(cls, valor: str) -> str:
        return valor.strip().strip("/")

    def hoje(self) -> date:
        return self.data_snapshot or datetime.now(ZoneInfo(self.fuso)).date()
