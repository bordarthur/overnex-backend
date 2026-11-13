"""Confere, depois do login no Supabase Auth, se o usuário está ativo em seg.usuario.

Ter senha no Supabase não basta: só entra quem a LANN cadastrou e mantém ativo. A decisão
vai para seg.log_acesso (recurso "auth:<rota>"; negado = etapa 1).
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends

from app.core.db import Pool, em_uma_ida, get_pool


@dataclass(frozen=True)
class UsuarioCadastrado:
    nome: str | None
    email: str
    # tempo de sessão parada da pessoa (seg.limite_sessao), para o front contar
    inatividade_segundos: int


SQL_CADASTRO = """
    select nome, email,
           extract(epoch from seg.limite_sessao(id_autenticacao))::integer as inatividade
      from seg.usuario
     where id = %s
"""


# (id de autenticação, recurso do log) → cadastro, ou None se inexistente ou inativo
VerificarCadastro = Callable[[UUID, str], UsuarioCadastrado | None]


def get_verificar_cadastro(pool: Annotated[Pool, Depends(get_pool)]) -> VerificarCadastro:
    def verificar(id_autenticacao: UUID, recurso: str) -> UsuarioCadastrado | None:
        with pool.connection() as conn:
            try:
                # em_uma_ida abre a transação (conexão em autocommit): o set_config abaixo
                # vale até o commit
                resolvido = em_uma_ida(
                    conn,
                    {"u": ("select id, ativo from seg.resolver_usuario(%s)", (id_autenticacao,))},
                )["u"]
                linha = resolvido[0] if resolvido else None
                usuario_id: UUID | None = linha["id"] if linha else None
                conn.execute(
                    "select set_config('app.usuario_id', %s, true)",
                    (str(usuario_id) if usuario_id else "",),
                )
                cadastro = None
                if linha and linha["ativo"]:
                    dados = conn.execute(SQL_CADASTRO, (usuario_id,)).fetchone()
                    if dados:
                        cadastro = UsuarioCadastrado(
                            nome=dados["nome"],
                            email=dados["email"],
                            inatividade_segundos=dados["inatividade"],
                        )
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
