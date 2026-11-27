"""O script de produção (supabase/producao/distribuidora_overnex_cadastro.sql) bate com o
gerador, e as contas usadas existem no plano de contas padrão."""

import re

from demo.folha import LANCAMENTOS
from demo.modelo import (
    CONTAS,
    DEPRECIACAO,
    DESCONTOS_CONCEDIDOS,
    DEVOLUCOES_VENDA,
    EMPRESAS,
    INICIO_FLUXO,
    JUROS_CLIENTES,
    JUROS_MORA,
    LINHAS,
    RENDIMENTO,
    SALDO_INICIAL,
)
from demo.tabelas import ESQUEMAS
from tests.sql import cadastro_sql, plano


def test_fontes_sao_as_tabelas_do_gerador() -> None:
    texto = cadastro_sql()
    fontes = set(re.findall(r"^\s+\('([a-z_]+)',\s+(?:'\w+'|null)", texto, re.M))
    assert fontes == set(ESQUEMAS)
    assert "'demo/distribuidora-overnex/' || v.tabela || '.parquet'" in texto


def test_empresas_do_mapa() -> None:
    texto = cadastro_sql()
    for e in EMPRESAS:
        assert f"('{e.origem}'," in texto
        assert f"('{e.nome}', " in texto


def test_de_para_igual_ao_do_gerador() -> None:
    linhas = re.findall(
        r"\('(\d{6})', '([\d.]+)', '[^']*',\s+(?:'[^']*'|null(?:::text)?),\s+"
        r"(?:'(\w+)'|null(?:::text)?)\)",
        cadastro_sql(),
    )
    no_sql = {conta: (id_, grupo or None) for conta, id_, grupo in linhas}
    assert no_sql == {c.codigo: (c.id_descritivo, c.grupo) for c in CONTAS}
    assert f"<> {len(CONTAS)} then" in cadastro_sql()


def test_saldo_inicial() -> None:
    assert f"date '{INICIO_FLUXO:%Y-%m-%d}', {SALDO_INICIAL:.2f}" in cadastro_sql()


def test_contas_da_dre_no_plano() -> None:
    p = plano()
    contas = (
        {linha.conta_receita for linha in LINHAS}
        | {linha.conta_cmv for linha in LINHAS}
        | {v[0] for v in LANCAMENTOS.values()}
        | {DEPRECIACAO, DESCONTOS_CONCEDIDOS, DEVOLUCOES_VENDA, JUROS_CLIENTES, JUROS_MORA}
        | {RENDIMENTO, "110.03.04", "110.03.01", "110.03.02", "130.01.01", "200.01.01"}
        | {"130.02.01", "130.03.01", "130.03.03", "130.03.04", "120.04.04", "170.01.02"}
    )
    for conta in sorted(contas):
        assert conta in p, f"{conta} não está no plano"
        assert p[conta][2] in ("CO", "CO/CA"), f"{conta} não entra na DRE"


def test_contas_do_erp_no_plano_e_no_caixa() -> None:
    p = plano()
    for c in CONTAS:
        assert c.id_descritivo in p, f"{c.codigo}: {c.id_descritivo} não está no plano"
        _, caixa, competencia = p[c.id_descritivo]
        assert caixa in ("FCO", "FCI", "FCF"), f"{c.id_descritivo} sem classe de caixa"
        assert competencia in ("CA", "CO/CA"), f"{c.id_descritivo} não entra no caixa"
