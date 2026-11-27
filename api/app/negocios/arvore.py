"""A árvore da bandeja "Negócios": cliente › setor › grupo › unidade (migration 35).

A unidade é o cfg.empresa (Lupo 1), com o setor (cfg.setor), o grupo (texto livre, Lupo) e o
segmento dela. A árvore só tem as unidades liberadas à pessoa no cliente escolhido.

Ordem: setor pela ordem de cfg.setor (unidade sem setor no fim, em "Sem setor"), grupo pelo
nome (sem grupo no fim do setor, em "Sem grupo") e unidade pelo nome, com ordem natural
("Lupo 2" antes de "Lupo 10"), sem diferença de acento nem de maiúsculas.

`negocios` vem null quando as unidades formam um negócio só (um par setor + grupo, contando
null como valor): aí o front mostra o chip "Unidade" no lugar da bandeja.
"""

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.negocios.contrato import GrupoNegocio, NegociosSessao, SetorNegocio
from app.seguranca.setores import ROTULO_SEM_SETOR, SEM_SETOR, setores_da_tela
from app.telas.moldura import rotulo_mes

SEM_GRUPO = "sem-grupo"
ROTULO_SEM_GRUPO = "Sem grupo"
MSG_SEM_VALORES = "Sem faturado para mostrar."

# unidades liberadas no cliente escolhido (o RLS de cfg.empresa recorta; para o admin, todas)
SQL_UNIDADES = """
    select e.id, e.nome, e.setor, e.grupo
      from cfg.empresa e
     where e.cliente_id = %s
"""
SQL_SETORES = "select id, rotulo, ordem from cfg.setor order by ordem, rotulo"


@dataclass(frozen=True)
class Unidade:
    id: UUID
    nome: str
    # id de cfg.setor; None = sem setor
    setor: str | None
    # None = sem grupo
    grupo: str | None


def _limpo(texto: str | None) -> str | None:
    """Sem espaços sobrando; vazio = None."""
    if texto is None:
        return None
    return " ".join(texto.split()) or None


def unidade_da_linha(r: Mapping[str, Any]) -> Unidade:
    return Unidade(id=r["id"], nome=r["nome"], setor=r["setor"], grupo=_limpo(r["grupo"]))


def natural(texto: str) -> tuple[tuple[int, int | str], ...]:
    """Chave de ordem natural: números pelo valor ("Lupo 2" < "Lupo 10"), sem acento nem
    maiúsculas."""
    sem_acento = "".join(
        c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn"
    ).casefold()
    return tuple(
        (0, int(p)) if p.isdigit() else (1, p) for p in re.split(r"(\d+)", sem_acento) if p
    )


def ordenar(unidades: Iterable[Unidade], ordem_setor: Mapping[str, int]) -> list[Unidade]:
    """Na ordem da árvore: setor (cfg.setor; sem setor no fim), grupo e nome."""

    def chave(u: Unidade) -> tuple[Any, ...]:
        return (
            u.setor is None,
            ordem_setor.get(u.setor, len(ordem_setor)) if u.setor else 0,
            u.setor or "",
            u.grupo is None,
            natural(u.grupo or ""),
            natural(u.nome),
            str(u.id),
        )

    return sorted(unidades, key=chave)


def id_setor(setor: str | None) -> str:
    return setor or SEM_SETOR


def id_grupo(setor: str | None, grupo: str | None) -> str:
    """ "varejo/Lupo"; sem grupo, "varejo/sem-grupo"; sem setor, "sem-setor/…"."""
    return f"{id_setor(setor)}/{grupo or SEM_GRUPO}"


def um_negocio_so(unidades: Iterable[Unidade]) -> bool:
    return len({(u.setor, u.grupo) for u in unidades}) <= 1


def setores_negocio(unidades: Sequence[Unidade], rotulos: Mapping[str, str]) -> list[SetorNegocio]:
    """Setores e grupos das `unidades` (já em ordem: `ordenar`), cada unidade em um grupo."""
    arvore: dict[str, tuple[str, dict[str, tuple[str, list[str]]]]] = {}
    for u in unidades:
        rotulo_setor = rotulos.get(u.setor, u.setor) if u.setor else ROTULO_SEM_SETOR
        _, grupos = arvore.setdefault(id_setor(u.setor), (rotulo_setor, {}))
        _, ids = grupos.setdefault(id_grupo(u.setor, u.grupo), (u.grupo or ROTULO_SEM_GRUPO, []))
        ids.append(str(u.id))
    return [
        SetorNegocio(
            id=setor,
            rotulo=rotulo,
            grupos=[GrupoNegocio(id=g, rotulo=r, unidades=ids) for g, (r, ids) in grupos.items()],
        )
        for setor, (rotulo, grupos) in arvore.items()
    ]


def telas_por_setor(telas: Iterable[str]) -> dict[str, list[str]]:
    """Das telas liberadas, as de um setor: código → setores."""
    saida: dict[str, list[str]] = {}
    for codigo in telas:
        setores = setores_da_tela(codigo)
        if setores is not None:
            saida[codigo] = sorted(setores)
    return saida


def negocios(
    unidades: Sequence[Unidade], rotulos: Mapping[str, str], telas: Iterable[str]
) -> NegociosSessao | None:
    """`negocios` de GET /api/sessao; None com um negócio só (ou nenhuma unidade)."""
    if um_negocio_so(unidades):
        return None
    return NegociosSessao(
        setores=setores_negocio(unidades, rotulos), telas_por_setor=telas_por_setor(telas)
    )


@dataclass(frozen=True)
class Totais:
    setores: dict[str, Decimal]
    grupos: dict[str, Decimal]
    unidades: dict[str, Decimal]


def totais(setores: Sequence[SetorNegocio], por_unidade: Mapping[UUID, Decimal]) -> Totais:
    """O valor de cada unidade e as somas dos grupos e setores inteiros (unidade sem valor
    conta zero)."""
    t = Totais(setores={}, grupos={}, unidades={})
    for s in setores:
        t.setores[s.id] = Decimal(0)
        for g in s.grupos:
            t.grupos[g.id] = Decimal(0)
            for u in g.unidades:
                valor = por_unidade.get(UUID(u), Decimal(0))
                t.unidades[u] = valor
                t.grupos[g.id] += valor
                t.setores[s.id] += valor
    return t


def rotulo_valores(mes: date, dados_ate: date | None) -> str:
    """ "Valores: faturado de 1º a 12/10" no mês dos dados mais recentes (o da última
    carga); "Valores: faturado em setembro de 2026" num mês fechado."""
    if dados_ate is not None and dados_ate.replace(day=1) == mes:
        if dados_ate.day == 1:
            return f"Valores: faturado em {dados_ate:%d/%m}"
        return f"Valores: faturado de 1º a {dados_ate:%d/%m}"
    return f"Valores: faturado em {rotulo_mes(mes).lower()}"
