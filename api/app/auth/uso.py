"""Último uso da sessão de login (seg.sessao_uso), para encerrar a sessão parada.

Uso = pedido autenticado à API (gravado junto com o commit, em app/core/db.py) ou renovação.
Sessão parada há mais que AUTH_INATIVIDADE_SEGUNDOS não renova: o /renovar responde 401 e
encerra a sessão no GoTrue. Sessão sem registro (aberta antes da migration 21) também não.
"""

from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends

from app.core.config import Settings, get_settings
from app.core.db import Pool, get_pool


@dataclass(frozen=True)
class UsoSessao:
    pool: Pool
    limite: timedelta

    def _chamar(self, funcao: str, sessao_id: UUID, id_autenticacao: UUID) -> Any:
        """Chama a função e faz o commit numa ida ao banco só (pipeline)."""
        with self.pool.connection() as conn:
            with conn.pipeline():
                cursor = conn.execute(
                    f"select seg.{funcao}(%s, %s, %s) as resultado",
                    (sessao_id, id_autenticacao, self.limite),
                )
                conn.commit()
            linha = cursor.fetchone()
            return linha["resultado"] if linha else None

    def iniciar(self, sessao_id: UUID, id_autenticacao: UUID) -> None:
        """Login: a sessão nova conta como usada agora."""
        self._chamar("iniciar_sessao", sessao_id, id_autenticacao)

    def renovar(self, sessao_id: UUID, id_autenticacao: UUID) -> bool:
        """True = dentro do limite (e conta como uso); False = parada ou sem registro."""
        return bool(self._chamar("renovar_sessao", sessao_id, id_autenticacao))


def get_uso_sessao(
    pool: Annotated[Pool, Depends(get_pool)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> UsoSessao:
    return UsoSessao(pool, timedelta(seconds=settings.auth_inatividade_segundos))
