"""CLI da carga diária: `uv run python -m carga`.

- cria uma linha em mart.carga ('rodando') e uma em mart.carga_fonte por fonte;
- cada fonte roda na sua transação; a falha de uma não impede as outras;
- mart.carga termina 'ok' só se todas as fontes deram certo, senão 'erro';
- imprime um resumo e sai com código != 0 se algo falhar.

Não depende de onde roda (GitHub Actions hoje, Container Apps Job depois): tudo vem
de variáveis de ambiente.
"""

import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from typing import Literal

from pydantic import ValidationError

from carga import lancamento, storage, tabela
from carga.arquivo import Progresso
from carga.config import Settings
from carga.db import Conexao, conectar
from carga.erros import ErroCarga
from carga.fontes import TIPOS_SUPORTADOS, Fonte, fontes_em_conflito, listar_fontes_ativas

logger = logging.getLogger("carga")

Status = Literal["ok", "erro"]


@dataclass
class ResultadoFonte:
    fonte: Fonte
    status: Status
    progresso: Progresso
    mensagem: str | None = None


@dataclass
class ResultadoCarga:
    carga_id: int
    status: Status
    fontes: list[ResultadoFonte] = field(default_factory=list)
    mensagem: str | None = None


def _rodar_fonte(
    conn: Conexao,
    settings: Settings,
    carga_id: int,
    fonte: Fonte,
    em_conflito: set[int],
    baixar: Callable[[str], bytes],
) -> ResultadoFonte:
    linha = conn.execute(
        """
        insert into mart.carga_fonte (carga_id, fonte_id, cliente_id, status)
        values (%s, %s, %s, 'rodando')
        returning id
        """,
        (carga_id, fonte.id, fonte.cliente_id),
    ).fetchone()
    assert linha is not None  # noqa: S101 — insert ... returning sempre devolve a linha
    carga_fonte_id: int = linha[0]

    progresso = Progresso()
    status: Status = "erro"
    mensagem: str | None = None
    try:
        if fonte.id in em_conflito:
            alvo = f"mart.{fonte.tabela_destino}" if fonte.tipo == "tabela" else fonte.tipo
            raise ErroCarga(
                f"mais de uma fonte ativa do cliente {fonte.cliente_id} grava em {alvo}; "
                "deixe só uma ativa em cfg.fonte_carga"
            )
        if fonte.tipo not in TIPOS_SUPORTADOS:
            raise ErroCarga(f"tipo de fonte não suportado: {fonte.tipo}")
        if fonte.tipo == "tabela":
            tabela.carregar(conn, settings, fonte, baixar, progresso)
        else:
            lancamento.carregar(conn, settings, fonte, baixar, progresso)
        status = "ok"
        mensagem = "; ".join(progresso.avisos) or None
    except ErroCarga as exc:
        mensagem = str(exc)
    except Exception as exc:
        logger.exception("Erro inesperado na fonte %s", fonte.id)
        mensagem = f"erro inesperado: {type(exc).__name__}: {exc}"

    conn.execute(
        """
        update mart.carga_fonte
           set status = %s, data_snapshot = %s, linhas_lidas = %s,
               linhas_gravadas = %s, mensagem = %s, fim = now()
         where id = %s
        """,
        (
            status,
            progresso.data_snapshot,
            progresso.linhas_lidas,
            progresso.linhas_gravadas if status == "ok" else None,
            mensagem,
            carga_fonte_id,
        ),
    )
    return ResultadoFonte(fonte=fonte, status=status, progresso=progresso, mensagem=mensagem)


def executar(settings: Settings, baixar: Callable[[str], bytes] | None = None) -> ResultadoCarga:
    """Roda a carga inteira. `baixar` existe para teste; o padrão é o Supabase Storage."""
    baixar = baixar or partial(storage.baixar, settings)

    with conectar(settings) as conn:
        linha = conn.execute(
            "insert into mart.carga (status) values ('rodando') returning id"
        ).fetchone()
        assert linha is not None  # noqa: S101
        resultado = ResultadoCarga(carga_id=linha[0], status="erro")

        try:
            fontes = listar_fontes_ativas(conn)
            em_conflito = fontes_em_conflito(fontes)
            for fonte in fontes:
                resultado.fontes.append(
                    _rodar_fonte(conn, settings, resultado.carga_id, fonte, em_conflito, baixar)
                )

            erros = sum(1 for r in resultado.fontes if r.status == "erro")
            if not fontes:
                resultado.mensagem = "nenhuma fonte ativa em cfg.fonte_carga"
            elif erros:
                resultado.mensagem = f"{erros} de {len(fontes)} fonte(s) com erro"
            else:
                resultado.status = "ok"
                resultado.mensagem = f"{len(fontes)} fonte(s) ok"
        except Exception as exc:
            resultado.mensagem = f"erro inesperado: {type(exc).__name__}: {exc}"
            raise
        finally:
            conn.execute(
                "update mart.carga set status = %s, mensagem = %s, fim = now() where id = %s",
                (resultado.status, resultado.mensagem, resultado.carga_id),
            )
    return resultado


def formatar_resumo(resultado: ResultadoCarga) -> str:
    linhas = [f"Carga {resultado.carga_id}: {resultado.status.upper()} — {resultado.mensagem}"]
    for r in resultado.fontes:
        p = r.progresso
        data = p.data_snapshot.isoformat() if p.data_snapshot else "-"
        cabecalho = (
            f"  {r.status.upper():<4} fonte {r.fonte.id} ({r.fonte.tipo}, "
            f"cliente {r.fonte.cliente_id}) {r.fonte.caminho_bucket}"
        )
        if r.status == "ok":
            linhas.append(
                f"{cabecalho}\n       snapshot {data}: {p.linhas_lidas} linhas lidas, "
                f"{p.linhas_gravadas} gravadas no mart"
            )
        else:
            linhas.append(f"{cabecalho}\n       snapshot {data}: {r.mensagem}")
    return "\n".join(linhas)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        settings = Settings()  # valores vêm do ambiente
    except ValidationError as exc:
        print(f"Configuração inválida:\n{exc}", file=sys.stderr)
        return 2

    try:
        resultado = executar(settings)
    except ErroCarga as exc:
        print(f"Carga não executada: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        logger.exception("Carga interrompida")
        print(f"Carga interrompida: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    print(formatar_resumo(resultado))
    return 0 if resultado.status == "ok" else 1
