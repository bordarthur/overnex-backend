"""Cadastra um usuário do Overnex e manda o convite por e-mail (só a BORD usa).

Uso (em api/):
    uv run python -m app.cli.convidar_usuario --email ana@cliente.com.br --nome "Ana Souza" \\
        --perfil cliente_usuario --cliente pj-cortes [--cliente outro-cliente] [--sessao 240]
    uv run python -m app.cli.convidar_usuario --email fulano@bordgestao.com.br \\
        --nome "Fulano" --perfil admin
    uv run python -m app.cli.convidar_usuario --email ana@cliente.com.br --reenviar

Perfis (os 4 fixos; ver app/seguranca/perfis.py): admin (Admin BORD: vê todas as empresas,
sem --cliente, e só com e-mail de ADMIN_DOMINIOS), consultor, cliente_principal e
cliente_usuario (um ou mais --cliente, pelo slug). O vínculo com o cliente leva o papel do
perfil, o perfil fixo do cliente e todas as empresas dele (seg.definir_vinculos). Depois de
criado, perfil, empresas e sessão mudam pela Gestão.

1. confere no banco os clientes e se o e-mail já existe;
2. convida no Supabase Auth (e-mail com o link para {FRONT_URL}/definir-senha);
3. grava seg.usuario e os vínculos numa transação só.

Se o convite falhar, nada é gravado. Se a gravação falhar depois do convite, o usuário
criado no Supabase Auth é apagado. --reenviar manda o convite de novo a quem ainda não
entrou (o link vence; a Gestão mostra "Convite vencido"). Precisa de SUPABASE_URL,
SUPABASE_SERVICE_ROLE_KEY, FRONT_URL e DATABASE_URL_ADMIN (ver api/README.md).

O Admin BORD também convida pela Gestão (POST /api/admin/usuarios): a tela grava o pedido
numa fila e o job app/cli/enviar_convites.py manda. Este comando continua valendo, e o
--reenviar serve também para quem ficou com "Convite não enviado" na Gestão.
"""

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

import psycopg
from psycopg.rows import DictRow, dict_row
from pydantic import ValidationError

from app.auth.gotrue import GoTrue, GoTrueErro, GoTrueIndisponivel
from app.core.config import DOMINIOS_ADMIN_PADRAO, ConfigCadastro, email_de_admin
from app.seguranca import perfis

PERFIS = tuple(p.id for p in perfis.PERFIS)
SESSOES = (30, 60, 240, 480, 720, 1440)
# acima disto, só Admin BORD
SESSAO_MAXIMA_COMUM = 720


class CadastroInvalido(Exception):
    """Pedido que não pode seguir; a mensagem vai para quem rodou o comando."""


@dataclass(frozen=True)
class Pedido:
    email: str
    nome: str
    perfil: str
    # slugs dos clientes da pessoa; vazio para o admin
    clientes: tuple[str, ...]
    # minutos de sessão parada; None = o padrão do perfil
    sessao: int | None = None

    @property
    def tipo(self) -> str:
        """seg.usuario.tipo (só informativo: não dá acesso a nada)."""
        return "lann" if self.perfil in (perfis.ADMIN, perfis.CONSULTOR) else "cliente"


@dataclass(frozen=True)
class Plano:
    # slug → cfg.cliente.id
    clientes: dict[str, UUID]


@dataclass(frozen=True)
class Pendente:
    """Quem foi convidado e ainda não entrou."""

    usuario_id: UUID
    nome: str


class Banco(Protocol):
    def planejar(self, pedido: Pedido) -> Plano: ...

    def gravar(self, pedido: Pedido, plano: Plano, id_autenticacao: UUID) -> UUID: ...

    def pendente(self, email: str) -> Pendente: ...

    def marcar_convite(self, usuario_id: UUID, id_autenticacao: UUID) -> None: ...


class BancoPostgres:
    """seg e cfg pela conexão administrativa (postgres, que ignora o RLS)."""

    def __init__(self, conn: psycopg.Connection[DictRow]) -> None:
        self.conn = conn

    def planejar(self, pedido: Pedido) -> Plano:
        conn = self.conn
        existe = conn.execute(
            "select 1 from seg.usuario where lower(email) = lower(%s)", (pedido.email,)
        ).fetchone()
        if existe:
            raise CadastroInvalido(f"O e-mail {pedido.email} já está cadastrado no Overnex.")

        achados = {
            r["slug"]: r
            for r in conn.execute(
                """
                select c.slug, c.id, c.ativo,
                       exists (select 1 from cfg.empresa e where e.cliente_id = c.id) as tem_empresa
                  from cfg.cliente c
                 where c.slug = any(%s::text[])
                """,
                (list(pedido.clientes),),
            )
        }
        for slug in pedido.clientes:
            cliente = achados.get(slug)
            if cliente is None:
                raise CadastroInvalido(f"Cliente '{slug}' não encontrado (use o slug).")
            if not cliente["ativo"]:
                raise CadastroInvalido(f"Cliente '{slug}' está inativo.")
            if not cliente["tem_empresa"]:
                raise CadastroInvalido(f"O cliente '{slug}' não tem empresas cadastradas.")
        return Plano(clientes={slug: achados[slug]["id"] for slug in pedido.clientes})

    def gravar(self, pedido: Pedido, plano: Plano, id_autenticacao: UUID) -> UUID:
        conn = self.conn
        with conn.transaction():
            linha = conn.execute(
                """
                insert into seg.usuario
                    (id_autenticacao, email, nome, tipo, perfil, sessao_minutos, convidado_em)
                values (%s, %s, %s, %s, %s, %s, now())
                returning id
                """,
                (
                    id_autenticacao,
                    pedido.email,
                    pedido.nome,
                    pedido.tipo,
                    pedido.perfil,
                    pedido.sessao,
                ),
            ).fetchone()
            if linha is None:
                raise RuntimeError("insert em seg.usuario não devolveu o id")
            usuario_id: UUID = linha["id"]
            # vínculo com o papel do perfil, perfil fixo de cada cliente e as empresas dele
            conn.execute(
                "select seg.definir_vinculos(%s, %s::uuid[])",
                (usuario_id, list(plano.clientes.values())),
            )
        return usuario_id

    def pendente(self, email: str) -> Pendente:
        linha = self.conn.execute(
            "select id, nome, ativo, primeiro_acesso_em from seg.usuario"
            " where lower(email) = lower(%s)",
            (email,),
        ).fetchone()
        if linha is None:
            raise CadastroInvalido(f"O e-mail {email} não está cadastrado no Overnex.")
        if not linha["ativo"]:
            raise CadastroInvalido(f"O acesso de {email} está desativado. Reative pela Gestão.")
        if linha["primeiro_acesso_em"] is not None:
            raise CadastroInvalido(
                f"{email} já entrou no Overnex. Para trocar a senha, use 'Esqueci a senha'."
            )
        return Pendente(usuario_id=linha["id"], nome=linha["nome"] or email.split("@")[0])

    def marcar_convite(self, usuario_id: UUID, id_autenticacao: UUID) -> None:
        # o id também: quem foi convidado pela Gestão e o envio falhou ainda está sem login
        self.conn.execute(
            "update seg.usuario set convidado_em = now(), id_autenticacao = %s where id = %s",
            (id_autenticacao, usuario_id),
        )


def _convidar_no_auth(gotrue: GoTrue, email: str, nome: str, front_url: str) -> UUID:
    """Manda o convite; devolve o id do usuário no Supabase Auth."""
    try:
        return gotrue.convidar(email, f"{front_url}/definir-senha", {"nome": nome})
    except GoTrueErro as exc:
        if exc.codigo == "email_exists" or "already been registered" in str(exc.corpo.get("msg")):
            raise CadastroInvalido(
                f"O e-mail {email} já existe no Supabase Auth. Nada foi gravado."
            ) from exc
        raise CadastroInvalido(
            f"O Supabase Auth recusou o convite ({exc.status}, {exc.codigo}). Nada foi gravado."
        ) from exc
    except GoTrueIndisponivel as exc:
        raise CadastroInvalido("Supabase Auth indisponível. Nada foi gravado.") from exc


def convidar(pedido: Pedido, banco: Banco, gotrue: GoTrue, front_url: str) -> tuple[UUID, Plano]:
    """Confere, convida e grava. Devolve o seg.usuario.id e o que foi liberado."""
    plano = banco.planejar(pedido)
    id_autenticacao = _convidar_no_auth(gotrue, pedido.email, pedido.nome, front_url)

    try:
        usuario_id = banco.gravar(pedido, plano, id_autenticacao)
    except Exception:
        # sem o cadastro no banco, o login do Supabase não serve para nada: desfaz
        try:
            gotrue.apagar_usuario(id_autenticacao)
        except (GoTrueErro, GoTrueIndisponivel):
            print(
                f"Atenção: apague à mão no Supabase Auth o usuário {id_autenticacao} "
                f"({pedido.email}).",
                file=sys.stderr,
            )
        raise
    return usuario_id, plano


def reenviar(email: str, banco: Banco, gotrue: GoTrue, front_url: str) -> UUID:
    """Manda o convite de novo a quem ainda não entrou. Devolve o seg.usuario.id."""
    pendente = banco.pendente(email)
    id_autenticacao = _convidar_no_auth(gotrue, email, pendente.nome, front_url)
    banco.marcar_convite(pendente.usuario_id, id_autenticacao)
    return pendente.usuario_id


def _pedido(argv: Sequence[str] | None) -> Pedido | str:
    """O pedido de cadastro, ou só o e-mail quando é --reenviar. O domínio do e-mail do
    admin é conferido depois, com a configuração (conferir_admin)."""
    parser = argparse.ArgumentParser(
        prog="python -m app.cli.convidar_usuario",
        description="Cadastra um usuário do Overnex e manda o convite por e-mail.",
    )
    parser.add_argument("--email", required=True)
    parser.add_argument("--nome", help="nome que aparece no Overnex")
    parser.add_argument("--perfil", choices=PERFIS)
    parser.add_argument(
        "--cliente",
        action="append",
        default=[],
        help="slug do cliente (ex.: pj-cortes); pode repetir; não vale para o admin",
    )
    parser.add_argument(
        "--sessao",
        type=int,
        choices=SESSOES,
        help="minutos de sessão parada (padrão: o do perfil); mais de 720, só admin",
    )
    parser.add_argument(
        "--reenviar",
        action="store_true",
        help="manda o convite de novo a quem ainda não entrou (só com --email)",
    )
    args = parser.parse_args(argv)

    email = args.email.strip().lower()
    if "@" not in email:
        parser.error("--email inválido")
    if args.reenviar:
        if args.nome or args.perfil or args.cliente or args.sessao:
            parser.error("--reenviar vai só com --email")
        return str(email)

    nome = (args.nome or "").strip()
    if not nome:
        parser.error("--nome é obrigatório")
    if not args.perfil:
        parser.error("--perfil é obrigatório")
    clientes = tuple(dict.fromkeys(c.strip() for c in args.cliente if c.strip()))
    if args.perfil == perfis.ADMIN:
        if clientes:
            parser.error("o perfil admin vê todas as empresas: não use --cliente")
    elif not clientes:
        parser.error("--cliente é obrigatório para este perfil")
    if args.sessao and args.sessao > SESSAO_MAXIMA_COMUM and args.perfil != perfis.ADMIN:
        parser.error("--sessao acima de 720 (12 h) é só para o perfil admin")
    return Pedido(email=email, nome=nome, perfil=args.perfil, clientes=clientes, sessao=args.sessao)


def conferir_admin(pedido: Pedido | str, admin_dominios: str = DOMINIOS_ADMIN_PADRAO) -> None:
    """Admin BORD só com e-mail de um dos domínios de ADMIN_DOMINIOS."""
    if (
        isinstance(pedido, Pedido)
        and pedido.perfil == perfis.ADMIN
        and not email_de_admin(pedido.email, admin_dominios)
    ):
        raise CadastroInvalido("Admin BORD só com e-mail da BORD (ADMIN_DOMINIOS).")


def main(argv: Sequence[str] | None = None) -> int:
    pedido = _pedido(argv)
    try:
        config = ConfigCadastro()  # valores vêm do ambiente (ou do .env)
    except ValidationError as exc:
        # só os nomes: nunca ecoar os valores (são segredos)
        nomes = sorted({str(e["loc"][0]).upper() for e in exc.errors() if e["loc"]})
        problemas = ", ".join(nomes) or "; ".join(str(e["msg"]) for e in exc.errors())
        print(f"Erro: configuração incompleta ({problemas}). Ver api/README.md.", file=sys.stderr)
        return 1
    try:
        conferir_admin(pedido, config.admin_dominios)
    except CadastroInvalido as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1
    gotrue = GoTrue(config.supabase_url, config.supabase_service_role_key.get_secret_value())
    try:
        with psycopg.connect(
            config.database_url_admin.get_secret_value(), autocommit=True, row_factory=dict_row
        ) as conn:
            banco = BancoPostgres(conn)
            if isinstance(pedido, str):
                usuario_id = reenviar(pedido, banco, gotrue, config.front_url)
                print(f"Convite reenviado para {pedido} (seg.usuario {usuario_id}).")
                return 0
            usuario_id, plano = convidar(pedido, banco, gotrue, config.front_url)
    except CadastroInvalido as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1
    finally:
        gotrue.close()

    print(f"Convite enviado para {pedido.email} (seg.usuario {usuario_id}).")
    print(f"  perfil:   {perfis.rotulo(pedido.perfil)}")
    print(f"  empresas: {', '.join(plano.clientes) or 'todas (Admin BORD)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
