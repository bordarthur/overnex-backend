"""Confere, depois do login no Supabase Auth, se o usuário está ativo em seg.usuario.

Ter senha no Supabase não basta: só entra quem a LANN cadastrou e mantém ativo. A decisão
vai para seg.log_acesso (recurso "auth:<rota>"; negado = etapa 1).
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends

from app.core.db import Pool, get_pool


@dataclass(frozen=True)
class UsuarioCadastrado:
    nome: str | None
    email: str


# (id de autenticação, recurso do log) → cadastro, ou None se inexistente ou inativo
VerificarCadastro = Callable[[UUID, str], UsuarioCadastrado | None]


def get_verificar_cadastro(pool: Annotated[Pool, Depends(get_pool)]) -> VerificarCadastro:
    def verificar(id_autenticacao: UUID, recurso: str) -> UsuarioCadastrado | None:
        with pool.connection() as conn:
            try:
                linha = conn.execute(
                    "select id, ativo from seg.resolver_usuario(%s)", (id_autenticacao,)
                ).fetchone()
                usuario_id: UUID | None = linha["id"] if linha else None
                conn.execute(
                    "select set_config('app.usuario_id', %s, true)",
                    (str(usuario_id) if usuario_id else "",),
                )
                cadastro = None
                if linha and linha["ativo"]:
                    dados = conn.execute(
                        "select nome, email from seg.usuario where id = %s", (usuario_id,)
                    ).fetchone()
                    if dados:
                        cadastro = UsuarioCadastrado(nome=dados["nome"], email=dados["email"])
                conn.execute(
                    """
                    insert into seg.log_acesso
                        (usuario_id, id_autenticacao, codigo_tela, resultado, etapa_negada, motivo)
                    values (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        usuario_id,
                        id_autenticacao,
                        recurso,
                        "permitido" if cadastro else "negado",
                        None if cadastro else 1,
                        None if cadastro else "usuário inexistente ou inativo",
                    ),
                )
            except BaseException:
                conn.rollback()
                raise
            conn.commit()
            return cadastro

    return verificar
