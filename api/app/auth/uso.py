"""Último uso da sessão de login (seg.sessao_uso), para encerrar a sessão parada.

Uso = pedido autenticado à API (gravado junto com o commit, em app/core/db.py) ou renovação.
O limite é da pessoa: seg.usuario.sessao_minutos ou, sem ele, o padrão do perfil
(seg.limite_sessao, migration 28). Sessão parada há mais que o limite não renova: o /renovar
responde 401 e encerra a sessão no GoTrue. Sessão sem registro também não.
"""

from dataclasses import dataclass
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends

from app.core.db import Pool, get_pool


@dataclass(frozen=True)
class UsoSessao:
    pool: Pool

    def _chamar(self, funcao: str, sessao_id: UUID, id_autenticacao: UUID) -> Any:
        """Chama a função numa ida ao banco só: a conexão do pool está em autocommit, e a
        chamada é a transação inteira."""
        with self.pool.connection() as conn:
            linha = conn.execute(
                f"select seg.{funcao}(%s, %s) as resultado",
                (sessao_id, id_autenticacao),
            ).fetchone()
            return linha["resultado"] if linha else None

    def iniciar(self, sessao_id: UUID, id_autenticacao: UUID) -> None:
        """Login: a sessão nova conta como usada agora (e marca o primeiro acesso)."""
        self._chamar("iniciar_sessao", sessao_id, id_autenticacao)

    def renovar(self, sessao_id: UUID, id_autenticacao: UUID) -> bool:
        """True = dentro do limite da pessoa (e conta como uso); False = parada ou sem
        registro."""
        return bool(self._chamar("renovar_sessao", sessao_id, id_autenticacao))


def get_uso_sessao(pool: Annotated[Pool, Depends(get_pool)]) -> UsoSessao:
    return UsoSessao(pool)
