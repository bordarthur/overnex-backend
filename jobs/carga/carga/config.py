from datetime import date

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigStorage(BaseSettings):
    """Só o acesso ao Storage (usado também pelo inventário, que não conecta no banco)."""

    # env_ignore_empty: variável vazia (ex.: input não preenchido no workflow) = não definida
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", env_ignore_empty=True, extra="ignore"
    )

    supabase_url: str
    # Chave secret do projeto: só existe no ambiente da carga.
    supabase_secret_key: SecretStr
    supabase_bucket: str = "overnex-bucket"

    @field_validator("supabase_url")
    @classmethod
    def _sem_barra_final(cls, valor: str) -> str:
        valor = valor.strip().rstrip("/")
        if not valor.startswith(("http://", "https://")):
            raise ValueError("SUPABASE_URL precisa começar com http:// ou https://")
        return valor

    @field_validator("supabase_bucket")
    @classmethod
    def _bucket_simples(cls, valor: str) -> str:
        valor = valor.strip().strip("/")
        if not valor or "/" in valor:
            raise ValueError("SUPABASE_BUCKET precisa ser só o nome do bucket")
        return valor


class Settings(ConfigStorage):
    # Role carga, pelo pooler do Supabase em MODO SESSÃO (porta 5432 do pooler).
    database_url_carga: SecretStr
    # true no agendamento: o snapshot de cada fonte precisa ser mais novo que a última carga ok.
    exigir_snapshot_novo: bool = False
    # Opcional: o _data_snapshot de todo arquivo precisa ser exatamente esta data.
    data_esperada: date | None = None
