"""Manda os convites que a Gestão pôs na fila (job do GitHub Actions: convites.yml).

Uso (em api/):
    uv run python -m app.cli.enviar_convites

A Gestão não cria login: grava o pedido em seg.convite_fila (app/admin/convites.py). Este job
roda onde a chave secreta do Supabase já está, lê os pedidos pendentes, manda o convite pelo
Supabase Auth (o mesmo e-mail do comando de cadastro, com o link para
{FRONT_URL}/definir-senha) e grava o resultado:

- saiu: a pessoa ganha o id do Supabase Auth e a data do convite ("Convite enviado");
- não saiu: o pedido fica como falhou, com o motivo em texto pronto para a tela ("Convite
  não enviado"). Quem decide tentar de novo é o Admin BORD, pelo botão da Gestão.

Antes dos convites, apaga no Supabase Auth o login de quem foi apagado pela Gestão
(seg.login_a_apagar, migration 32): primeiro, para o e-mail de quem foi apagado e convidado
de novo já estar livre. O que não der para apagar fica na fila para a próxima rodada.

Fala com o banco pelo role carga, que em seg só executa as funções das duas filas
(migrations 29 e 32). Precisa de SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY,
FRONT_URL e DATABASE_URL_CARGA. Não escreve e-mail, nome nem chave na saída: só o número do
pedido e o resultado.
"""

import sys
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

import psycopg
from psycopg.rows import DictRow, dict_row
from pydantic import ValidationError

from app.auth.gotrue import GoTrue, GoTrueErro, GoTrueIndisponivel
from app.core.config import ConfigConvites

MSG_DESATIVADO = "O acesso foi desativado antes de o convite sair."
MSG_JA_ENTROU = "A pessoa já entrou no Overnex: não precisa de convite."
MSG_INDISPONIVEL = "O serviço de login estava fora do ar. Tente de novo."
MSG_LIMITE = "O limite de e-mails do Supabase foi atingido. Tente de novo mais tarde."
MSG_JA_EXISTE = (
    "Este e-mail já tem um login criado fora do Overnex. Confira em Authentication → Users, "
    "no Supabase."
)
MSG_EMAIL = (
    "O serviço de login recusou este e-mail. Se o endereço estiver errado, desative esta "
    "pessoa e convide o endereço certo."
)
MSG_RECUSADO = "O serviço de login recusou o convite (erro {})."
MSG_GRAVACAO = (
    "O convite pode ter saído, mas o cadastro não foi atualizado. Tente de novo; se repetir, "
    "avise quem cuida do banco."
)


@dataclass(frozen=True)
class Pedido:
    """Uma linha de seg.convites_na_fila()."""

    convite_id: int
    email: str
    nome: str | None
    ativo: bool
    entrou: bool


class Fila(Protocol):
    def pendentes(self) -> list[Pedido]: ...

    def concluir(
        self, convite_id: int, id_autenticacao: UUID | None, motivo: str | None
    ) -> None: ...

    def logins_a_apagar(self) -> list[UUID]: ...

    def login_apagado(self, id_autenticacao: UUID) -> None: ...


class FilaPostgres:
    """As filas pelo role carga: só as funções das migrations 29 e 32."""

    def __init__(self, conn: psycopg.Connection[DictRow]) -> None:
        self.conn = conn

    def pendentes(self) -> list[Pedido]:
        linhas = self.conn.execute(
            "select convite_id, email, nome, ativo, entrou from seg.convites_na_fila()"
        ).fetchall()
        return [
            Pedido(
                convite_id=r["convite_id"],
                email=r["email"],
                nome=r["nome"],
                ativo=r["ativo"],
                entrou=r["entrou"],
            )
            for r in linhas
        ]

    def concluir(self, convite_id: int, id_autenticacao: UUID | None, motivo: str | None) -> None:
        self.conn.execute(
            "select seg.concluir_convite(%s::bigint, %s::uuid, %s::text)",
            (convite_id, id_autenticacao, motivo),
        )

    def logins_a_apagar(self) -> list[UUID]:
        linhas = self.conn.execute("select l as id from seg.logins_a_apagar() as l").fetchall()
        return [r["id"] for r in linhas]

    def login_apagado(self, id_autenticacao: UUID) -> None:
        self.conn.execute("select seg.login_apagado(%s::uuid)", (id_autenticacao,))


def motivo_da_recusa(exc: GoTrueErro) -> str:
    """Por que o Supabase Auth recusou, em texto para a Gestão."""
    if exc.status == 429 or (exc.codigo or "").startswith("over_"):
        return MSG_LIMITE
    if exc.codigo == "email_exists" or "already been registered" in str(exc.corpo.get("msg")):
        return MSG_JA_EXISTE
    if exc.codigo in ("email_address_invalid", "email_address_not_authorized"):
        return MSG_EMAIL
    return MSG_RECUSADO.format(", ".join(str(p) for p in (exc.status, exc.codigo) if p))


def enviar(pedido: Pedido, gotrue: GoTrue, front_url: str) -> tuple[UUID | None, str | None]:
    """Manda um convite. Devolve (id no Supabase Auth, None) ou (None, motivo)."""
    if not pedido.ativo:
        return None, MSG_DESATIVADO
    if pedido.entrou:
        return None, MSG_JA_ENTROU
    nome = pedido.nome or pedido.email.split("@")[0]
    try:
        return gotrue.convidar(pedido.email, f"{front_url}/definir-senha", {"nome": nome}), None
    except GoTrueErro as exc:
        return None, motivo_da_recusa(exc)
    except GoTrueIndisponivel:
        return None, MSG_INDISPONIVEL


def apagar_logins(fila: Fila, gotrue: GoTrue) -> tuple[int, int]:
    """Apaga no Supabase Auth os logins de quem foi apagado pela Gestão. Devolve (apagados,
    que ficaram para a próxima rodada). A saída não leva o id."""
    apagados = pendentes = 0
    for id_autenticacao in fila.logins_a_apagar():
        try:
            gotrue.apagar_usuario(id_autenticacao)
        except GoTrueErro as exc:
            # 404: o login já não existe (apagado à mão no painel); é o que se queria
            if exc.status != 404:
                pendentes += 1
                print(f"login não apagado (erro {exc.status}, {exc.codigo})", file=sys.stderr)
                continue
        except GoTrueIndisponivel:
            pendentes += 1
            print("login não apagado (serviço de login fora do ar)", file=sys.stderr)
            continue
        fila.login_apagado(id_autenticacao)
        apagados += 1
    return apagados, pendentes


def processar(fila: Fila, gotrue: GoTrue, front_url: str) -> tuple[int, int, int]:
    """Manda todos os pendentes. Devolve (enviados, não enviados, resultados não gravados)."""
    enviados = falhas = sem_registro = 0
    for pedido in fila.pendentes():
        id_autenticacao, motivo = enviar(pedido, gotrue, front_url)
        try:
            fila.concluir(pedido.convite_id, id_autenticacao, motivo)
        except psycopg.Error as exc:
            # não deixa o pedido na fila: a próxima rodada mandaria o mesmo e-mail de novo
            print(
                f"convite {pedido.convite_id}: resultado não gravado ({type(exc).__name__})",
                file=sys.stderr,
            )
            sem_registro += 1
            try:
                fila.concluir(pedido.convite_id, None, MSG_GRAVACAO)
            except psycopg.Error as outro:
                print(
                    f"convite {pedido.convite_id}: ficou na fila ({type(outro).__name__})",
                    file=sys.stderr,
                )
            continue
        if motivo is None:
            enviados += 1
            print(f"convite {pedido.convite_id}: enviado")
        else:
            falhas += 1
            print(f"convite {pedido.convite_id}: não enviado ({motivo})")
    return enviados, falhas, sem_registro


def main() -> int:
    try:
        config = ConfigConvites()  # valores vêm do ambiente (ou do .env)
    except ValidationError as exc:
        # só os nomes: nunca ecoar os valores (são segredos)
        nomes = sorted({str(e["loc"][0]).upper() for e in exc.errors() if e["loc"]})
        problemas = ", ".join(nomes) or "; ".join(str(e["msg"]) for e in exc.errors())
        print(f"Erro: configuração incompleta ({problemas}).", file=sys.stderr)
        return 1
    try:
        # prepare_threshold=None: funciona também pelo pooler em modo transação
        conn = psycopg.connect(
            config.database_url_carga.get_secret_value(),
            autocommit=True,
            row_factory=dict_row,
            prepare_threshold=None,
        )
    except psycopg.Error as exc:
        # só o tipo: a mensagem do psycopg pode trazer a connection string
        print(f"Erro: não conectou ao banco ({type(exc).__name__}).", file=sys.stderr)
        return 1

    gotrue = GoTrue(config.supabase_url, config.supabase_service_role_key.get_secret_value())
    try:
        with conn:
            fila = FilaPostgres(conn)
            apagados, logins_pendentes = apagar_logins(fila, gotrue)
            enviados, falhas, sem_registro = processar(fila, gotrue, config.front_url)
    except psycopg.Error as exc:
        print(f"Erro: a fila não pôde ser lida ({type(exc).__name__}).", file=sys.stderr)
        return 1
    finally:
        gotrue.close()

    print(f"Logins apagados: {apagados}; ficaram para a próxima rodada: {logins_pendentes}.")
    print(f"Convites: {enviados} enviados, {falhas} não enviados.")
    return 1 if sem_registro or logins_pendentes else 0


if __name__ == "__main__":
    raise SystemExit(main())
