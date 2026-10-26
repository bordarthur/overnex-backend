"""Cadastra um usuário do Overnex e manda o convite por e-mail (só a LANN usa).

Uso (em api/):
    uv run python -m app.cli.convidar_usuario --email ana@cliente.com.br --nome "Ana Souza" \\
        --cliente pj-cortes --papel leitor --perfil "Financeiro" [--perfil "..."] \\
        [--empresas todas | --empresas "Empresa A,Empresa B"] [--tipo cliente|lann]

1. confere no banco o cliente, os perfis, as empresas e se o e-mail já existe;
2. convida no Supabase Auth (e-mail com o link para {FRONT_URL}/definir-senha);
3. grava seg.usuario, usuario_cliente, usuario_perfil e usuario_empresa numa transação só.

Se o convite falhar, nada é gravado. Se a gravação falhar depois do convite, o usuário
criado no Supabase Auth é apagado. Precisa de SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY,
FRONT_URL e DATABASE_URL_ADMIN (ver api/README.md).
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
from app.core.config import ConfigCadastro

PAPEIS = ("leitor", "gestor", "admin")
TIPOS = ("cliente", "lann")
TODAS = "todas"


class CadastroInvalido(Exception):
    """Pedido que não pode seguir; a mensagem vai para quem rodou o comando."""


@dataclass(frozen=True)
class Pedido:
    email: str
    nome: str
    cliente: str
    papel: str
    perfis: tuple[str, ...]
    # None = todas as empresas do cliente
    empresas: tuple[str, ...] | None
    tipo: str


@dataclass(frozen=True)
class Plano:
    cliente_id: UUID
    perfis: dict[str, UUID]
    empresas: dict[str, UUID]


class Banco(Protocol):
    def planejar(self, pedido: Pedido) -> Plano: ...

    def gravar(self, pedido: Pedido, plano: Plano, id_autenticacao: UUID) -> UUID: ...


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

        cliente = conn.execute(
            "select id, ativo from cfg.cliente where slug = %s", (pedido.cliente,)
        ).fetchone()
        if cliente is None:
            raise CadastroInvalido(f"Cliente '{pedido.cliente}' não encontrado (use o slug).")
        if not cliente["ativo"]:
            raise CadastroInvalido(f"Cliente '{pedido.cliente}' está inativo.")
        cliente_id: UUID = cliente["id"]

        perfis = {
            r["nome"]: r["id"]
            for r in conn.execute(
                "select id, nome from cfg.perfil_acesso where cliente_id = %s and nome = any(%s)",
                (cliente_id, list(pedido.perfis)),
            )
        }
        faltam = [p for p in pedido.perfis if p not in perfis]
        if faltam:
            raise CadastroInvalido(
                f"Perfil não encontrado no cliente '{pedido.cliente}': {', '.join(faltam)}."
            )

        todas = {
            r["nome"]: r["id"]
            for r in conn.execute(
                "select id, nome from cfg.empresa where cliente_id = %s order by nome",
                (cliente_id,),
            )
        }
        if pedido.empresas is None:
            empresas = todas
        else:
            faltam = [e for e in pedido.empresas if e not in todas]
            if faltam:
                raise CadastroInvalido(
                    f"Empresa não encontrada no cliente '{pedido.cliente}': {', '.join(faltam)}."
                )
            empresas = {e: todas[e] for e in pedido.empresas}
        if not empresas:
            raise CadastroInvalido(f"O cliente '{pedido.cliente}' não tem empresas cadastradas.")
        return Plano(cliente_id=cliente_id, perfis=perfis, empresas=empresas)

    def gravar(self, pedido: Pedido, plano: Plano, id_autenticacao: UUID) -> UUID:
        conn = self.conn
        with conn.transaction():
            linha = conn.execute(
                """
                insert into seg.usuario (id_autenticacao, email, nome, tipo)
                values (%s, %s, %s, %s)
                returning id
                """,
                (id_autenticacao, pedido.email, pedido.nome, pedido.tipo),
            ).fetchone()
            if linha is None:
                raise RuntimeError("insert em seg.usuario não devolveu o id")
            usuario_id: UUID = linha["id"]
            conn.execute(
                "insert into seg.usuario_cliente (usuario_id, cliente_id, papel) "
                "values (%s, %s, %s)",
                (usuario_id, plano.cliente_id, pedido.papel),
            )
            with conn.cursor() as cur:
                cur.executemany(
                    "insert into seg.usuario_perfil (usuario_id, cliente_id, perfil_id) "
                    "values (%s, %s, %s)",
                    [(usuario_id, plano.cliente_id, p) for p in plano.perfis.values()],
                )
                cur.executemany(
                    "insert into seg.usuario_empresa (usuario_id, cliente_id, empresa_id) "
                    "values (%s, %s, %s)",
                    [(usuario_id, plano.cliente_id, e) for e in plano.empresas.values()],
                )
        return usuario_id


def convidar(pedido: Pedido, banco: Banco, gotrue: GoTrue, front_url: str) -> tuple[UUID, Plano]:
    """Confere, convida e grava. Devolve o seg.usuario.id e o que foi liberado."""
    plano = banco.planejar(pedido)
    try:
        id_autenticacao = gotrue.convidar(
            pedido.email, f"{front_url}/definir-senha", {"nome": pedido.nome}
        )
    except GoTrueErro as exc:
        if exc.codigo == "email_exists" or "already been registered" in str(exc.corpo.get("msg")):
            raise CadastroInvalido(
                f"O e-mail {pedido.email} já existe no Supabase Auth. Nada foi gravado."
            ) from exc
        raise CadastroInvalido(
            f"O Supabase Auth recusou o convite ({exc.status}, {exc.codigo}). Nada foi gravado."
        ) from exc
    except GoTrueIndisponivel as exc:
        raise CadastroInvalido("Supabase Auth indisponível. Nada foi gravado.") from exc

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


def _pedido(argv: Sequence[str] | None) -> Pedido:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli.convidar_usuario",
        description="Cadastra um usuário do Overnex e manda o convite por e-mail.",
    )
    parser.add_argument("--email", required=True)
    parser.add_argument("--nome", required=True, help="nome que aparece no Overnex")
    parser.add_argument("--cliente", required=True, help="slug do cliente (ex.: pj-cortes)")
    parser.add_argument("--papel", required=True, choices=PAPEIS)
    parser.add_argument(
        "--perfil", required=True, action="append", help="perfil de acesso (pode repetir)"
    )
    parser.add_argument(
        "--empresas",
        default=TODAS,
        help='"todas" (padrão) ou nomes separados por vírgula, como em cfg.empresa',
    )
    parser.add_argument("--tipo", default="cliente", choices=TIPOS)
    args = parser.parse_args(argv)

    email = args.email.strip().lower()
    if "@" not in email:
        parser.error("--email inválido")
    nome = args.nome.strip()
    if not nome:
        parser.error("--nome vazio")
    empresas = (
        None
        if args.empresas.strip().lower() == TODAS
        else tuple(e.strip() for e in args.empresas.split(",") if e.strip())
    )
    if empresas == ():
        parser.error("--empresas vazio")
    return Pedido(
        email=email,
        nome=nome,
        cliente=args.cliente.strip(),
        papel=args.papel,
        perfis=tuple(dict.fromkeys(p.strip() for p in args.perfil)),
        empresas=empresas,
        tipo=args.tipo,
    )


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
    gotrue = GoTrue(config.supabase_url, config.supabase_service_role_key.get_secret_value())
    try:
        with psycopg.connect(
            config.database_url_admin.get_secret_value(), autocommit=True, row_factory=dict_row
        ) as conn:
            usuario_id, plano = convidar(pedido, BancoPostgres(conn), gotrue, config.front_url)
    except CadastroInvalido as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1
    finally:
        gotrue.close()

    print(f"Convite enviado para {pedido.email} (seg.usuario {usuario_id}).")
    print(f"  cliente:  {pedido.cliente} ({pedido.papel})")
    print(f"  perfis:   {', '.join(plano.perfis)}")
    print(f"  empresas: {', '.join(plano.empresas)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
