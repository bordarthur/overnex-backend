"""Seções da Visão geral em paralelo (_em_paralelo) — sem banco (pool falso, threads reais)."""

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, cast
from uuid import UUID

import pytest

from app.core.cache import memo, por_requisicao
from app.core.db import Sessao
from app.seguranca.acesso import ContextoConsolidado
from app.seguranca.auth import UsuarioToken
from app.telas.visao_geral.contrato import Secao
from app.telas.visao_geral.router import _em_paralelo
from app.telas.visao_geral.vendas import SecaoDoMes

USUARIO = UUID("d1000000-0000-4000-8000-00000000000a")


@dataclass
class ConexaoFalsa:
    nome: str
    consultas: list[tuple[str, Any]] = field(default_factory=list)
    desfeita: bool = False

    def execute(self, sql: str, params: Any = None) -> None:
        self.consultas.append((sql, params))

    def rollback(self) -> None:
        self.desfeita = True


@dataclass
class PoolFalso:
    entregues: list[ConexaoFalsa] = field(default_factory=list)
    em_uso: int = 0
    trava: threading.Lock = field(default_factory=threading.Lock)

    @contextmanager
    def connection(self) -> Iterator[ConexaoFalsa]:
        with self.trava:
            conn = ConexaoFalsa(f"extra-{len(self.entregues)}")
            self.entregues.append(conn)
            self.em_uso += 1
        try:
            yield conn
        finally:
            with self.trava:
                self.em_uso -= 1


def _ctx(pool: PoolFalso | None) -> ContextoConsolidado:
    sessao = Sessao(
        conn=ConexaoFalsa("principal"),  # type: ignore[arg-type]
        usuario=UsuarioToken(id_autenticacao=UUID(int=1)),
        pool=pool,  # type: ignore[arg-type]
    )
    return ContextoConsolidado(
        sessao=sessao,
        usuario_id=USUARIO,
        cliente_id=UUID(int=2),
        empresa_id=None,
        empresas=(UUID(int=3),),
        papel="gestor",
        codigo_tela="VG-01",
        ve_dado_nominal=False,
    )


def _marca(nome: str) -> list[SecaoDoMes]:
    """Resultado de mentira: o nome da conexão vai no lugar da seção."""
    return cast(list[SecaoDoMes], [nome])


def _nomes(resultados: list[list[SecaoDoMes]]) -> list[list[str]]:
    return cast(list[list[str]], resultados)


def test_ramos_rodam_ao_mesmo_tempo_cada_um_na_sua_conexao() -> None:
    pool = PoolFalso()
    ctx = _ctx(pool)
    # os três só passam da barreira se estiverem rodando juntos
    barreira = threading.Barrier(3, timeout=5)

    def ramo(c: ContextoConsolidado) -> list[SecaoDoMes]:
        barreira.wait()
        return _marca(c.sessao.conn.nome)  # type: ignore[attr-defined]

    resultados = _nomes(_em_paralelo(ctx, [ramo, ramo, ramo]))

    # o primeiro na conexão da requisição; os outros, um em cada conexão extra (na ordem)
    assert resultados[0] == ["principal"]
    assert sorted(r[0] for r in resultados[1:]) == ["extra-0", "extra-1"]
    for conn in pool.entregues:
        # o mesmo usuário (RLS) e nada fica gravado
        assert conn.consultas == [
            ("select set_config('app.usuario_id', %s, true)", (str(USUARIO),))
        ]
        assert conn.desfeita
    assert pool.em_uso == 0


def test_cache_da_requisicao_vale_nas_threads() -> None:
    pool = PoolFalso()
    calculos: list[str] = []

    def ramo(c: ContextoConsolidado) -> list[SecaoDoMes]:
        def calcular() -> str:
            calculos.append(threading.current_thread().name)
            return "fontes"

        return _marca(memo("fontes", calcular))

    with por_requisicao():
        memo("fontes", lambda: "fontes")  # já lido antes dos ramos
        resultados = _nomes(_em_paralelo(_ctx(pool), [ramo, ramo, ramo]))

    assert resultados == [["fontes"]] * 3
    assert calculos == []  # nenhum ramo leu de novo


def test_erro_num_ramo_sobe_e_devolve_as_conexoes() -> None:
    pool = PoolFalso()

    def quebra(c: ContextoConsolidado) -> list[SecaoDoMes]:
        raise ValueError("regra quebrou")

    with pytest.raises(ValueError, match="regra quebrou"):
        _em_paralelo(_ctx(pool), [lambda c: _marca("ok"), quebra, lambda c: _marca("ok")])
    assert pool.em_uso == 0
    assert all(c.desfeita for c in pool.entregues)


def test_sem_pool_roda_em_sequencia() -> None:
    ordem: list[int] = []

    def ramo(n: int) -> Any:
        def f(c: ContextoConsolidado) -> list[SecaoDoMes]:
            ordem.append(n)
            return _marca(c.sessao.conn.nome)  # type: ignore[attr-defined]

        return f

    assert _nomes(_em_paralelo(_ctx(None), [ramo(1), ramo(2)])) == [["principal"], ["principal"]]
    assert ordem == [1, 2]


def test_secao_de_verdade_passa_intacta() -> None:
    from datetime import date

    secao = SecaoDoMes(secao=cast(Secao, object()), mes=date(2026, 9, 1))
    assert _em_paralelo(_ctx(PoolFalso()), [lambda c: [secao], lambda c: []]) == [[secao], []]
