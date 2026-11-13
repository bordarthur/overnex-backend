"""Seção Resultado: DRE pelo plano gerencial, velocímetros e cartões — sem banco."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from app.farol.parametro import PADROES, Parametro
from app.telas.plano import Plano
from app.telas.visao_geral import resultado
from app.telas.visao_geral.contra_referencia import Mensal, Razao
from app.telas.visao_geral.relativo import Serie, indicador_relativo
from app.telas.visao_geral.resultado import (
    DESPESAS_FIXAS_RECEITA,
    MARGEM_EBITDA,
    Operacao,
    cenario_do_orcamento,
    com_inadimplencia,
    dre_por_mes,
    grupos_da_operacao,
    kpis_resultado,
    medidas_da_dre,
    orcado_das_linhas,
    somar_orcado,
)

D = Decimal
SET = date(2026, 9, 1)
EBITDA = Razao("ebitda", "receita_liquida")
FIXAS = Razao("despesas_fixas", "receita_liquida")


def _dre(ebitda: str, fixas: str = "200", receita: str = "1000", mc: str = "300") -> dict[str, D]:
    return {
        "receita_liquida": D(receita),
        "margem_contribuicao": D(mc),
        "despesas_fixas": D(fixas),
        "folha": D(100),
        "ebitda": D(ebitda),
        "lucro_liquido": D(ebitda) - D(20),
    }


def _mensal(*ebitdas: str, inicio: int = 3) -> Mensal:
    """Meses a partir de mar/2026, receita líquida de 1.000 e o EBITDA de cada um."""
    return {date(2026, inicio + i, 1): _dre(e) for i, e in enumerate(ebitdas)}


def test_dre_pelo_grupo_com_despesa_negativa_ou_positiva() -> None:
    negativas = {
        "100": D(1000),
        "110": D(-50),
        "120": D(-600),
        "130": D(-50),
        "140": D(-200),
        "150": D(-10),
        "160": D(5),
        "170": D(-15),
        "200": D(-20),
    }
    positivas = {k: abs(v) for k, v in negativas.items()}

    for grupos in (negativas, positivas):
        m = medidas_da_dre(grupos, D(-120))
        assert m == {
            "receita_liquida": D(950),
            "margem_contribuicao": D(300),
            "despesas_fixas": D(200),
            "folha": D(120),
            "ebitda": D(100),
            # 100 - 10 + 5 - 15 - 20
            "lucro_liquido": D(60),
        }


def test_vendas_e_devolucoes_somam_aos_lancamentos() -> None:
    operacao = grupos_da_operacao(
        {SET: {"faturado": D(1000), "custo": D(600), "pedidos": D(40)}},
        {SET: D(30), date(2026, 10, 1): D(5)},
    )
    assert operacao.grupos == {
        SET: {"100": D(1000), "120": D(600), "110": D(30)},
        date(2026, 10, 1): {"110": D(5)},
    }

    # lançamentos: 20 de dedução e 50 de despesa variável; agosto só tem lançamento sem
    # receita, e outubro só devolução: nenhum dos dois vira mês da DRE
    lancados = {SET: {"110": D(-20), "130": D(-50)}, date(2026, 8, 1): {"140": D(-9)}}
    real = dre_por_mes(lancados, {}, operacao)

    assert list(real) == [SET]
    assert real[SET]["receita_liquida"] == D(950)  # 1000 - (30 + 20)
    assert real[SET]["margem_contribuicao"] == D(300)  # 950 - 600 - 50


def test_despesas_pelo_de_para_com_o_sinal_do_power_bi() -> None:
    operacao = grupos_da_operacao(
        {SET: {"faturado": D(1000), "custo": D(600)}},
        {},
        [
            (SET, "140.01.01", D(80)),  # salário: despesa fixa e folha
            (SET, "140.02.03", D(20)),
            (SET, "130.03.01", D(-5)),  # estorno: abate a despesa variável
            (SET, "100.02.03", D(-40)),  # frete retorno: crédito no contas a pagar é receita
            (SET, "120.01.52", D(10)),
        ],
    )
    assert operacao.grupos[SET] == {
        "100": D(1040),
        "120": D(610),
        "140": D(100),
        "130": D(-5),
    }
    assert operacao.folha == {SET: D(80)}

    real = dre_por_mes({SET: {"130": D(-50)}}, {SET: D(-20)}, operacao)
    assert real[SET]["receita_liquida"] == D(1040)
    assert real[SET]["margem_contribuicao"] == D(385)  # 1040 - 610 - (50 - 5)
    assert real[SET]["ebitda"] == D(285)
    assert real[SET]["folha"] == D(100)  # 20 lançado + 80 do contas a pagar


def test_inadimplencia_nas_deducoes_pela_taxa_do_mes() -> None:
    out = date(2026, 10, 1)
    op = Operacao(
        grupos={SET: {"100": D("19072782.05"), "110": D("138044.71")}, out: {"110": D(5)}},
        folha={SET: D(1)},
    )

    com = com_inadimplencia(op, lambda mes: D("0.4"))

    # o mesmo da DRE do Power BI da PJ em jan/26: 0,4% da receita bruta
    assert com.grupos[SET]["110"] - D("138044.71") == D("76291.12820")
    # deduções de jan/26 no Power BI: 214.335,83
    assert round(com.grupos[SET]["110"], 2) == D("214335.84")
    # mês sem receita bruta: nada
    assert com.grupos[out] == {"110": D(5)}
    assert com.folha == op.folha
    # a operação original fica igual
    assert op.grupos[SET]["110"] == D("138044.71")
    # sem taxa no mês: nada
    assert com_inadimplencia(op, lambda mes: None).grupos == op.grupos


def test_contas_fora_da_dre_por_parametro(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        resultado,
        "parametros_do_cliente",
        lambda conn, c: [
            Parametro(
                "dre.contas_fora", {"contas": ["120.03.22", "180.01"]}, date(2026, 1, 1), None
            )
        ],
    )
    fora = resultado.fora_da_dre(None, None)  # type: ignore[arg-type]

    assert fora(SET, "120.03.22")
    # um código de nível 2 tira tudo dentro dele
    assert fora(SET, "180.01.06")
    assert not fora(SET, "120.03.2")
    assert not fora(SET, "120.03.01")
    # antes do vigente_de, nada fica fora
    assert not fora(date(2025, 12, 1), "120.03.22")


def test_grupos_da_operacao_sem_as_contas_fora() -> None:
    despesas = [
        (SET, "120.03.22", D(784)),  # Premiações (C)
        (SET, "120.03.01", D(100)),  # Salários (C)
    ]
    op = grupos_da_operacao({}, {}, despesas, fora=lambda mes, conta: conta == "120.03.22")
    assert op.grupos[SET]["120"] == D(100)
    assert grupos_da_operacao({}, {}, despesas).grupos[SET]["120"] == D(884)


def test_taxa_de_inadimplencia_do_parametro(monkeypatch: pytest.MonkeyPatch) -> None:
    def parametros(valor: object) -> None:
        monkeypatch.setattr(
            resultado,
            "parametros_do_cliente",
            lambda conn, c: [Parametro("dre.inadimplencia", valor, date(2020, 1, 1), None)],
        )

    for valor, esperado in (
        ({"taxa": 0.4}, D("0.4")),
        ({"taxa": "0,4"}, None),
        ({"taxa": "0.4"}, D("0.4")),
        ({"taxa": 0}, None),
        ({"taxa": True}, None),
        ({"outra": 1}, None),
        (0.4, None),
    ):
        parametros(valor)
        assert resultado.taxa_inadimplencia(None, None, SET) == esperado, valor  # type: ignore[arg-type]
    # antes do vigente_de: nada
    parametros({"taxa": 0.4})
    assert resultado.taxa_inadimplencia(None, None, date(2019, 12, 1)) is None  # type: ignore[arg-type]


def test_sem_receita_bruta_nao_ha_dre() -> None:
    assert medidas_da_dre({"140": D(-200)}, D(0)) is None


def test_cenario_base_ou_o_unico() -> None:
    assert cenario_do_orcamento(["Meta", " BASE "]) == "base"
    assert cenario_do_orcamento(["Realista", "realista "]) == "realista"
    assert cenario_do_orcamento(["Meta", "Otimista"]) is None


def test_cenario_escolhido_por_parametro() -> None:
    cenarios = ["Conservador", "Meta", "Super Meta", "Base"]
    # o parâmetro vence o "Base"
    assert cenario_do_orcamento(cenarios, " meta ") == "meta"
    # cenário que não existe no orçamento: sem orçado
    assert cenario_do_orcamento(cenarios, "Otimista") is None


def test_margem_ebitda_contra_o_orcado() -> None:
    real = _mensal("100", "100", "100", "100", "100", "100", "78")  # mar a set
    orcado = {m: _dre("100") for m in real}

    ind = indicador_relativo(
        MARGEM_EBITDA,
        SET,
        Serie(real, EBITDA, D(100)),
        Serie(orcado, EBITDA, D(100)),
        PADROES["margem_ebitda"],
    )

    assert ind is not None
    mes = ind.periodos.mes
    assert mes.valor.valor == D("7.8")
    assert mes.status == "atencao"  # -2,2 p.p.: abaixo de -0,5 e acima de -2,5
    assert mes.delta is not None
    assert (mes.delta.valor, mes.delta.unidade) == (D("-2.2"), "pp")
    assert mes.delta_ruim
    assert mes.referencia == "contra 10,0% orçado"
    assert (ind.gauge.min, ind.gauge.max, ind.gauge.meta) == (D(5), D(12), D(10))
    assert ind.tendencia.faixa == (D("9.5"), D(12))
    # ano: (6 × 100 + 78) / 7.000 = 9,69%, contra 10% orçado
    assert ind.periodos.ano.status == "saudavel"
    assert ind.periodos.ano.referencia == "de mar a set, contra 10,0% orçado"


def test_sem_orcado_compara_com_a_media_dos_meses_anteriores() -> None:
    real = {date(2026, 7, 1): _dre("100", fixas="200"), SET: _dre("100", fixas="210")}
    real[date(2026, 8, 1)] = _dre("100", fixas="200")

    ind = indicador_relativo(
        DESPESAS_FIXAS_RECEITA, SET, Serie(real, FIXAS, D(100)), None, PADROES["despesas_fixas"]
    )

    assert ind is not None
    mes = ind.periodos.mes
    assert mes.valor.valor == D(21)
    assert mes.status == "critico"  # +1 p.p., acima de +0,6
    assert mes.delta_ruim
    assert mes.referencia == "da receita líquida, contra a média de 2 meses (jul a ago): 20,0%"


def test_um_mes_so_nao_tem_indicador() -> None:
    real = {SET: _dre("100")}
    ind = indicador_relativo(
        MARGEM_EBITDA, SET, Serie(real, EBITDA, D(100)), None, PADROES["margem_ebitda"]
    )
    assert ind is None


def test_kpis_com_ponto_de_equilibrio_e_margem_de_seguranca() -> None:
    # abr: margem de contribuição de 40% e fixas de 200 → ponto de 500, segurança de 50%
    real = {date(2026, 4, 1): _dre("200", mc="400"), SET: _dre("100")}
    orcado = {SET: _dre("120")}

    kpis = kpis_resultado(SET, real, orcado, "atencao")

    assert [k.rotulo for k in kpis] == [
        "EBITDA de setembro",
        "Lucro líquido de setembro",
        "Ponto de equilíbrio",
        "Margem de segurança",
    ]
    assert kpis[0].contexto == "orçado R$ 120"
    assert kpis[0].status == "atencao"
    assert kpis[1].contexto == "orçado R$ 100"
    # set: margem de contribuição de 30%, fixas de 200 → 666,67; segurança de 33,3%
    assert kpis[2].valor.valor.quantize(D("0.01")) == D("666.67")
    assert kpis[3].valor.valor.quantize(D("0.1")) == D("33.3")
    assert kpis[3].contexto == "era 50% em abril"


def test_kpis_sem_orcado_e_sem_margem_de_contribuicao() -> None:
    real = {date(2026, 8, 1): _dre("-50", mc="0"), SET: _dre("-80", mc="-10")}

    kpis = kpis_resultado(SET, real, {}, None)

    # sem margem de contribuição positiva não existe ponto de equilíbrio
    assert [k.rotulo for k in kpis] == ["EBITDA de setembro", "Lucro líquido de setembro"]
    assert kpis[0].contexto == "mês anterior: -R$ 50"


# ---------- leitura numa ida: realizado e orçado de `consultas` ----------

AGO = date(2026, 8, 1)


def _nada_fora(mes: date, conta: str) -> bool:
    return False


def _orcamento(cenario: str, receita: str, folha: str, mes: date = SET) -> list[dict[str, Any]]:
    return [
        {"mes": mes, "conta": "100.01.01", "cenario": cenario, "valor": D(receita)},
        {"mes": mes, "conta": "140.01.01", "cenario": cenario, "valor": D(folha)},
    ]


def test_orcado_no_cenario_base_no_escolhido_ou_em_tudo_sem_nome() -> None:
    linhas = [*_orcamento("base", "1000", "-300"), *_orcamento("meta", "1200", "-250")]

    # sem parâmetro: "base"; com ele, o escolhido
    assert orcado_das_linhas(linhas, None, Plano(), _nada_fora)[SET]["ebitda"] == D(700)
    assert orcado_das_linhas(linhas, " Meta ", Plano(), _nada_fora)[SET]["ebitda"] == D(950)
    # orçamento sem cenário: vale tudo
    sem_nome = orcado_das_linhas(_orcamento("", "1000", "-300"), None, Plano(), _nada_fora)
    assert sem_nome[SET]["ebitda"] == D(700)
    assert sem_nome[SET]["folha"] == D(300)


def test_orcado_com_varios_cenarios_e_nenhum_escolhido_fica_vazio(
    caplog: pytest.LogCaptureFixture,
) -> None:
    linhas = [*_orcamento("meta", "1000", "-300"), *_orcamento("otimista", "1200", "-250")]

    assert orcado_das_linhas(linhas, None, Plano(), _nada_fora, uuid4()) == {}
    assert "sem cenário escolhido" in caplog.text


def test_orcado_com_valor_nulo_mantem_o_mes() -> None:
    linhas = [{"mes": AGO, "conta": "140.02.01", "cenario": "", "valor": None}]

    grupos, folha = somar_orcado(linhas, Plano(), _nada_fora)

    assert grupos == {AGO: {"140": D(0)}}
    assert folha == {}


class _Leituras:
    """`consultas` e `em_uma_ida` simulados: guarda cada ida e devolve as linhas pedidas."""

    def __init__(self, linhas: dict[str, list[dict[str, Any]]]) -> None:
        self.linhas = linhas
        self.idas: list[list[str]] = []

    def consultas(
        self, conn: Any, ctx: Any, de: date, ate: date, com_agrupados: bool = True
    ) -> dict[str, Any]:
        nomes = ["agrupados" if com_agrupados else "lancamentos", "orcamento"]
        return dict.fromkeys(nomes)

    def em_uma_ida(self, conn: Any, consultas: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        self.idas.append(list(consultas))
        return {n: self.linhas.get(n, []) for n in consultas}


@pytest.fixture
def leituras(monkeypatch: pytest.MonkeyPatch) -> _Leituras:
    falso = _Leituras({"orcamento": _orcamento("base", "1000", "-250")})
    monkeypatch.setattr(resultado, "tabelas_do_cliente", lambda *a: ["f_lancamentos_pj"])
    monkeypatch.setattr(resultado, "consultas", falso.consultas)
    monkeypatch.setattr(resultado, "em_uma_ida", falso.em_uma_ida)
    monkeypatch.setattr(resultado, "ler_plano", lambda conn: Plano())
    monkeypatch.setattr(resultado, "fora_da_dre", lambda conn, c: _nada_fora)
    monkeypatch.setattr(resultado, "taxa_inadimplencia", lambda conn, c, mes: None)
    monkeypatch.setattr(resultado, "cenario_escolhido", lambda conn, ctx, d: None)
    monkeypatch.setattr(resultado, "farol", lambda conn, c, chave, ref: PADROES[chave])
    return falso


def _secao() -> Any:
    ctx: Any = SimpleNamespace(sessao=SimpleNamespace(conn=None), cliente_id=uuid4())
    return resultado.secao_resultado(ctx, SET)


def test_secao_le_realizado_e_orcado_numa_ida(leituras: _Leituras) -> None:
    # agrupados no sinal do resultado: receita positiva, custo negativo
    leituras.linhas["agrupados"] = [
        {"mes": SET, "conta": "100.01.01", "valor": D(1000)},
        {"mes": SET, "conta": "140.01.01", "valor": D(-300)},
    ]

    s = _secao()

    assert leituras.idas == [["agrupados", "orcamento"]]
    assert s.secao.kpis[0].valor.valor == D(700)
    assert s.secao.kpis[0].contexto == "orçado R$ 750"


def test_agrupados_sem_valor_le_de_novo_pela_regra_sem_eles(leituras: _Leituras) -> None:
    leituras.linhas["agrupados"] = [{"mes": SET, "conta": "100.01.01", "valor": D(0)}]
    # lançamentos no sinal do contas a pagar: entrada negativa, saída positiva
    leituras.linhas["lancamentos"] = [
        {"mes": SET, "conta": "100.01.01", "valor": D(-1000)},
        {"mes": SET, "conta": "140.01.01", "valor": D(300)},
    ]

    s = _secao()

    assert leituras.idas == [["agrupados", "orcamento"], ["lancamentos", "orcamento"]]
    assert s.secao.kpis[0].valor.valor == D(700)
