from enum import StrEnum
from functools import lru_cache
from typing import Self

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Ambiente(StrEnum):
    DEV = "dev"
    HOMOLOG = "homolog"
    PROD = "prod"


class ProvedorAuth(StrEnum):
    # E-mail e senha no Supabase Auth, pelas rotas /api/auth/* (app/auth).
    SUPABASE = "supabase"
    # Microsoft Entra ID (desligado por enquanto; a validação continua em seguranca/auth.py).
    ENTRA = "entra"


class Settings(BaseSettings):
    # hide_input_in_errors: erro de configuração nunca mostra os valores (há segredos)
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", hide_input_in_errors=True
    )

    # Conexão do role api_site (no Supabase, pelo pooler em modo transação).
    database_url: SecretStr
    auth_provedor: ProvedorAuth = ProvedorAuth.SUPABASE
    entra_tenant_id: str = ""
    entra_audience: str = ""
    # Projeto do Supabase (ex.: https://<ref>.supabase.co). Os JWTs são validados pelo JWKS
    # em {SUPABASE_URL}/auth/v1/.well-known/jwks.json.
    supabase_url: str = ""
    # Chave publishable (ou anon legada): vai no header apikey das chamadas ao GoTrue.
    supabase_anon_key: SecretStr = SecretStr("")
    # Base dos links dos e-mails (convite e recuperação levam a {FRONT_URL}/definir-senha).
    front_url: str = ""
    # Secure no cookie do refresh token; obrigatório fora de dev.
    cookie_seguro: bool = True
    # Max-Age do cookie do refresh token. O Supabase não expira o refresh token (prazo de
    # sessão e de inatividade são do plano Pro): este é o prazo de fato. Cada renovação troca
    # o cookie e recomeça a contagem. Se configurar "Inactivity timeout" no Supabase, use o
    # mesmo valor aqui.
    auth_refresh_segundos: int = 7 * 24 * 3600
    # Padrão seguro: sem AMBIENTE explícito, comporta-se como produção.
    ambiente: Ambiente = Ambiente.PROD
    auth_fake: bool = False
    # Visão geral com as seções fictícias de app/telas/visao_geral/demo.json (só dev).
    visao_geral_demo: bool = False
    # Cartões "a decidir" dos painéis (ver telas/paineis/router.py), para revisão (só dev).
    paineis_a_decidir: bool = False
    # Prazo da resposta pronta da Visão geral em memória (0 desliga; ver telas/visao_geral).
    visao_geral_cache_segundos: int = 900

    # Conexões sempre abertas. Abrir uma nova ao pooler custa ~0,4 s (várias idas e voltas):
    # a Visão geral usa até 3 por requisição (seções em paralelo), a Equipe comercial 2, e a
    # pré-montagem depois de /api/sessao (app/telas/preparo.py) roda junto com a tela aberta.
    db_pool_min: int = 8
    db_pool_max: int = 20

    @model_validator(mode="after")
    def _validar(self) -> Self:
        self.supabase_url = self.supabase_url.rstrip("/")
        self.front_url = self.front_url.rstrip("/")
        dev = self.ambiente is Ambiente.DEV
        if self.auth_fake and not dev:
            raise ValueError("AUTH_FAKE só é permitido com AMBIENTE=dev.")
        if self.visao_geral_demo and not dev:
            raise ValueError("VISAO_GERAL_DEMO só é permitido com AMBIENTE=dev.")
        if self.paineis_a_decidir and not dev:
            raise ValueError("PAINEIS_A_DECIDIR só é permitido com AMBIENTE=dev.")
        if not self.cookie_seguro and not dev:
            raise ValueError("COOKIE_SEGURO=false só é permitido com AMBIENTE=dev.")
        if self.auth_provedor is ProvedorAuth.SUPABASE and not self.supabase_configurado:
            raise ValueError(
                "SUPABASE_URL, SUPABASE_ANON_KEY e FRONT_URL são obrigatórios com "
                "AUTH_PROVEDOR=supabase."
            )
        if (
            self.auth_provedor is ProvedorAuth.ENTRA
            and not self.auth_fake
            and not self.entra_configurado
        ):
            raise ValueError(
                "ENTRA_TENANT_ID e ENTRA_AUDIENCE são obrigatórios com AUTH_PROVEDOR=entra "
                "sem AUTH_FAKE."
            )
        if self.auth_refresh_segundos <= 0:
            raise ValueError("AUTH_REFRESH_SEGUNDOS precisa ser maior que zero.")
        return self

    @property
    def entra_configurado(self) -> bool:
        return bool(self.entra_tenant_id and self.entra_audience)

    @property
    def supabase_configurado(self) -> bool:
        return bool(
            self.supabase_url and self.supabase_anon_key.get_secret_value() and self.front_url
        )


class ConfigCadastro(BaseSettings):
    """Configuração do CLI de cadastro de usuários (app/cli/convidar_usuario.py).

    Fica fora de Settings de propósito: a chave secreta e a conexão administrativa não
    entram no ambiente da API publicada.
    """

    # hide_input_in_errors: erro de configuração nunca mostra os valores (há segredos)
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", hide_input_in_errors=True
    )

    supabase_url: str
    # Chave secreta (sb_secret_…) ou service_role legada: convida e apaga usuários no Auth.
    supabase_service_role_key: SecretStr
    front_url: str
    # Conexão como postgres (ignora o RLS): grava seg.usuario e os vínculos.
    database_url_admin: SecretStr

    @model_validator(mode="after")
    def _validar(self) -> Self:
        self.supabase_url = self.supabase_url.rstrip("/")
        self.front_url = self.front_url.rstrip("/")
        if not (self.supabase_url and self.front_url):
            raise ValueError("SUPABASE_URL e FRONT_URL são obrigatórios.")
        if not self.supabase_service_role_key.get_secret_value():
            raise ValueError("SUPABASE_SERVICE_ROLE_KEY é obrigatória.")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()  # valores vêm do ambiente
