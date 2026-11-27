"""GET /api/telas/estoque (EST-01) contra o banco local: as consultas de vendas, estoque e
cadastro, a janela de 12 meses, o estoque a custo pelo cadastro e o setor.

Alfa (Ana; as duas unidades são Distribuidora no seed; carga de 12/10/2026): o estoque é o do seed
(mart.f_estoque_nilo_maia: P001 com 120 + 15 nas duas empresas, P002 com 40), mais vendas e
cadastro no formato da PJ. Beta (Bruno) não tem unidade com setor: a tela fica negada mesmo
ligada.
"""

from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from tests.conftest import CLIENTE_A, CLIENTE_B, EMPRESA_A1, OID_A, OID_B, auth

pytestmark = pytest.mark.db

ESTOQUE = "/api/telas/estoque"
PERFIL_ANA = "f1000000-0000-4000-8000-00000000000a"
PERFIL_BRUNO = "f1000000-0000-4000-8000-00000000000b"
CAMINHOS = [
    f"dataflow/dbo_{t}_teste_estoque/dbo_{t}_teste_estoque.parquet" for t in ("f_vendas", "d_bitem")
]
COLUNAS_VENDAS = (
    '{"data": "en_dataemissao", "valor": "en_valortotal", "custo": "en_custototal",'
    ' "produto": "en_codigoproduto"}'
)


@pytest.fixture
def estoque_da_ana(admin_conn: psycopg.Connection[tuple[Any, ...]]) -> Iterator[None]:
    admin_conn.execute(
        """
        insert into cfg.fonte_carga
            (cliente_id, tipo, caminho_bucket, ativo, tabela_destino, contem_dado_nominal,
             colunas_modelo)
        values (%(c)s, 'tabela', %(v)s, false, 'f_vendas_pj', false, %(cols)s),
               (%(c)s, 'tabela', %(b)s, false, 'd_bitem_pj', false, '{}')
        """,
        {"c": CLIENTE_A, "v": CAMINHOS[0], "b": CAMINHOS[1], "cols": COLUNAS_VENDAS},
    )
    # 12 meses até 12/10/2026: a venda de 01/09/2025 fica de fora
    admin_conn.execute(
        """
        insert into mart.f_vendas_pj
            (_linha, _cliente_id, "en_dataemissao", "en_codigoproduto", "en_valortotal",
             "en_custototal")
        values (1, %(c)s, '2026-10-01', 'P001', 3600, 2160),
               (2, %(c)s, '2026-05-10', 'P001', 2400, 1440),
               (3, %(c)s, '2025-09-01', 'P001', 99999, 1),
               (4, %(c)s, '2026-10-10', 'P003', 4000, 2000),
               (5, %(c)s, '2026-09-01', 'P002', 3000, 2400)
        """,
        {"c": CLIENTE_A},
    )
    admin_conn.execute(
        """
        insert into mart.d_bitem_pj
            (_linha, _cliente_id, "en_codigoproduto", "en_nome", "en_grupo", "en_secao",
             "en_custounitario")
        values (1, %(c)s, 'P001', 'PICANHA', 'CORTES NOBRES', 'BOVINOS', 30),
               (2, %(c)s, 'P002', 'FRANGO', 'AVES INTEIRAS', 'AVES', 10),
               (3, %(c)s, 'P003', 'ALCATRA', 'CORTES NOBRES', 'BOVINOS', 25)
        """,
        {"c": CLIENTE_A},
    )
    # o Estoque ligado nos dois clientes e liberado aos perfis da Ana e do Bruno
    admin_conn.execute(
        "insert into cfg.cliente_tela (cliente_id, codigo_tela, ligada)"
        " values (%s, 'EST-01', true), (%s, 'EST-01', true)",
        (CLIENTE_A, CLIENTE_B),
    )
    admin_conn.execute(
        "insert into cfg.perfil_tela (perfil_id, codigo_tela, ve_dado_nominal)"
        " values (%s, 'EST-01', false), (%s, 'EST-01', false)",
        (PERFIL_ANA, PERFIL_BRUNO),
    )
    yield
    admin_conn.execute("delete from cfg.perfil_tela where codigo_tela = 'EST-01'")
    admin_conn.execute("delete from cfg.cliente_tela where codigo_tela = 'EST-01'")
    for tabela in ("f_vendas_pj", "d_bitem_pj"):
        admin_conn.execute(
            f"delete from mart.{tabela} where _cliente_id = %s",  # noqa: S608
            (CLIENTE_A,),
        )
    admin_conn.execute(
        "delete from cfg.fonte_carga where cliente_id = %s and caminho_bucket = any(%s)",
        (CLIENTE_A, CAMINHOS),
    )


def _dica(ponto: dict[str, Any]) -> dict[str, Any]:
    return {i["rotulo"]: i["valor"] for i in ponto["dica"]}


@pytest.mark.usefixtures("estoque_da_ana")
def test_estoque_monta_a_bcg_com_os_dados_do_banco(client: TestClient) -> None:
    assert "EST-01" in client.get("/api/sessao", headers=auth(OID_A)).json()["telas"]

    r = client.get(ESTOQUE, headers=auth(OID_A))

    assert r.status_code == 200
    corpo = r.json()
    assert corpo["frescor"].startswith("Estoque de 12/10")
    assert [f["id"] for f in corpo["filtros"]] == ["secao", "classe", "lista"]
    assert [o["id"] for o in corpo["filtros"][0]["opcoes"]] == ["todas", "aves", "bovinos"]
    bcg, acoes, real_ideal, saude, criticos, preso = corpo["cartoes"]
    # o resto do painel (27/11/2026): os 4 KPIs, o aviso e os quatro cartões, pela mesma leitura
    assert [k["rotulo"] for k in corpo["kpis"]] == [
        "Valor em estoque",
        "Acima do ideal",
        "Abaixo do mínimo",
        "Cobertura média",
    ]
    assert corpo["aviso"]["texto"][0]["texto"].startswith("Retrato do estoque de")
    assert (real_ideal["tipo"], saude["tipo"]) == ("real-ideal", "saude-estoque")
    assert (criticos["id"], preso["id"]) == ("criticos", "excesso-parados")
    # o estoque de hoje soma as duas unidades: P001 (135 × 30) e P002 (40 × 10)
    assert corpo["kpis"][0]["valor"]["valor"] == 4050 + 400
    # giros: P001 = 3600 ÷ 12 ÷ (135 × 30) ≈ 0,07; P002 = 2400 ÷ 12 ÷ (40 × 10) = 0,5; P003 sem
    # estoque (o maior). Margens: 40%, 20% e 50%
    assert (bcg["corteX"]["rotulo"], bcg["corteY"]["rotulo"]) == ("mediana 0,5×", "mediana 40%")
    assert bcg["trilha"] == "2 famílias · 3 principais produtos · clique numa bolha para abrir"
    nobres, aves = bcg["pontos"]
    assert (nobres["nome"], aves["nome"]) == ("Cortes nobres", "Aves inteiras")
    picanha, alcatra = (
        next(p for p in nobres["produtos"] if p["nome"] == nome) for nome in ("Picanha", "Alcatra")
    )
    # a venda de 2025 ficou de fora; o estoque é a quantidade das duas empresas × custo
    assert _dica(picanha)["Faturamento 12m"]["valor"] == 6000
    assert _dica(picanha)["Estoque hoje"]["valor"] == 4050
    assert picanha["quadrante"] == "interrogacao"
    assert (alcatra["quadrante"], _dica(alcatra)["Giro"]) == ("estrela", "sem estoque hoje")
    assert aves["quadrante"] == "vaca-leiteira"
    estrela = acoes["itens"][0]
    assert (estrela["rotulo"], estrela["nota"]) == ("Estrela", "1 abaixo do mínimo")

    # o filtro de seção apaga, sem mudar os cortes
    so_aves = client.get(ESTOQUE, params={"secao": "aves"}, headers=auth(OID_A)).json()
    assert so_aves["cartoes"][0]["corteX"] == bcg["corteX"]
    assert [p["apagado"] for p in so_aves["cartoes"][0]["pontos"]] == [True, False]


@pytest.mark.usefixtures("estoque_da_ana")
def test_estoque_negado_para_empresa_sem_o_setor(client: TestClient) -> None:
    # a Beta não tem setor: fora do menu e 403, mesmo ligado e liberado ao perfil
    assert "EST-01" not in client.get("/api/sessao", headers=auth(OID_B)).json()["telas"]
    r = client.get(ESTOQUE, headers=auth(OID_B))
    assert (r.status_code, r.json()) == (403, {"detail": "Acesso negado."})


@pytest.fixture
def alfa_industria_de_outro_setor(
    admin_conn: psycopg.Connection[tuple[Any, ...]],
) -> Iterator[None]:
    admin_conn.execute("update cfg.empresa set setor = 'industria' where id = %s", (EMPRESA_A1,))
    yield
    admin_conn.execute(
        "update cfg.empresa set setor = 'distribuidora' where id = %s", (EMPRESA_A1,)
    )


@pytest.mark.usefixtures("estoque_da_ana", "alfa_industria_de_outro_setor")
def test_estoque_soma_so_as_unidades_da_distribuidora(client: TestClient) -> None:
    escopo = {"setores": ["distribuidora"], "rotulo": "Este painel soma só a Distribuidora"}
    # a Alfa Comércio ainda é Distribuidora: o painel vale, só com ela
    assert "EST-01" in client.get("/api/sessao", headers=auth(OID_A)).json()["telas"]
    r = client.get(ESTOQUE, headers=auth(OID_A))
    assert r.status_code == 200
    assert r.json()["escopo"] == escopo

    # só a unidade da Indústria marcada: o aviso, sem cartões
    so_industria = client.get(ESTOQUE, params={"unidades": EMPRESA_A1}, headers=auth(OID_A))
    assert so_industria.status_code == 200
    corpo = so_industria.json()
    assert (corpo["cartoes"], corpo["escopo"]) == ([], escopo)
    assert corpo["aviso"]["texto"][0]["texto"].startswith("Estoque é um painel da Distribuidora.")
