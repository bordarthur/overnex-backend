"""GET /api/telas/visao-geral e GET /api/sessao contra o banco local."""

from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from tests.conftest import (
    EMPRESA_A1,
    EMPRESA_A2,
    EMPRESA_B1,
    OID_A,
    OID_B,
    OID_DESCONHECIDO,
    USUARIO_A,
    auth,
)

pytestmark = pytest.mark.db

VISAO_GERAL = "/api/telas/visao-geral"
SESSAO = "/api/sessao"


@pytest.fixture
def com_demo(client: TestClient) -> Iterator[None]:
    app: Any = client.app
    demo = get_settings().model_copy(update={"visao_geral_demo": True})

    def _settings() -> Settings:
        return demo

    app.dependency_overrides[get_settings] = _settings
    yield
    app.dependency_overrides.pop(get_settings, None)


@pytest.mark.parametrize(
    ("oid", "nome", "sigla", "dados_ate"),
    [
        (OID_A, "Padrão LANN Alfa", "PA", "2026-10-12"),
        (OID_B, "Padrão LANN Beta", "PB", "2026-10-11"),
    ],
)
def test_visao_geral_moldura_do_proprio_cliente(
    client: TestClient, oid: str, nome: str, sigla: str, dados_ate: str
) -> None:
    r = client.get(VISAO_GERAL, headers=auth(oid))

    assert r.status_code == 200
    corpo = r.json()
    assert corpo["tela"] == "visao-geral"
    assert corpo["versaoContrato"] == "1.0"
    assert corpo["cliente"] == {"nome": nome, "sigla": sigla, "logoUrl": None}
    assert corpo["dadosAte"] == dados_ate
    assert corpo["atualizadoEm"].startswith("2026-10-12T")
    assert corpo["periodos"] == [
        {"id": "mes", "rotulo": "Outubro de 2026"},
        {"id": "ano", "rotulo": "Acumulado de 2026"},
    ]
    # sem regra de indicador ainda
    assert corpo["secoes"] == []


@pytest.mark.usefixtures("com_demo")
def test_visao_geral_demo_traz_as_secoes_do_mockup(client: TestClient) -> None:
    corpo = client.get(VISAO_GERAL, headers=auth(OID_A)).json()

    assert [s["id"] for s in corpo["secoes"]][:1] == ["vendas"]
    kpi = corpo["secoes"][0]["kpis"][0]
    # opcional sem valor sai do JSON (o front usa `campo?:`), não vai como null
    assert "status" not in kpi
    assert "casas" not in kpi["valor"]
    # o cliente continua o do usuário, não o da fixture
    assert corpo["cliente"]["nome"] == "Padrão LANN Alfa"


@pytest.mark.parametrize("empresa", [EMPRESA_A1, EMPRESA_A2])
def test_visao_geral_uma_empresa(client: TestClient, empresa: str) -> None:
    r = client.get(VISAO_GERAL, params={"empresa": empresa}, headers=auth(OID_A))
    assert r.status_code == 200


def test_visao_geral_empresa_de_outro_cliente_403(
    client: TestClient, admin_conn: psycopg.Connection[tuple[Any, ...]]
) -> None:
    r = client.get(VISAO_GERAL, params={"empresa": EMPRESA_B1}, headers=auth(OID_A))

    assert r.status_code == 403
    assert r.json() == {"detail": "Acesso negado."}
    linha = admin_conn.execute(
        """
        select resultado, etapa_negada
          from seg.log_acesso
         where usuario_id = %s and codigo_tela = 'VG-01'
         order by id desc
         limit 1
        """,
        (USUARIO_A,),
    ).fetchone()
    assert linha == ("negado", 5)


def test_visao_geral_consolidada_registra_sem_empresa(
    client: TestClient, admin_conn: psycopg.Connection[tuple[Any, ...]]
) -> None:
    assert client.get(VISAO_GERAL, headers=auth(OID_A)).status_code == 200
    linha = admin_conn.execute(
        """
        select resultado, empresa_id
          from seg.log_acesso
         where usuario_id = %s and codigo_tela = 'VG-01'
         order by id desc
         limit 1
        """,
        (USUARIO_A,),
    ).fetchone()
    assert linha == ("permitido", None)


@pytest.fixture
def ana_sem_empresas(admin_conn: psycopg.Connection[tuple[Any, ...]]) -> Iterator[None]:
    linhas = admin_conn.execute(
        "delete from seg.usuario_empresa where usuario_id = %s "
        "returning usuario_id, cliente_id, empresa_id",
        (USUARIO_A,),
    ).fetchall()
    yield
    with admin_conn.cursor() as cur:
        cur.executemany(
            "insert into seg.usuario_empresa (usuario_id, cliente_id, empresa_id) "
            "values (%s, %s, %s)",
            linhas,
        )


@pytest.mark.usefixtures("ana_sem_empresas")
def test_visao_geral_consolidada_sem_empresa_no_vinculo_403(client: TestClient) -> None:
    assert client.get(VISAO_GERAL, headers=auth(OID_A)).status_code == 403


@pytest.fixture
def vg_bloqueada_para_ana(admin_conn: psycopg.Connection[tuple[Any, ...]]) -> Iterator[None]:
    admin_conn.execute(
        "delete from cfg.perfil_tela "
        "where perfil_id = 'f1000000-0000-4000-8000-00000000000a' and codigo_tela = 'VG-01'"
    )
    yield
    admin_conn.execute(
        "insert into cfg.perfil_tela (perfil_id, codigo_tela, ve_dado_nominal) "
        "values ('f1000000-0000-4000-8000-00000000000a', 'VG-01', false)"
    )


@pytest.mark.usefixtures("vg_bloqueada_para_ana")
def test_visao_geral_tela_nao_liberada_403_e_some_da_sessao(client: TestClient) -> None:
    assert client.get(VISAO_GERAL, headers=auth(OID_A)).status_code == 403
    assert client.get(SESSAO, headers=auth(OID_A)).json()["telas"] == ["FIN-01"]


def test_visao_geral_sem_token_401(client: TestClient) -> None:
    assert client.get(VISAO_GERAL).status_code == 401


def test_sessao_da_ana(client: TestClient) -> None:
    r = client.get(SESSAO, headers=auth(OID_A))

    assert r.status_code == 200
    assert r.json() == {
        "usuario": {"nome": "Usuário fake", "email": "ana@alfa.example"},
        "cliente": {
            "nome": "Padrão LANN Alfa",
            "sigla": "PA",
            "logoUrl": None,
            "slug": "padrao-lann-alfa",
        },
        "papel": "gestor",
        "empresas": [
            {"id": EMPRESA_A2, "nome": "Alfa Comércio"},
            {"id": EMPRESA_A1, "nome": "Alfa Indústria"},
        ],
        "telas": ["FIN-01", "VG-01"],
        "periodos": [{"id": "2026-10", "rotulo": "Outubro de 2026", "dadosAte": "2026-10-12"}],
    }


def test_sessao_do_bruno_so_ve_o_cliente_dele(client: TestClient) -> None:
    corpo = client.get(SESSAO, headers=auth(OID_B)).json()
    assert corpo["cliente"]["slug"] == "padrao-lann-beta"
    assert corpo["empresas"] == [{"id": EMPRESA_B1, "nome": "Beta Serviços"}]


def test_sessao_usuario_desconhecido_403(client: TestClient) -> None:
    r = client.get(SESSAO, headers=auth(OID_DESCONHECIDO))
    assert r.status_code == 403
    assert r.json() == {"detail": "Acesso negado."}


def test_sessao_sem_token_401(client: TestClient) -> None:
    assert client.get(SESSAO).status_code == 401


CLIENTE_B = "c1000000-0000-4000-8000-00000000000b"
CAMINHO_VENDAS = "dataflow/dbo_f_vendas_teste_api/dbo_f_vendas_teste_api.parquet"
CAMINHO_METAS = "dataflow/dbo_f_metas_teste_api/dbo_f_metas_teste_api.parquet"


@pytest.fixture
def vendas_do_bruno(admin_conn: psycopg.Connection[tuple[Any, ...]]) -> Iterator[None]:
    # Tabelas sem coluna de empresa, como as da Nilo Maia. Agosto fica de fora: a seção usa
    # o mês mais recente com venda.
    admin_conn.execute(
        """
        insert into cfg.fonte_carga
            (cliente_id, tipo, caminho_bucket, ativo, tabela_destino, contem_dado_nominal)
        values (%(c)s, 'tabela', %(vendas)s, false, 'f_vendas_nilo_maia', true),
               (%(c)s, 'tabela', %(metas)s, false, 'f_metas_nilo_maia', false)
        """,
        {"c": CLIENTE_B, "vendas": CAMINHO_VENDAS, "metas": CAMINHO_METAS},
    )
    admin_conn.execute(
        """
        insert into mart.f_vendas_nilo_maia
            (_linha, _cliente_id, "en_datemicao", "en_codcli", "en_numdoc", "en_VALTOT")
        values (1, %(c)s, '28/08/2026 00:00:00', 'C9', 'P0', 999),
               (2, %(c)s, '01/09/2026 00:00:00', 'C1', 'P1', 600),
               (3, %(c)s, '01/09/2026 00:00:00', 'C1', 'P1', 400),
               (4, %(c)s, '2026-09-15', 'C2', 'P2', 1000),
               (5, %(c)s, 'sem data', 'C3', 'P3', 5000)
        """,
        {"c": CLIENTE_B},
    )
    admin_conn.execute(
        """
        insert into mart.f_metas_nilo_maia (_linha, _cliente_id, "Competencia", "Valor")
        values (1, %(c)s, '01/08/2026 00:00:00', 1000),
               (2, %(c)s, '01/09/2026 00:00:00', 4000)
        """,
        {"c": CLIENTE_B},
    )
    # histórico da carga: agosto (setembro vem da tabela de vendas)
    admin_conn.execute(
        """
        insert into mart.resumo_mensal
            (cliente_id, empresa_id, tabela, medida, mes, valor, data_snapshot)
        values (%(c)s, null, 'f_vendas_nilo_maia', 'faturado', '2026-08-01', 999, '2026-10-11'),
               (%(c)s, null, 'f_vendas_nilo_maia', 'faturado', '2026-09-01', 2000, '2026-10-11')
        """,
        {"c": CLIENTE_B},
    )
    yield
    admin_conn.execute("delete from mart.resumo_mensal where cliente_id = %s", (CLIENTE_B,))
    admin_conn.execute("delete from cfg.parametro_cliente where cliente_id = %s", (CLIENTE_B,))
    admin_conn.execute("delete from cfg.feriado where cliente_id = %s", (CLIENTE_B,))
    for tabela in ("f_vendas_nilo_maia", "f_metas_nilo_maia"):
        admin_conn.execute(
            f"delete from mart.{tabela} where _cliente_id = %s",  # noqa: S608
            (CLIENTE_B,),
        )
    admin_conn.execute(
        "delete from cfg.fonte_carga where cliente_id = %s and caminho_bucket = any(%s)",
        (CLIENTE_B, [CAMINHO_VENDAS, CAMINHO_METAS]),
    )


@pytest.mark.usefixtures("vendas_do_bruno")
def test_secao_vendas_do_mes_mais_recente(client: TestClient) -> None:
    secoes = client.get(VISAO_GERAL, headers=auth(OID_B)).json()["secoes"]

    assert [s["id"] for s in secoes] == ["vendas"]
    vendas = secoes[0]
    assert vendas["resumo"] == "Dados de setembro de 2026."
    kpis = {k["rotulo"]: k for k in vendas["kpis"]}
    assert list(kpis) == [
        "Meta de setembro",
        "Faturado até 15/09",
        "Projeção de fechamento",
        "A realizar por dia útil",
    ]
    # setembro/2026: 21 dias úteis (7/09 é feriado); até 15/09, 10
    assert kpis["Meta de setembro"]["valor"]["valor"] == 4000
    # linha sem data e agosto ficam de fora
    faturado = kpis["Faturado até 15/09"]
    assert faturado["valor"]["valor"] == 2000
    assert faturado["contexto"] == "meta até a data: R$ 2 mil"  # 4000 × 10 / 21
    assert faturado["status"] == "saudavel"
    projecao = kpis["Projeção de fechamento"]
    assert projecao["valor"]["valor"] == 4200  # 200 por dia × 21
    assert projecao["contexto"] == "R$ 200 acima da meta"
    assert projecao["status"] == "saudavel"
    realizar = kpis["A realizar por dia útil"]
    assert round(realizar["valor"]["valor"], 2) == 181.82  # 2000 / 11
    assert realizar["contexto"] == "hoje vende R$ 200 por dia"

    [ind] = vendas["indicadores"]
    assert ind["id"] == "vendas-meta"
    # o mês usa a projeção: 4200 de 4000
    assert ind["periodos"]["mes"]["status"] == "saudavel"
    assert ind["periodos"]["mes"]["ponteiro"] == 105
    assert ind["periodos"]["mes"]["referencia"] == "projeção de R$ 4 mil contra meta de R$ 4 mil"
    # ano com o realizado — agosto: 999 de 1000; setembro: 2000 de 4000
    assert ind["periodos"]["ano"]["referencia"] == "R$ 3 mil de ago a set contra meta de R$ 5 mil"
    assert ind["tendencia"]["rotulos"] == ["ago-26", "set-26"]
    assert ind["tendencia"]["real"] == [999, 4200]
    assert ind["tendencia"]["referencia"] == [1000, 4000]


@pytest.mark.usefixtures("vendas_do_bruno")
def test_feriado_do_cliente_entra_nos_dias_uteis(
    client: TestClient, admin_conn: psycopg.Connection[tuple[Any, ...]]
) -> None:
    admin_conn.execute(
        "insert into cfg.feriado (cliente_id, data, nome) values (%s, '2026-09-14', 'Teste')",
        (CLIENTE_B,),
    )
    [vendas] = client.get(VISAO_GERAL, headers=auth(OID_B)).json()["secoes"]
    kpis = {k["rotulo"]: k for k in vendas["kpis"]}

    # 9 dias úteis passados, 20 no mês
    assert round(kpis["Projeção de fechamento"]["valor"]["valor"], 2) == 4444.44
    assert kpis["A realizar por dia útil"]["contexto"] == "hoje vende R$ 222 por dia"


@pytest.mark.usefixtures("vendas_do_bruno")
def test_secao_custos_com_cmv_por_ramo(
    client: TestClient, admin_conn: psycopg.Connection[tuple[Any, ...]]
) -> None:
    # setembro: MERCEARIA 600+400 com custo 850; CONSTRUTORA 1000 com custo 800
    admin_conn.execute(
        """
        update mart.f_vendas_nilo_maia
           set "en_precus" = case _linha when 2 then 500 when 3 then 350 when 4 then 800 end,
               "en_ramo" = case _linha when 4 then 'CONSTRUTORA' else 'MERCEARIA' end
         where _cliente_id = %s and _linha in (2, 3, 4)
        """,
        (CLIENTE_B,),
    )
    # agosto, no histórico por ramo: MERCEARIA com CMV de 80%
    admin_conn.execute(
        """
        insert into mart.resumo_mensal
            (cliente_id, empresa_id, tabela, medida, dimensao, mes, valor, data_snapshot)
        values (%(c)s, null, %(t)s, 'faturado_ramo', 'MERCEARIA', %(ago)s, 1000, %(snap)s),
               (%(c)s, null, %(t)s, 'custo_ramo', 'MERCEARIA', %(ago)s, 800, %(snap)s),
               (%(c)s, null, %(t)s, 'custo', '', %(ago)s, 800, %(snap)s)
        """,
        {"c": CLIENTE_B, "t": "f_vendas_nilo_maia", "ago": "2026-08-01", "snap": "2026-10-11"},
    )
    secoes = {s["id"]: s for s in client.get(VISAO_GERAL, headers=auth(OID_B)).json()["secoes"]}

    custos = secoes["custos"]
    kpis = {k["rotulo"]: k for k in custos["kpis"]}
    # 1650 / 2000
    assert kpis["CMV do mês"]["valor"]["valor"] == 82.5
    assert kpis["Lucro bruto até 15/09"]["valor"]["valor"] == 350
    # total de agosto: 800 / 999
    assert kpis["CMV do mês"]["contexto"].endswith("do mês anterior (80,1%)")
    [mercearia] = [i for i in custos["indicadores"] if i["id"] == "cmv-mercearia"]
    assert mercearia["nome"] == "CMV de mercearia"
    assert mercearia["periodos"]["mes"]["valor"]["valor"] == 85  # 850 / 1000
    assert mercearia["periodos"]["mes"]["status"] == "critico"  # +5 p.p.
    assert mercearia["tendencia"]["rotulos"] == ["ago-26", "set-26"]


@pytest.mark.usefixtures("vendas_do_bruno")
def test_periodos_com_o_mes_dos_numeros(client: TestClient) -> None:
    corpo = client.get(VISAO_GERAL, headers=auth(OID_B)).json()
    assert corpo["periodos"][0] == {"id": "mes", "rotulo": "Setembro de 2026"}


@pytest.mark.usefixtures("vendas_do_bruno")
def test_sessao_lista_os_meses_com_venda_do_mais_recente(client: TestClient) -> None:
    # agosto e setembro no resumo da carga; outubro é o mês do snapshot (11/10)
    assert client.get(SESSAO, headers=auth(OID_B)).json()["periodos"] == [
        {"id": "2026-10", "rotulo": "Outubro de 2026", "dadosAte": "2026-10-11"},
        {"id": "2026-09", "rotulo": "Setembro de 2026", "dadosAte": "2026-09-30"},
        {"id": "2026-08", "rotulo": "Agosto de 2026", "dadosAte": "2026-08-31"},
    ]


@pytest.mark.usefixtures("vendas_do_bruno")
def test_visao_geral_de_mes_fechado(client: TestClient) -> None:
    corpo = client.get(VISAO_GERAL, params={"mes": "2026-08"}, headers=auth(OID_B)).json()

    assert corpo["periodos"] == [
        {"id": "mes", "rotulo": "Agosto de 2026"},
        {"id": "ano", "rotulo": "Acumulado de 2026"},
    ]
    # dadosAte continua o da última carga
    assert corpo["dadosAte"] == "2026-10-11"
    [vendas] = corpo["secoes"]
    assert vendas["resumo"] == "Dados de agosto de 2026."
    kpis = {k["rotulo"]: k for k in vendas["kpis"]}
    # mês fechado: sem projeção nem "A realizar"; o faturado é o do mês inteiro
    assert list(kpis) == ["Meta de agosto", "Faturado em agosto"]
    assert kpis["Faturado em agosto"]["valor"]["valor"] == 999
    assert kpis["Faturado em agosto"]["contexto"] == "vendas até 31/08"


@pytest.mark.usefixtures("vendas_do_bruno")
def test_visao_geral_de_mes_fora_da_tabela_sai_do_resumo(
    client: TestClient, admin_conn: psycopg.Connection[tuple[Any, ...]]
) -> None:
    # julho só no resumo da carga, fora da janela da tabela de vendas
    admin_conn.execute(
        """
        insert into mart.resumo_mensal
            (cliente_id, empresa_id, tabela, medida, mes, valor, data_snapshot)
        values (%(c)s, null, 'f_vendas_nilo_maia', 'faturado', '2026-07-01', 500, '2026-10-11'),
               (%(c)s, null, 'f_vendas_nilo_maia', 'pedidos', '2026-07-01', 5, '2026-10-11')
        """,
        {"c": CLIENTE_B},
    )
    corpo = client.get(VISAO_GERAL, params={"mes": "2026-07"}, headers=auth(OID_B)).json()

    [vendas] = corpo["secoes"]
    kpis = {k["rotulo"]: k for k in vendas["kpis"]}
    # sem meta em julho: só o faturado do mês inteiro
    assert list(kpis) == ["Faturado em julho"]
    assert kpis["Faturado em julho"]["valor"]["valor"] == 500
    assert kpis["Faturado em julho"]["contexto"] == "vendas até 31/07"


def test_visao_geral_mes_fora_do_formato_422(client: TestClient) -> None:
    r = client.get(VISAO_GERAL, params={"mes": "2026-13"}, headers=auth(OID_A))
    assert r.status_code == 422


def test_visao_geral_mes_sem_dados_422(client: TestClient) -> None:
    r = client.get(VISAO_GERAL, params={"mes": "2025-01"}, headers=auth(OID_A))
    assert r.status_code == 422
    assert r.json() == {"detail": "Mês sem dados."}


@pytest.mark.usefixtures("vendas_do_bruno")
def test_faixas_do_farol_vem_de_cfg_parametro_cliente(
    client: TestClient, admin_conn: psycopg.Connection[tuple[Any, ...]]
) -> None:
    admin_conn.execute(
        """
        insert into cfg.parametro_cliente (cliente_id, chave, valor, vigente_de)
        values (%s, 'farol.vendas_meta',
                '{"atencao": 120, "critico": 110, "min": 0, "max": 130}', '2026-01-01')
        """,
        (CLIENTE_B,),
    )
    [vendas] = client.get(VISAO_GERAL, headers=auth(OID_B)).json()["secoes"]
    ind = vendas["indicadores"][0]

    # 105% da meta: abaixo dos 110% do parâmetro
    assert ind["periodos"]["mes"]["status"] == "critico"
    assert ind["periodos"]["mes"]["ponteiro"] == 105
    assert ind["gauge"]["min"] == 0
    assert ind["gauge"]["faixas"][0] == {"de": 0, "ate": 110, "status": "critico"}


@pytest.mark.usefixtures("vendas_do_bruno")
def test_secao_vendas_some_com_empresa_escolhida(client: TestClient) -> None:
    # a tabela não tem empresa: só entra na visão de todas as empresas
    r = client.get(VISAO_GERAL, params={"empresa": EMPRESA_B1}, headers=auth(OID_B))
    assert r.json()["secoes"] == []


@pytest.mark.usefixtures("vendas_do_bruno")
def test_secao_vendas_nao_vaza_para_outro_cliente(client: TestClient) -> None:
    assert client.get(VISAO_GERAL, headers=auth(OID_A)).json()["secoes"] == []


CAMINHOS_PJ = [
    "dataflow/dbo_f_vendas_teste_pj/dbo_f_vendas_teste_pj.parquet",
    "dataflow/dbo_f_vendas_agile_teste_pj/dbo_f_vendas_agile_teste_pj.parquet",
    "dataflow/dbo_f_metas_teste_pj/dbo_f_metas_teste_pj.parquet",
]
COLUNAS_PJ = (
    '{"data": "en_dataemissao", "valor": "en_valortotal", "custo": "en_custototal",'
    ' "pedido": "en_numpedido", "cliente": "en_codigocliente",'
    ' "produto": "en_codigoproduto", "abertura": "it_grupo"}'
)


@pytest.fixture
def vendas_pj_do_bruno(admin_conn: psycopg.Connection[tuple[Any, ...]]) -> Iterator[None]:
    # Os números de vendas_do_bruno no formato da PJ Cortes: outras colunas (colunas_modelo)
    # e duas tabelas de vendas, a atual (setembro) e a do sistema antigo (agosto).
    admin_conn.execute(
        """
        insert into cfg.fonte_carga
            (cliente_id, tipo, caminho_bucket, ativo, tabela_destino, contem_dado_nominal,
             colunas_modelo)
        values (%(c)s, 'tabela', %(v)s, false, 'f_vendas_pj', true, %(cols)s),
               (%(c)s, 'tabela', %(a)s, false, 'f_vendas_agile_pj_cortes', true, %(cols)s),
               (%(c)s, 'tabela', %(m)s, false, 'f_metas_comercial_pj', false, '{"data": "Data"}')
        """,
        {
            "c": CLIENTE_B,
            "v": CAMINHOS_PJ[0],
            "a": CAMINHOS_PJ[1],
            "m": CAMINHOS_PJ[2],
            "cols": COLUNAS_PJ,
        },
    )
    admin_conn.execute(
        """
        insert into mart.f_vendas_pj
            (_linha, _cliente_id, "en_dataemissao", "en_codigocliente", "en_numpedido",
             "en_codigoproduto", "en_valortotal", "en_custototal", "it_grupo")
        values (1, %(c)s, '2026-09-01', 'C1', 'P1', 'X', 600, 500, 'BOVINOS'),
               (2, %(c)s, '2026-09-01', 'C1', 'P1', 'Y', 400, 350, 'BOVINOS'),
               (3, %(c)s, '2026-09-15', 'C2', 'P2', 'X', 1000, 800, 'SUINOS')
        """,
        {"c": CLIENTE_B},
    )
    admin_conn.execute(
        """
        insert into mart.f_vendas_agile_pj_cortes
            (_linha, _cliente_id, "en_dataemissao", "en_codigocliente", "en_numpedido",
             "en_valortotal", "en_custototal", "it_grupo")
        values (1, %(c)s, '2026-08-28', 'C9', 'P0', 999, 800, 'BOVINOS')
        """,
        {"c": CLIENTE_B},
    )
    admin_conn.execute(
        """
        insert into mart.f_metas_comercial_pj (_linha, _cliente_id, "Data", "Valor")
        values (1, %(c)s, '2026-08-01', 1000),
               (2, %(c)s, '2026-09-01', 4000)
        """,
        {"c": CLIENTE_B},
    )
    # histórico da carga: cada tabela grava o seu
    admin_conn.execute(
        """
        insert into mart.resumo_mensal
            (cliente_id, empresa_id, tabela, medida, mes, valor, data_snapshot)
        values (%(c)s, null, 'f_vendas_agile_pj_cortes', 'faturado', '2026-08-01', 999,
                '2026-10-11'),
               (%(c)s, null, 'f_vendas_pj', 'faturado', '2026-09-01', 2000, '2026-10-11')
        """,
        {"c": CLIENTE_B},
    )
    admin_conn.execute(
        """
        insert into cfg.parametro_cliente (cliente_id, chave, valor, vigente_de)
        values (%s, 'custos.abertura',
                '{"nome": "grupo", "descricao": "grupo de produto", "plural": "grupos"}',
                '2026-01-01')
        """,
        (CLIENTE_B,),
    )
    yield
    admin_conn.execute("delete from mart.resumo_mensal where cliente_id = %s", (CLIENTE_B,))
    admin_conn.execute("delete from cfg.parametro_cliente where cliente_id = %s", (CLIENTE_B,))
    for tabela in ("f_vendas_pj", "f_vendas_agile_pj_cortes", "f_metas_comercial_pj"):
        admin_conn.execute(
            f"delete from mart.{tabela} where _cliente_id = %s",  # noqa: S608
            (CLIENTE_B,),
        )
    admin_conn.execute(
        "delete from cfg.fonte_carga where cliente_id = %s and caminho_bucket = any(%s)",
        (CLIENTE_B, CAMINHOS_PJ),
    )


@pytest.mark.usefixtures("vendas_pj_do_bruno")
def test_vendas_com_colunas_da_pj_e_duas_tabelas(client: TestClient) -> None:
    secoes = {s["id"]: s for s in client.get(VISAO_GERAL, headers=auth(OID_B)).json()["secoes"]}

    vendas = secoes["vendas"]
    assert vendas["resumo"] == "Dados de setembro de 2026."
    kpis = {k["rotulo"]: k for k in vendas["kpis"]}
    assert kpis["Meta de setembro"]["valor"]["valor"] == 4000
    # agosto, da tabela antiga, fica fora do mês
    assert kpis["Faturado até 15/09"]["valor"]["valor"] == 2000
    [ind] = [i for i in vendas["indicadores"] if i["id"] == "vendas-meta"]
    # agosto vem do resumo da tabela antiga
    assert ind["periodos"]["ano"]["referencia"] == "R$ 3 mil de ago a set contra meta de R$ 5 mil"
    assert ind["tendencia"]["real"] == [999, 4200]

    custos = secoes["custos"]
    assert custos["resumo"] == "Dados de setembro de 2026, por grupo de produto."
    nomes = {i["nome"] for i in custos["indicadores"]}
    assert nomes <= {"CMV de bovinos", "CMV de suinos", "CMV dos demais grupos"}
