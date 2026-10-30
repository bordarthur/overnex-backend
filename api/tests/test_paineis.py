"""GET /api/telas/vendas, /equipe-comercial, /clientes, /resultado, /receber-pagar e /pessoas
contra o banco local.

Dados no formato da PJ Cortes (colunas_modelo com vendedor, linha e quantidade) no cliente
Beta (Bruno; última carga em 11/10/2026).
"""

from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from tests.conftest import CLIENTE_B, EMPRESA_A1, OID_A, OID_B, auth

pytestmark = pytest.mark.db

VENDAS = "/api/telas/vendas"
EQUIPE = "/api/telas/equipe-comercial"
CLIENTES = "/api/telas/clientes"
RESULTADO = "/api/telas/resultado"
RECEBER_PAGAR = "/api/telas/receber-pagar"
PESSOAS = "/api/telas/pessoas"
SESSAO = "/api/sessao"

CAMINHOS = [
    f"dataflow/dbo_{t}_teste_paineis/dbo_{t}_teste_paineis.parquet"
    for t in ("f_vendas", "f_metas", "d_vendedor", "d_bitem", "f_receitas")
]
COLUNAS_VENDAS = (
    '{"data": "en_dataemissao", "valor": "en_valortotal", "custo": "en_custototal",'
    ' "pedido": "en_numpedido", "cliente": "en_codigocliente",'
    ' "produto": "en_codigoproduto", "abertura": "it_grupo",'
    ' "vendedor": "en_codigovendedor", "linha": "it_grupo", "quantidade": "en_quantidade"}'
)
TABELAS = ("f_vendas_pj", "f_metas_comercial_pj", "d_vendedor_pj", "d_bitem_pj", "f_receitas_pj")


@pytest.fixture
def paineis_do_bruno(admin_conn: psycopg.Connection[tuple[Any, ...]]) -> Iterator[None]:
    admin_conn.execute(
        """
        insert into cfg.fonte_carga
            (cliente_id, tipo, caminho_bucket, ativo, tabela_destino, contem_dado_nominal,
             colunas_modelo)
        values (%(c)s, 'tabela', %(v)s, false, 'f_vendas_pj', true, %(cols)s),
               (%(c)s, 'tabela', %(m)s, false, 'f_metas_comercial_pj', false, '{"data": "Data"}'),
               (%(c)s, 'tabela', %(d)s, false, 'd_vendedor_pj', true, '{}'),
               (%(c)s, 'tabela', %(b)s, false, 'd_bitem_pj', false, '{}'),
               (%(c)s, 'tabela', %(r)s, false, 'f_receitas_pj', true, '{}')
        """,
        {
            "c": CLIENTE_B,
            "v": CAMINHOS[0],
            "m": CAMINHOS[1],
            "d": CAMINHOS[2],
            "b": CAMINHOS[3],
            "r": CAMINHOS[4],
            "cols": COLUNAS_VENDAS,
        },
    )
    # outubro (até 09/10): V1 (atacado) 3 pedidos, V2 (varejo) 2; setembro: um pedido de cada
    admin_conn.execute(
        """
        insert into mart.f_vendas_pj
            (_linha, _cliente_id, "en_dataemissao", "en_numpedido", "en_codigoproduto",
             "en_codigocliente", "en_quantidade", "en_valortotal", "en_custototal",
             "en_codigovendedor", "it_grupo")
        values (1, %(c)s, '2026-10-01', 'P1', 'X', 'C1', 10, 600, 400, 'V1', 'BOVINOS'),
               (2, %(c)s, '2026-10-01', 'P1', 'Y', 'C1', 5, 400, 200, 'V1', 'AVES'),
               (3, %(c)s, '2026-10-02', 'P2', 'X', 'C2', 8, 1000, 700, 'V1', 'BOVINOS'),
               (4, %(c)s, '2026-10-05', 'P3', 'Z', 'C3', 2, 300, 150, 'V2', 'AVES'),
               (5, %(c)s, '2026-10-09', 'P4', 'X', 'C4', 4, 700, 500, 'V2', 'BOVINOS'),
               (6, %(c)s, '2026-10-09', 'P5', 'Y', 'C1', 3, 500, 250, 'V1', 'AVES'),
               (7, %(c)s, '2026-09-10', 'P0', 'X', 'C1', 9, 2000, 1500, 'V1', 'BOVINOS'),
               (8, %(c)s, '2026-09-15', 'P9', 'Z', 'C3', 1, 1000, 600, 'V2', 'AVES')
        """,
        {"c": CLIENTE_B},
    )
    admin_conn.execute(
        """
        insert into mart.f_metas_comercial_pj
            (_linha, _cliente_id, "Data", "Valor", "NDeClientes", "en_vendedor")
        values (1, %(c)s, '2026-10-01', 6000, 4, 'V1'),
               (2, %(c)s, '2026-10-01', 4000, 3, 'V2'),
               (3, %(c)s, '2026-09-01', 3000, 2, 'V1'),
               (4, %(c)s, '2026-09-01', 1500, 2, 'V2')
        """,
        {"c": CLIENTE_B},
    )
    admin_conn.execute(
        """
        insert into mart.d_vendedor_pj
            (_linha, _cliente_id, "en_codigovendedor", "en_nome", "Canal", "sp_supervisor")
        values (1, %(c)s, 'V1', 'ANA RIBEIRO', 'ATACADO', 'Paula'),
               (2, %(c)s, 'V2', 'BRUNO LIMA', 'Varejo', 'Marcos')
        """,
        {"c": CLIENTE_B},
    )
    admin_conn.execute(
        """
        insert into mart.d_bitem_pj (_linha, _cliente_id, "en_codigoproduto", "en_nome")
        values (1, %(c)s, 'X', 'CONTRAFILE'), (2, %(c)s, 'Y', 'PEITO DE FRANGO'),
               (3, %(c)s, 'Z', 'COXA')
        """,
        {"c": CLIENTE_B},
    )
    # V2: título de 800 vencido em 30/09 e em aberto
    admin_conn.execute(
        """
        insert into mart.f_receitas_pj
            (_linha, _cliente_id, "en_dataemissao", "en_datavencimento", "en_datapagamento",
             "en_valor", "en_codigovendedor")
        values (1, %(c)s, '2026-09-01', '2026-09-30', null, 800, 'V2'),
               (2, %(c)s, '2026-09-01', '2026-09-20', '2026-09-21', 500, 'V1')
        """,
        {"c": CLIENTE_B},
    )
    # histórico da carga: os meses com venda (lista de /api/sessao) e o faturado por vendedor
    admin_conn.execute(
        """
        insert into mart.resumo_mensal
            (cliente_id, empresa_id, tabela, medida, dimensao, mes, valor, data_snapshot)
        values (%(c)s, null, 'f_vendas_pj', 'faturado', '', '2026-09-01', 3000, '2026-10-11'),
               (%(c)s, null, 'f_vendas_pj', 'faturado', '', '2026-10-01', 3500, '2026-10-11'),
               (%(c)s, null, 'f_vendas_pj', 'faturado_vendedor', 'V1', '2026-09-01', 2000,
                '2026-10-11'),
               (%(c)s, null, 'f_vendas_pj', 'faturado_vendedor', 'V2', '2026-09-01', 1000,
                '2026-10-11')
        """,
        {"c": CLIENTE_B},
    )
    yield
    admin_conn.execute("delete from mart.resumo_mensal where cliente_id = %s", (CLIENTE_B,))
    for tabela in TABELAS:
        admin_conn.execute(
            f"delete from mart.{tabela} where _cliente_id = %s",  # noqa: S608
            (CLIENTE_B,),
        )
    admin_conn.execute(
        "delete from cfg.fonte_carga where cliente_id = %s and caminho_bucket = any(%s)",
        (CLIENTE_B, CAMINHOS),
    )


def test_sessao_lista_as_telas_comerciais(client: TestClient) -> None:
    telas = client.get(SESSAO, headers=auth(OID_B)).json()["telas"]
    assert {"COM-01", "COM-02", "COM-03", "FIN-02", "FIN-03"} <= set(telas)


@pytest.mark.parametrize("rota", [VENDAS, EQUIPE, CLIENTES, RESULTADO, RECEBER_PAGAR, PESSOAS])
def test_sem_token_401(client: TestClient, rota: str) -> None:
    assert client.get(rota).status_code == 401


@pytest.mark.parametrize("rota", [VENDAS, EQUIPE, CLIENTES, RESULTADO, RECEBER_PAGAR])
def test_cliente_sem_vendas_vem_sem_cartoes(client: TestClient, rota: str) -> None:
    r = client.get(rota, headers=auth(OID_A))
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["dadosAte"] == "2026-10-12"
    assert corpo["cartoes"] == []
    assert corpo["filtros"] == []


@pytest.fixture
def com01_bloqueada_para_ana(admin_conn: psycopg.Connection[tuple[Any, ...]]) -> Iterator[None]:
    admin_conn.execute(
        "delete from cfg.perfil_tela "
        "where perfil_id = 'f1000000-0000-4000-8000-00000000000a' and codigo_tela = 'COM-01'"
    )
    yield
    admin_conn.execute(
        "insert into cfg.perfil_tela (perfil_id, codigo_tela, ve_dado_nominal) "
        "values ('f1000000-0000-4000-8000-00000000000a', 'COM-01', false)"
    )


@pytest.mark.usefixtures("com01_bloqueada_para_ana")
def test_tela_fora_do_perfil_403_e_some_da_sessao(client: TestClient) -> None:
    r = client.get(VENDAS, headers=auth(OID_A))
    assert r.status_code == 403
    assert r.json() == {"detail": "Acesso negado."}
    assert "COM-01" not in client.get(SESSAO, headers=auth(OID_A)).json()["telas"]


@pytest.fixture
def equipe_sem_nominal(admin_conn: psycopg.Connection[tuple[Any, ...]]) -> Iterator[None]:
    admin_conn.execute(
        "update cfg.perfil_tela set ve_dado_nominal = false "
        "where perfil_id = 'f1000000-0000-4000-8000-00000000000a' and codigo_tela = 'COM-02'"
    )
    yield
    admin_conn.execute(
        "update cfg.perfil_tela set ve_dado_nominal = true "
        "where perfil_id = 'f1000000-0000-4000-8000-00000000000a' and codigo_tela = 'COM-02'"
    )


@pytest.mark.usefixtures("equipe_sem_nominal")
def test_equipe_sem_dado_nominal_403(
    client: TestClient, admin_conn: psycopg.Connection[tuple[Any, ...]]
) -> None:
    assert client.get(EQUIPE, headers=auth(OID_A)).status_code == 403
    linha = admin_conn.execute(
        """
        select etapa_negada from seg.log_acesso
         where codigo_tela = 'COM-02' order by id desc limit 1
        """
    ).fetchone()
    assert linha == (6,)


def test_empresa_de_outro_cliente_403(client: TestClient) -> None:
    r = client.get(VENDAS, params={"empresa": EMPRESA_A1}, headers=auth(OID_B))
    assert r.status_code == 403


def test_mes_sem_dados_422(client: TestClient) -> None:
    r = client.get(CLIENTES, params={"mes": "2025-01"}, headers=auth(OID_A))
    assert r.status_code == 422
    assert r.json() == {"detail": "Mês sem dados."}


@pytest.fixture
def bruno_sem_carga(admin_conn: psycopg.Connection[tuple[Any, ...]]) -> Iterator[None]:
    ids = [
        r[0]
        for r in admin_conn.execute(
            "update mart.carga_fonte set status = 'erro' "
            "where cliente_id = %s and status = 'ok' returning id",
            (CLIENTE_B,),
        )
    ]
    yield
    admin_conn.execute("update mart.carga_fonte set status = 'ok' where id = any(%s)", (ids,))


@pytest.mark.usefixtures("paineis_do_bruno", "bruno_sem_carga")
def test_antes_da_primeira_carga_vem_vazio(client: TestClient) -> None:
    corpo = client.get(VENDAS, headers=auth(OID_B)).json()
    assert corpo["dadosAte"] is None
    assert corpo["atualizadoEm"] is None
    for campo in ("filtros", "destaques", "kpis", "apoios", "cartoes"):
        assert corpo[campo] == []


@pytest.mark.usefixtures("paineis_do_bruno")
def test_vendas_do_mes(client: TestClient) -> None:
    corpo = client.get(VENDAS, headers=auth(OID_B)).json()

    assert corpo["tela"] == "vendas"
    filtros = {f["id"]: f for f in corpo["filtros"]}
    assert [o["id"] for o in filtros["canal"]["opcoes"]] == ["todos", "atacado", "varejo"]
    assert [o["rotulo"] for o in filtros["linha"]["opcoes"]] == ["Todas", "Aves", "Bovinos"]
    vendido = corpo["destaques"][0]
    assert vendido["rotulo"] == "Vendido até 09/10"
    assert vendido["valor"]["valor"] == 3500
    # outubro/2026: 21 dias úteis (12/10 é feriado); até 09/10, 7
    assert vendido["contexto"] == "7 de 21 dias úteis; 35,0% da meta"
    projecao = corpo["destaques"][1]
    assert projecao["valor"]["valor"] == 10500  # 500 por dia × 21
    apoios = {a["rotulo"]: a["valor"]["valor"] for a in corpo["apoios"]}
    assert apoios["Pedidos"] == 5
    assert apoios["Ticket por pedido"] == 700
    ids = [c["id"] for c in corpo["cartoes"]]
    assert ids[0] == "andamento-mes"
    assert "canais" in ids
    assert "linhas-produto" in ids


@pytest.mark.usefixtures("paineis_do_bruno")
def test_vendas_filtros_mudam_os_numeros(client: TestClient) -> None:
    varejo = client.get(VENDAS, params={"canal": "varejo"}, headers=auth(OID_B)).json()
    assert varejo["destaques"][0]["valor"]["valor"] == 1000
    # meta do varejo: só a do V2
    assert varejo["destaques"][0]["contexto"].endswith("25,0% da meta")

    aves = client.get(VENDAS, params={"linha": "aves"}, headers=auth(OID_B)).json()
    assert aves["destaques"][0]["valor"]["valor"] == 1200
    assert "meta" not in aves["destaques"][0]["contexto"]

    r = client.get(VENDAS, params={"linha": "suinos"}, headers=auth(OID_B))
    assert r.status_code == 422
    assert r.json() == {"detail": "Filtro inválido."}


@pytest.mark.usefixtures("paineis_do_bruno")
def test_vendas_de_mes_fechado(client: TestClient) -> None:
    corpo = client.get(VENDAS, params={"mes": "2026-09"}, headers=auth(OID_B)).json()
    assert [d["rotulo"] for d in corpo["destaques"]] == ["Vendido em setembro", "Meta de setembro"]
    assert corpo["destaques"][0]["valor"]["valor"] == 3000


@pytest.mark.usefixtures("paineis_do_bruno")
def test_equipe_comercial(client: TestClient) -> None:
    corpo = client.get(EQUIPE, headers=auth(OID_B)).json()

    assert [f["id"] for f in corpo["filtros"]] == ["canal", "supervisor"]
    ranking = next(c for c in corpo["cartoes"] if c["id"] == "ranking")
    # V1: 2500 → 7500 de 6000 (125%); V2: 1000 → 3000 de 4000 (75%)
    assert [(i["nome"], i["atingimento"]["valor"]) for i in ranking["itens"]] == [
        ("Ana Ribeiro", 125),
        ("Bruno Lima", 75),
    ]
    assert corpo["destaques"][0]["valor"] == "1 de 2"
    report = next(c for c in corpo["cartoes"] if c["id"] == "report-equipe")
    vencido = [c["rotulo"] for c in report["colunas"]].index("Vencido na carteira")
    bruno = report["linhas"][1]["celulas"]
    assert bruno[vencido]["valor"]["valor"] == 800

    paula = client.get(EQUIPE, params={"supervisor": "paula"}, headers=auth(OID_B)).json()
    ranking = next(c for c in paula["cartoes"] if c["id"] == "ranking")
    assert [i["nome"] for i in ranking["itens"]] == ["Ana Ribeiro"]


@pytest.mark.usefixtures("paineis_do_bruno")
def test_clientes_e_atendimentos(client: TestClient) -> None:
    corpo = client.get(CLIENTES, headers=auth(OID_B)).json()

    assert corpo["filtros"] == []
    assert "aviso" not in corpo
    kpis = {k["rotulo"]: k for k in corpo["kpis"]}
    # 5 pedidos em 7 dias úteis, projetados para 21
    assert kpis["Atendimentos no mês"]["valor"]["valor"] == 15
    assert kpis["Ticket médio por atendimento"]["valor"]["valor"] == 700
    # P1 tem dois produtos: 6 itens em 5 pedidos
    assert kpis["Itens por atendimento"]["valor"]["valor"] == 1.2
    assert client.get(CLIENTES, params={"canal": "x"}, headers=auth(OID_B)).status_code == 200


@pytest.mark.usefixtures("paineis_do_bruno")
def test_dados_do_bruno_nao_vazam_para_a_ana(client: TestClient) -> None:
    assert client.get(VENDAS, headers=auth(OID_A)).json()["cartoes"] == []


def test_resultado_sem_lancamentos_vem_vazio(client: TestClient) -> None:
    r = client.get(RESULTADO, headers=auth(OID_B), params={"comparar": "mes-anterior"})
    assert r.status_code == 200
    corpo = r.json()
    for campo in ("filtros", "destaques", "kpis", "apoios", "cartoes"):
        assert corpo[campo] == []
    assert client.get(RESULTADO, headers=auth(OID_B), params={"visao": "ano"}).status_code == 422


@pytest.mark.usefixtures("paineis_do_bruno")
def test_receber_pagar(client: TestClient) -> None:
    r = client.get(RECEBER_PAGAR, headers=auth(OID_B))

    assert r.status_code == 200
    corpo = r.json()
    vendedor = next(f for f in corpo["filtros"] if f["id"] == "vendedor")
    assert [o["rotulo"] for o in vendedor["opcoes"]] == ["Todos", "Ana Ribeiro", "Bruno Lima"]
    assert corpo["kpis"][0]["rotulo"] == "A receber em 30 dias"
    lados = next(c for c in corpo["cartoes"] if c["id"] == "contas-receber-pagar")["lados"]
    # o título de 800 da V2 venceu em 30/09: em 11/10 está vencido (D+2 em 02/10)
    assert lados[0]["total"] == "em aberto: R$ 800"
    assert lados[0]["kpis"][0]["valor"]["valor"] == 800

    # o título da Ana Ribeiro (V1) foi pago
    corpo = client.get(
        RECEBER_PAGAR, headers=auth(OID_B), params={"vendedor": "ana-ribeiro"}
    ).json()
    assert all(c["id"] != "contas-receber-pagar" for c in corpo["cartoes"])
    assert (
        client.get(RECEBER_PAGAR, headers=auth(OID_B), params={"vendedor": "ninguem"}).status_code
        == 422
    )


# ---------- Pessoas (PES-01) ----------

FOLHA = "dataflow/dbo_f_folha_teste_paineis/dbo_f_folha_teste_paineis.parquet"


@pytest.fixture
def folha_do_bruno(admin_conn: psycopg.Connection[tuple[Any, ...]]) -> Iterator[None]:
    """Folha de agosto e setembro no formato da PJ Cortes; PES-01 só no perfil do Bruno (o
    da Ana, "Financeiro", não libera: o painel é da diretoria e do RH)."""
    admin_conn.execute(
        """
        insert into cfg.fonte_carga
            (cliente_id, tipo, caminho_bucket, ativo, tabela_destino, contem_dado_nominal)
        values (%s, 'tabela', %s, false, 'f_folha_pj_cortes', true)
        """,
        (CLIENTE_B, FOLHA),
    )
    # agosto: CARLOS (Logística) e DIANA (Comercial); setembro: DIANA e EDU (entra)
    admin_conn.execute(
        """
        insert into mart.f_folha_pj_cortes
            (_linha, _cliente_id, "NOME", "CARGO", "SETOR", "Data", "Lancamento", "Valor")
        values (1, %(c)s, 'CARLOS SOUZA', 'MOTORISTA', 'Logística', '2026-08-01', 'SALÁRIO', 2000),
               (2, %(c)s, 'CARLOS SOUZA', 'MOTORISTA', 'Logística', '2026-08-01', 'INSS', 500),
               (3, %(c)s, 'DIANA  LIMA', 'VENDEDOR', 'Comercial', '2026-08-01', 'SALÁRIO', 3000),
               (4, %(c)s, 'Diana Lima', 'VENDEDOR', 'COMERCIAL', '2026-09-01', 'SALÁRIO', 3000),
               (5, %(c)s, 'Diana Lima', 'VENDEDOR', 'COMERCIAL', '2026-09-01', 'ALIMENTAÇÃO',
                400),
               (6, %(c)s, 'EDU RAMOS', 'MOTORISTA', 'LOGISTICA', '2026-09-01', 'SALÁRIO', 2200)
        """,
        {"c": CLIENTE_B},
    )
    admin_conn.execute(
        "insert into cfg.perfil_tela (perfil_id, codigo_tela, ve_dado_nominal) "
        "values ('f1000000-0000-4000-8000-00000000000b', 'PES-01', false)"
    )
    yield
    admin_conn.execute(
        "delete from cfg.perfil_tela "
        "where perfil_id = 'f1000000-0000-4000-8000-00000000000b' and codigo_tela = 'PES-01'"
    )
    admin_conn.execute("delete from mart.f_folha_pj_cortes where _cliente_id = %s", (CLIENTE_B,))
    admin_conn.execute(
        "delete from cfg.fonte_carga where cliente_id = %s and caminho_bucket = %s",
        (CLIENTE_B, FOLHA),
    )


@pytest.mark.usefixtures("paineis_do_bruno", "folha_do_bruno")
def test_pessoas(client: TestClient) -> None:
    r = client.get(PESSOAS, headers=auth(OID_B))

    assert r.status_code == 200
    corpo = r.json()
    assert corpo["tela"] == "pessoas"
    assert corpo["filtros"] == []
    kpis = {k["rotulo"]: k for k in corpo["kpis"]}
    # "DIANA  LIMA" e "Diana Lima" são a mesma pessoa
    assert kpis["Colaboradores"]["valor"]["valor"] == 2
    assert kpis["Folha com encargos"]["valor"]["valor"] == 5600
    # faturado de setembro (3.000) ÷ 2 pessoas
    assert kpis["Faturamento por colaborador"]["valor"]["valor"] == 1500
    folha = next(c for c in corpo["cartoes"] if c["id"] == "folha-por-setor")
    assert [i["rotulo"] for i in folha["itens"]] == ["Comercial", "Logística"]
    # sem folha em outubro (o mês da carga): os números são de setembro
    assert corpo["aviso"]["texto"][1]["texto"].endswith("os números são de setembro de 2026.")
    # nenhum nome sai na resposta
    for nome in ("CARLOS", "DIANA", "Diana", "EDU"):
        assert nome not in r.text


@pytest.mark.usefixtures("folha_do_bruno")
def test_pessoas_fora_do_perfil_403(client: TestClient) -> None:
    r = client.get(PESSOAS, headers=auth(OID_A))
    assert r.status_code == 403
    assert r.json() == {"detail": "Acesso negado."}
    assert "PES-01" not in client.get(SESSAO, headers=auth(OID_A)).json()["telas"]
    assert "PES-01" in client.get(SESSAO, headers=auth(OID_B)).json()["telas"]


@pytest.mark.usefixtures("folha_do_bruno", "bruno_sem_carga")
def test_pessoas_antes_da_primeira_carga_vem_vazio(client: TestClient) -> None:
    corpo = client.get(PESSOAS, headers=auth(OID_B)).json()
    assert (corpo["dadosAte"], corpo["atualizadoEm"]) == (None, None)
    for campo in ("filtros", "destaques", "kpis", "apoios", "cartoes"):
        assert corpo[campo] == []
