"""Convite pela Gestão: a API grava o pedido numa fila e um job manda o e-mail.

Criar o login no Supabase Auth exige a chave secreta, que não entra no ambiente da API
(decisão de 13/11/2026: opção E de docs/convite-pela-gestao-opcoes.md). Aqui só se confere o
pedido e se grava a fila (seg.convite_fila, migration 29). Quem manda o convite é o job
app/cli/enviar_convites.py, no GitHub Actions, onde a chave já está. Depois de gravar, a API
aciona o job (se GITHUB_CONVITES_TOKEN estiver configurado); sem isso, o pedido espera a
rodada de hora em hora.

Regras, com o texto pronto no erro (422):
- as mesmas da edição (app/admin/usuarios.py): perfil, Admin BORD só com e-mail da BORD,
  empresas que existem, tempo de sessão;
- o e-mail é o login: um por pessoa;
- pode convidar sem empresa: a pessoa fica "Sem empresa" até alguém marcar uma;
- reenvio só para quem está ativo, ainda não entrou e não tem pedido na fila.

O que só o envio descobre (limite de e-mails, serviço de login fora do ar) aparece depois,
na situação da pessoa ("Convite não enviado", com o motivo).
"""

import logging
import re
from collections.abc import Sequence
from uuid import UUID

import httpx
from psycopg import errors

from app.admin.usuarios import (
    MSG_ADMIN_EMAIL,
    MSG_CLIENTE,
    MSG_NAO_ENCONTRADA,
    MSG_PERFIL,
    NaoEncontrado,
    RegraInvalida,
    conferir_sessao,
    registrar,
)
from app.core.config import Settings, email_de_admin
from app.core.db import Conexao
from app.seguranca import perfis

logger = logging.getLogger(__name__)

MSG_EMAIL = "E-mail inválido."
MSG_JA_TEM = "Este e-mail já tem cadastro no Overnex."
MSG_DESATIVADO = "O acesso desta pessoa está desativado. Reative antes de mandar o convite."
MSG_JA_ENTROU = 'Esta pessoa já entrou no Overnex. Para trocar a senha, ela usa "Esqueci a senha".'
MSG_NA_FILA = "O convite desta pessoa já está na fila."
MSG_SAI_JA = "Convite na fila: sai em instantes."
MSG_SAI_DEPOIS = "Convite na fila: sai na próxima rodada de envio, em até uma hora."

# formato mínimo; quem valida de verdade é o envio do e-mail
EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")

SQL_CONFERIR = """
    select exists (select 1 from seg.usuario u where lower(u.email) = %(email)s) as existe,
           seg.sessao_padrao(%(perfil)s::text) as padrao,
           coalesce((
               select jsonb_agg(jsonb_build_object('id', c.id, 'slug', c.slug))
                 from cfg.cliente c
                where c.slug = any(%(slugs)s::text[]) and c.ativo
           ), '[]'::jsonb) as pedidos
"""
SQL_PEDIR = """
    select seg.pedir_convite(
        %(email)s::text, %(nome)s::text, %(perfil)s::text, %(sessao)s::integer,
        %(clientes)s::uuid[]
    ) as id
"""
SQL_PENDENTE = """
    select u.email, u.ativo, u.primeiro_acesso_em is not null as entrou,
           exists (
               select 1 from seg.convite_fila f
                where f.usuario_id = u.id and f.estado = 'fila'
           ) as na_fila
      from seg.usuario u
     where u.id = %(id)s
"""
SQL_REPEDIR = """
    insert into seg.convite_fila (usuario_id, pedido_por) values (%(id)s, %(autor)s)
"""


def mensagem(settings: Settings) -> str:
    """Texto do passo final, conforme a API consiga ou não acionar o job na hora."""
    return MSG_SAI_JA if settings.convites_na_hora else MSG_SAI_DEPOIS


def pedir(
    conn: Conexao,
    autor: UUID,
    *,
    email: str,
    nome: str,
    perfil: str,
    clientes: Sequence[str],
    sessao_minutos: int | None,
    admin_dominios: str,
) -> UUID:
    """Cria a pessoa (ainda sem login) com os vínculos e põe o convite na fila. Devolve o
    seg.usuario.id. Não confirma a transação."""
    email = email.strip().lower()
    nome = nome.strip()
    if not EMAIL.fullmatch(email):
        raise RegraInvalida(MSG_EMAIL)
    if perfil not in perfis.POR_ID:
        raise RegraInvalida(MSG_PERFIL)
    admin = perfil == perfis.ADMIN
    if admin and not email_de_admin(email, admin_dominios):
        raise RegraInvalida(MSG_ADMIN_EMAIL)
    if sessao_minutos is not None:
        conferir_sessao(sessao_minutos, perfil)
    slugs = [] if admin else list(dict.fromkeys(clientes))

    atual = conn.execute(
        SQL_CONFERIR, {"email": email, "perfil": perfil, "slugs": slugs}
    ).fetchone()
    if atual is None:
        raise LookupError("a conferência do convite não devolveu linha")
    if atual["existe"]:
        raise RegraInvalida(MSG_JA_TEM)
    pedidos = {c["slug"]: c["id"] for c in atual["pedidos"]}
    faltam = [s for s in slugs if s not in pedidos]
    if faltam:
        raise RegraInvalida(MSG_CLIENTE.format(", ".join(faltam)))
    # igual ao padrão do perfil = sem valor próprio
    sessao = None if sessao_minutos == atual["padrao"] else sessao_minutos

    try:
        linha = conn.execute(
            SQL_PEDIR,
            {
                "email": email,
                "nome": nome,
                "perfil": perfil,
                "sessao": sessao,
                "clientes": [pedidos[s] for s in slugs],
            },
        ).fetchone()
    except errors.UniqueViolation as exc:
        # outro pedido com o mesmo e-mail chegou entre a conferência e a gravação
        raise RegraInvalida(MSG_JA_TEM) from exc
    if linha is None:
        raise LookupError("seg.pedir_convite não devolveu o id")
    usuario_id: UUID = linha["id"]
    registrar(
        conn,
        autor,
        "seg.usuario",
        str(usuario_id),
        "insert",
        None,
        {
            "email": email,
            "nome": nome,
            "perfil": perfil,
            "sessao_minutos": sessao,
            "clientes": sorted(slugs),
            "convite": "fila",
        },
    )
    return usuario_id


def repedir(conn: Conexao, autor: UUID, usuario_id: UUID) -> None:
    """Põe na fila mais um convite para quem ainda não entrou (reenvio, ou nova tentativa
    depois de um envio que falhou). Não confirma a transação."""
    atual = conn.execute(SQL_PENDENTE, {"id": usuario_id}).fetchone()
    if atual is None:
        raise NaoEncontrado(MSG_NAO_ENCONTRADA)
    if not atual["ativo"]:
        raise RegraInvalida(MSG_DESATIVADO)
    if atual["entrou"]:
        raise RegraInvalida(MSG_JA_ENTROU)
    if atual["na_fila"]:
        raise RegraInvalida(MSG_NA_FILA)
    try:
        conn.execute(SQL_REPEDIR, {"id": usuario_id, "autor": autor})
    except errors.UniqueViolation as exc:
        # dois cliques ao mesmo tempo: um pedido pendente por pessoa
        raise RegraInvalida(MSG_NA_FILA) from exc
    registrar(conn, autor, "seg.convite_fila", str(usuario_id), "insert", None, {"convite": "fila"})


def acionar_envio(settings: Settings, *, transport: httpx.BaseTransport | None = None) -> None:
    """Pede ao GitHub que rode o job de convites agora. Nunca levanta erro: se não der, o
    pedido continua na fila e sai na rodada de hora em hora. O token não vai para o log."""
    if not settings.convites_na_hora:
        return
    url = (
        f"https://api.github.com/repos/{settings.github_convites_repo}"
        f"/actions/workflows/{settings.github_convites_workflow}/dispatches"
    )
    cabecalhos = {
        "Authorization": f"Bearer {settings.github_convites_token.get_secret_value()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        with httpx.Client(timeout=httpx.Timeout(10.0, connect=5.0), transport=transport) as http:
            resposta = http.post(
                url, headers=cabecalhos, json={"ref": settings.github_convites_ref}
            )
    except httpx.HTTPError as exc:
        logger.warning("Job de convites não acionado: %s", type(exc).__name__)
        return
    if resposta.status_code >= 300:
        logger.warning("Job de convites não acionado: o GitHub respondeu %s", resposta.status_code)
