"""Cache que espera quem já está montando, consultas numa ida só, pré-montagem das telas e as
idas ao banco de uma tela em cache (begin, pré-leitura e teste do pool) — sem banco."""

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

import pytest
from psycopg.pq import TransactionStatus

from app.core import db
from app.core.cache import CacheComPrazo
from app.core.config import get_settings
from app.core.db import Sessao, em_uma_ida
from app.core.erros import AcessoNegado
from app.seguranca.acesso import SQL_FATOS, verificar_acesso_consolidado
from app.seguranca.auth import UsuarioToken
from app.telas import preleitura, preparo
from app.telas.paineis import base
from app.telas.preleitura import versao_do_cadastro
from app.telas.visao_geral.vendas import TabelaDoCliente

# ---------- CacheComPrazo.obter_ou_montar ----------


def test_duas_threads_da_mesma_chave_montam_uma_vez() -> None:
    cache: CacheComPrazo[str] = CacheComPrazo(maximo=10)
    comecou, pode_terminar = threading.Event(), threading.Event()
    montagens: list[str] = []

    def montar() -> str:
        montagens.append(threading.current_thread().name)
        comecou.set()
        pode_terminar.wait(5)
        return "tela"

    resultados: list[str] = []
    primeira = threading.Thread(
        target=lambda: resultados.append(cache.obter_ou_montar("k", montar, 60))
    )
    primeira.start()
    comecou.wait(5)
    # a segunda chega com a primeira ainda montando: espera por ela
    segunda = threading.Thread(
        target=lambda: resultados.append(cache.obter_ou_montar("k", montar, 60))
    )
    segunda.start()
    pode_terminar.set()
    primeira.join(5)
    segunda.join(5)

    assert resultados == ["tela", "tela"]
    assert len(montagens) == 1
    # e fica guardada
    assert cache.obter_ou_montar("k", lambda: "outra", 60) == "tela"


def test_erro_de_quem_monta_vale_para_quem_espera_e_nao_fica_guardado() -> None:
    cache: CacheComPrazo[str] = CacheComPrazo(maximo=10)
    with pytest.raises(ValueError, match="quebrou"):
        cache.obter_ou_montar("k", lambda: (_ for _ in ()).throw(ValueError("quebrou")), 60)
    assert cache.obter_ou_montar("k", lambda: "depois", 60) == "depois"


def test_cache_desligado_monta_sempre() -> None:
    cache: CacheComPrazo[int] = CacheComPrazo(maximo=10)
    contas = iter(range(10))
    assert cache.obter_ou_montar("k", lambda: next(contas), 0) == 0
    assert cache.obter_ou_montar("k", lambda: next(contas), 0) == 1


# ---------- em_uma_ida ----------


class Cursor:
    def __init__(self, linhas: list[Any]) -> None:
        self.linhas = linhas

    def fetchall(self) -> list[Any]:
        return self.linhas


class ConexaoPipeline:
    def __init__(self) -> None:
        self.em_pipeline = False
        self.consultas: list[tuple[Any, Any, bool]] = []

    @contextmanager
    def pipeline(self) -> Iterator[None]:
        self.em_pipeline = True
        yield
        self.em_pipeline = False

    def execute(self, q: Any, p: Any) -> Cursor:
        self.consultas.append((q, p, self.em_pipeline))
        return Cursor([{"q": q}])


def test_em_uma_ida_manda_tudo_no_pipeline_e_devolve_por_nome() -> None:
    conn = ConexaoPipeline()
    linhas = em_uma_ida(conn, {"a": ("select 1", None), "b": ("select 2", (1,))})  # type: ignore[arg-type]

    assert linhas == {"a": [{"q": "select 1"}], "b": [{"q": "select 2"}]}
    assert [c[2] for c in conn.consultas] == [True, True]
    assert em_uma_ida(conn, {}) == {}  # type: ignore[arg-type]


# ---------- colunas da Equipe ----------


def test_papeis_da_pj_valem_so_se_a_coluna_existe() -> None:
    pj = TabelaDoCliente(
        "f_vendas_pj",
        dict(base.COLUNAS_VENDAS_PAINEL),
        existentes=frozenset({"en_codigovendedor", "it_grupo"}),
    )
    padrao = TabelaDoCliente("f_vendas_nilo_maia", dict(base.COLUNAS_VENDAS_PAINEL))
    assert base.tem_papel([pj], "vendedor")
    assert base.tem_papel([pj], "linha")
    assert not base.tem_papel([pj], "quantidade")
    assert not base.tem_papel([padrao], "vendedor")


# ---------- pré-montagem ----------

USUARIO = UsuarioToken(id_autenticacao=UUID(int=7))


class PoolFalso:
    def __init__(self) -> None:
        self.desfeitas = 0

    @contextmanager
    def connection(self) -> Iterator[Any]:
        pool = self

        class Conn:
            def rollback(self) -> None:
                pool.desfeitas += 1

        yield Conn()


@pytest.fixture
def com_cache() -> Iterator[Any]:
    preparo._agendados.limpar()
    yield get_settings().model_copy(update={"visao_geral_cache_segundos": 900})
    preparo._agendados.limpar()


def test_agenda_so_as_telas_liberadas_e_uma_vez_por_usuario(
    com_cache: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    enviados: list[Any] = []
    monkeypatch.setattr(preparo._fila, "submit", lambda *a: enviados.append(a))

    agendadas = preparo.agendar(PoolFalso(), USUARIO, ["FIN-01", "COM-03", "VG-01"], com_cache)  # type: ignore[arg-type]

    # na ordem de TELAS (a Visão geral primeiro), sem o que não é tela pré-montável
    assert agendadas == ["VG-01", "COM-03"]
    assert len(enviados) == 1
    assert preparo.agendar(PoolFalso(), USUARIO, ["VG-01"], com_cache) == []  # type: ignore[arg-type]


def test_agenda_de_novo_quando_a_pessoa_troca_de_cliente(
    com_cache: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    enviados: list[Any] = []
    monkeypatch.setattr(preparo._fila, "submit", lambda *a: enviados.append(a))
    pool = PoolFalso()

    assert preparo.agendar(pool, USUARIO, ["VG-01"], com_cache, "cliente-a") == ["VG-01"]  # type: ignore[arg-type]
    assert preparo.agendar(pool, USUARIO, ["VG-01"], com_cache, "cliente-a") == []  # type: ignore[arg-type]
    assert preparo.agendar(pool, USUARIO, ["VG-01"], com_cache, "cliente-b") == ["VG-01"]  # type: ignore[arg-type]
    # o slug vai junto, para a pré-montagem escolher o mesmo cliente
    assert [e[-1] for e in enviados] == ["cliente-a", "cliente-b"]


@pytest.mark.parametrize(("prazo", "intervalo"), [(3600, 450), (600, 300)])
def test_pre_monta_de_novo_a_cada_metade_do_prazo_e_no_maximo_a_cada_7_min_e_meio(
    com_cache: Any, monkeypatch: pytest.MonkeyPatch, prazo: int, intervalo: int
) -> None:
    guardados: list[float] = []
    monkeypatch.setattr(preparo._fila, "submit", lambda *a: None)
    monkeypatch.setattr(preparo._agendados, "guardar", lambda c, v, s: guardados.append(s))
    settings = com_cache.model_copy(update={"visao_geral_cache_segundos": prazo})

    preparo.agendar(PoolFalso(), USUARIO, ["VG-01"], settings)  # type: ignore[arg-type]

    assert guardados == [intervalo]


def test_sem_cache_ou_sem_pool_nao_agenda(com_cache: Any) -> None:
    sem_cache = com_cache.model_copy(update={"visao_geral_cache_segundos": 0})
    assert preparo.agendar(PoolFalso(), USUARIO, ["VG-01"], sem_cache) == []  # type: ignore[arg-type]
    assert preparo.agendar(None, USUARIO, ["VG-01"], com_cache) == []


def test_preparar_monta_cada_tela_sem_gravar_e_segue_depois_de_negado_ou_erro(
    com_cache: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    montadas: list[str] = []

    def verificar(
        sessao: Any,
        codigo: str,
        empresa: Any,
        *,
        exige_dado_nominal: bool,
        cliente: str | None,
        junto: Any,
    ) -> Any:
        assert cliente == "cliente-a"
        # a pré-leitura da tela vai na mesma ida ao banco da checagem
        assert junto == {"carga": ("consulta", None)}
        if codigo == "COM-02":
            assert exige_dado_nominal
            raise AcessoNegado(6, "sem dado nominal")
        return codigo

    def responder(nome: str, falha: bool = False) -> Any:
        def f(ctx: Any, settings: Any) -> None:
            if falha:
                raise RuntimeError("regra quebrou")
            montadas.append(nome)

        return f

    monkeypatch.setattr(preparo, "verificar_acesso_consolidado", verificar)
    monkeypatch.setattr(preparo, "consultas_adiantadas", lambda conn: {"carga": ("consulta", None)})
    telas = [
        preparo.Tela("VG-01", False, responder("VG-01", falha=True)),
        preparo.Tela("COM-02", True, responder("COM-02")),
        preparo.Tela("COM-03", False, responder("COM-03")),
    ]
    pool = PoolFalso()
    preparo.preparar(pool, USUARIO, telas, com_cache, "cliente-a")  # type: ignore[arg-type]

    assert montadas == ["COM-03"]
    # toda conexão termina desfeita: nada gravado (nem o log de acesso)
    assert pool.desfeitas == 3


def test_premontagem_inclui_os_paineis_financeiros() -> None:
    telas = {t.codigo: t for t in preparo.TELAS}
    assert telas["FIN-02"].nominal is False
    # Receber e pagar mostra clientes, fornecedores e vendedores
    assert telas["FIN-03"].nominal is True
    assert next(iter(telas)) == "VG-01"


# ---------- versão do cadastro (chave do cache) ----------


def _cadastro() -> dict[str, list[dict[str, Any]]]:
    return {
        "cliente": [{"nome": "PJ Cortes", "logo_url": None}],
        "fontes": [{"tabela_destino": "f_vendas_pj", "colunas_modelo": {}, "existentes": ["a"]}],
        "feriados": [{"data": date(2026, 11, 20)}],
        "parametros": [
            {
                "chave": "orcamento.cenario",
                "valor": {"cenario": "Meta"},
                "vigente_de": date(2026, 1, 1),
                "vigente_ate": None,
            }
        ],
        "plano": [{"id_descritivo": "100.01.01", "fator": 1}],
        "conta_dre": [{"versao": "abc"}],
        "carga": [{"data_snapshot": date(2026, 11, 3)}],
    }


def test_versao_do_cadastro_muda_so_com_o_cadastro() -> None:
    base_ = versao_do_cadastro(_cadastro())
    assert versao_do_cadastro(_cadastro()) == base_

    def mudado(nome: str, linhas: list[dict[str, Any]]) -> str:
        return versao_do_cadastro({**_cadastro(), nome: linhas})

    # a carga tem chave própria; aqui não conta
    assert mudado("carga", []) == base_
    assert mudado("feriados", []) != base_
    assert mudado("conta_dre", [{"versao": "def"}]) != base_
    outro = _cadastro()["parametros"]
    outro[0]["valor"] = {"cenario": "Base"}
    assert mudado("parametros", outro) != base_
    # sem a tabela de uma migration, a consulta fica de fora: vale como vazia
    assert versao_do_cadastro({k: v for k, v in _cadastro().items() if k != "plano"}) == mudado(
        "plano", []
    )


# ---------- idas ao banco da tela em cache: begin, pré-leitura e teste do pool ----------


class Info:
    def __init__(self, status: TransactionStatus) -> None:
        self.transaction_status = status


class ConexaoDoPool:
    """Conexão em autocommit, como as do pool: guarda o que foi mandado e em que pipeline."""

    def __init__(
        self, respostas: dict[str, list[Any]] | None = None, *, autocommit: bool = True
    ) -> None:
        self.autocommit = autocommit
        self.info = Info(TransactionStatus.IDLE)
        self.respostas = respostas or {}
        self.pipelines = 0
        # (consulta, parâmetros, número do pipeline; 0 = fora de pipeline)
        self.mandadas: list[tuple[Any, Any, int]] = []
        self._em_pipeline = False

    @contextmanager
    def pipeline(self) -> Iterator[None]:
        self.pipelines += 1
        self._em_pipeline = True
        yield
        self._em_pipeline = False

    def execute(self, q: Any, p: Any = None) -> Cursor:
        self.mandadas.append((q, p, self.pipelines if self._em_pipeline else 0))
        if q == "begin":
            self.info = Info(TransactionStatus.INTRANS)
        for trecho, linhas in self.respostas.items():
            if trecho in str(q):
                return Cursor(linhas)
        return Cursor([])


def test_em_uma_ida_abre_a_transacao_na_mesma_ida() -> None:
    conn = ConexaoDoPool()

    em_uma_ida(conn, {"a": ("select 1", None)})  # type: ignore[arg-type]
    # já com transação, não abre outra
    em_uma_ida(conn, {"b": ("select 2", None)})  # type: ignore[arg-type]

    assert conn.mandadas == [("begin", None, 1), ("select 1", None, 1), ("select 2", None, 2)]


def test_em_uma_ida_so_le_as_respostas_depois_de_fechar_o_pipeline() -> None:
    """Um fetch dentro do pipeline pede as respostas antes da hora: seriam duas idas."""
    conn = ConexaoDoPool()
    dentro: list[bool] = []

    class CursorQueAvisa(Cursor):
        def fetchall(self) -> list[Any]:
            dentro.append(conn._em_pipeline)
            return self.linhas

    conn.execute = lambda q, p=None: CursorQueAvisa([])  # type: ignore[method-assign]

    em_uma_ida(conn, {"a": ("select 1", None), "b": ("select 2", None)})  # type: ignore[arg-type]

    assert dentro == [False, False]


def test_em_uma_ida_nao_manda_begin_em_conexao_que_abre_sozinha() -> None:
    conn = ConexaoDoPool(autocommit=False)
    em_uma_ida(conn, {"a": ("select 1", None)})  # type: ignore[arg-type]
    assert [m[0] for m in conn.mandadas] == ["select 1"]


FATOS = {
    "usuario_id": UUID(int=1),
    "ativo": True,
    "vinculos": [{"cliente_id": str(UUID(int=2)), "papel": "gestor"}],
    "perfis_tela": {str(UUID(int=2)): False},
    "empresas": [{"cliente_id": str(UUID(int=2)), "empresa_id": str(UUID(int=3))}],
    "perfil": "cliente_principal",
    "cliente_id": UUID(int=2),
}


def test_checagem_de_acesso_leva_o_begin_e_a_pre_leitura_na_mesma_ida() -> None:
    conn = ConexaoDoPool({"seg.contexto_acesso": [FATOS], "carga_fonte": [{"fim": "x"}]})
    sessao = Sessao(conn=conn, usuario=USUARIO)  # type: ignore[arg-type]

    ctx = verificar_acesso_consolidado(
        sessao, "VG-01", None, junto={"carga": ("select fim from mart.carga_fonte", None)}
    )

    assert ctx.cliente_id == UUID(int=2)
    # uma ida só, nesta ordem: a função define o cliente escolhido antes das outras consultas
    assert [(m[0], m[2]) for m in conn.mandadas] == [
        ("begin", 1),
        (SQL_FATOS, 1),
        ("select fim from mart.carga_fonte", 1),
    ]
    assert conn.mandadas[1][1] == (USUARIO.id_autenticacao, "VG-01", None)
    # as linhas ficam para a rota; os fatos, não
    assert sessao.adiantadas == {"carga": [{"fim": "x"}]}


def test_checagem_sem_nada_junto_tambem_abre_a_transacao_na_mesma_ida() -> None:
    conn = ConexaoDoPool({"seg.contexto_acesso": [FATOS]})
    sessao = Sessao(conn=conn, usuario=USUARIO)  # type: ignore[arg-type]

    verificar_acesso_consolidado(sessao, "VG-01", None)

    assert [(m[0], m[2]) for m in conn.mandadas] == [("begin", 1), (SQL_FATOS, 1)]
    assert sessao.adiantadas == {}


def test_consultas_adiantadas_usam_o_cliente_escolhido_da_transacao(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(preleitura, "existe", lambda conn, nome: True)
    cliente = UUID(int=2)

    diretas = preleitura._consultas(None, cliente, True)  # type: ignore[arg-type]
    adiantadas = preleitura.consultas_adiantadas(None, com_meses=True)  # type: ignore[arg-type]

    assert list(adiantadas) == list(diretas)
    assert "meses" in adiantadas
    assert "meses" not in preleitura.consultas_adiantadas(None)  # type: ignore[arg-type]
    for nome, (consulta, parametros) in diretas.items():
        adiantada, sem_parametros = adiantadas[nome]
        assert sem_parametros is None
        assert "%" not in str(adiantada)
        if parametros is None:  # o plano de contas é o mesmo para todos
            assert adiantada == consulta
            continue
        # o cliente aparece uma vez só, e é ele que troca
        assert parametros == (cliente,)
        assert str(consulta).count("%s") == 1
        assert adiantada == str(consulta).replace("%s", "(select seg.cliente_atual())")


def test_sem_o_resumo_mensal_os_meses_pedidos_vem_vazios(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(preleitura, "existe", lambda conn, nome: nome != "mart.resumo_mensal")
    consultas = preleitura.consultas_adiantadas(None, com_meses=True)  # type: ignore[arg-type]
    assert consultas["meses"] == (preleitura.SQL_SEM_MESES, None)


LINHAS_INICIO: dict[str, list[Any]] = {
    "carga": [{"data_snapshot": date(2026, 11, 3), "fim": datetime(2026, 11, 3, 12, tzinfo=UTC)}],
    "cliente": [{"nome": "PJ Cortes", "logo_url": None}],
    "fontes": [],
}


def test_preler_usa_as_linhas_que_vieram_com_a_checagem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(preleitura, "existe", lambda conn, nome: False)
    conn = ConexaoDoPool()
    sessao = Sessao(conn=conn, usuario=USUARIO)  # type: ignore[arg-type]
    sessao.adiantadas.update(LINHAS_INICIO)

    inicio = preleitura.preler(sessao, UUID(int=2))

    assert conn.mandadas == []  # nenhuma ida ao banco
    assert inicio.cliente.nome == "PJ Cortes"
    assert inicio.carga is not None
    assert inicio.carga.data_snapshot == date(2026, 11, 3)
    assert inicio.meses is None
    assert inicio.versao == versao_do_cadastro(LINHAS_INICIO)


def test_preler_vai_ao_banco_sem_as_linhas_ou_sem_os_meses_pedidos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(preleitura, "existe", lambda conn, nome: False)
    respostas = {
        "mart.carga_fonte": LINHAS_INICIO["carga"],
        "cfg.cliente": LINHAS_INICIO["cliente"],
    }

    # nada veio com a checagem
    conn = ConexaoDoPool(respostas)
    sessao = Sessao(conn=conn, usuario=USUARIO)  # type: ignore[arg-type]
    assert preleitura.preler(sessao, UUID(int=2)).cliente.nome == "PJ Cortes"
    assert conn.pipelines == 1

    # veio sem os meses, e a rota pede os meses
    conn = ConexaoDoPool(respostas)
    sessao = Sessao(conn=conn, usuario=USUARIO)  # type: ignore[arg-type]
    sessao.adiantadas.update(LINHAS_INICIO)
    inicio = preleitura.preler(sessao, UUID(int=2), com_meses=True)
    assert conn.pipelines == 1
    assert inicio.meses is not None


class ConexaoQualquer:
    """Só para servir de chave (o teste do pool guarda a hora da devolução por conexão)."""


def test_conexao_usada_ha_pouco_sai_do_pool_sem_teste(monkeypatch: pytest.MonkeyPatch) -> None:
    agora = [1000.0]
    testadas: list[Any] = []
    monkeypatch.setattr("app.core.db.time.monotonic", lambda: agora[0])
    monkeypatch.setattr(
        "psycopg_pool.ConnectionPool.check_connection", staticmethod(testadas.append)
    )
    checar = db.checar_se_parada(60)
    conn: Any = ConexaoQualquer()

    checar(conn)  # nova: nunca voltou ao pool
    assert testadas == [conn]

    db._marcar_devolucao(conn)
    agora[0] += 59
    checar(conn)  # usada há pouco
    assert testadas == [conn]

    agora[0] += 1
    checar(conn)  # parada há 60 s
    assert testadas == [conn, conn]


def test_com_zero_o_pool_testa_sempre(monkeypatch: pytest.MonkeyPatch) -> None:
    testadas: list[Any] = []
    monkeypatch.setattr(
        "psycopg_pool.ConnectionPool.check_connection", staticmethod(testadas.append)
    )
    conn: Any = ConexaoQualquer()
    db._marcar_devolucao(conn)

    db.checar_se_parada(0)(conn)

    assert testadas == [conn]


def test_pool_fica_em_autocommit_com_o_teste_so_da_parada() -> None:
    pool = db.criar_pool(get_settings())  # fechado: não conecta
    kwargs = pool.kwargs
    assert isinstance(kwargs, dict)
    assert kwargs["autocommit"] is True
    assert pool._reset is db._marcar_devolucao
