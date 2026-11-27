"""As tabelas do mart que a demonstração preenche, com as colunas de cada uma.

São tabelas da PJ Cortes (migrations 12, 13, 18 e 23): a carga troca só as linhas do
cliente da demonstração, e o RLS separa os dois. As colunas e os tipos precisam ser os do
mart (o teste tests/test_esquema.py confere contra as migrations): texto → string, numeric →
double, date → date32. Todo arquivo leva _data_snapshot, como os do snapshot.
"""

from collections.abc import Iterable, Mapping
from datetime import date
from typing import Any, Literal

import pyarrow as pa

Tipo = Literal["text", "numeric", "date"]

ESQUEMAS: Mapping[str, tuple[tuple[str, Tipo], ...]] = {
    "d_bitem_pj": (
        ("en_codigoproduto", "text"),
        ("en_nome", "text"),
        ("en_grupo", "text"),
        ("en_secao", "text"),
        ("en_valorunitario", "numeric"),
        ("en_custounitario", "numeric"),
        ("EMPRESA", "text"),
        ("id_secao", "text"),
        ("id_secao_cmv", "text"),
    ),
    "d_cliente_pj": (
        ("en_codigocliente", "text"),
        ("en_razaosocial", "text"),
        ("en_fantasia", "text"),
        ("en_endereco", "text"),
        ("en_bairro", "text"),
        ("en_cidade", "text"),
        ("en_UF", "text"),
        ("en_cep", "text"),
        ("en_ramo", "text"),
        ("en_limitecredito", "numeric"),
        ("ven_codigo", "text"),
        ("Grupo", "text"),
        ("en_canal", "text"),
        ("EMPRESA", "text"),
    ),
    "d_fornecedor_pj": (
        ("en_codigofornecedor", "text"),
        ("en_razaosocial", "text"),
        ("en_fantasia", "text"),
        ("en_endereco", "text"),
        ("en_cidade", "text"),
        ("en_uf", "text"),
        ("en_canal", "text"),
        ("EMPRESA", "text"),
    ),
    "d_vendedor_pj": (
        ("en_codigovendedor", "text"),
        ("en_nome", "text"),
        ("en_cpf", "text"),
        ("en_dtentrada", "text"),
        ("en_dtsaida", "text"),
        ("Canal", "text"),
        ("sp_supervisor", "text"),
        ("EMPRESA", "text"),
    ),
    "f_estoque_pj": (
        ("en_filial", "text"),
        ("en_codigoproduto", "text"),
        ("en_saldoestoque", "numeric"),
        ("EMPRESA", "text"),
    ),
    "f_despesas_pj": (
        ("en_numdoc", "text"),
        ("en_numlancamento", "text"),
        ("en_datemi", "date"),
        ("en_datven", "date"),
        ("en_datpag", "date"),
        ("en_codigoplanocliente", "text"),
        ("en_nomeplanocliente", "text"),
        ("en_observacao", "text"),
        ("en_codigofornecedor", "text"),
        ("en_valor", "numeric"),
        ("en_valorpago", "numeric"),
        ("en_valorjuro", "numeric"),
        ("EMPRESA", "text"),
    ),
    "f_folha_pj_cortes": (
        ("NOME", "text"),
        ("CARGO", "text"),
        ("SETOR", "text"),
        ("EMPRESA", "text"),
        ("REGIME", "text"),
        ("CONTRATO", "text"),
        ("Id_Funcao", "text"),
        ("Data", "date"),
        ("Lancamento", "text"),
        ("Valor", "numeric"),
        ("Descritivo", "text"),
        ("Id_Descritivo", "text"),
    ),
    "f_fluxo_pj": (
        ("Empresas", "text"),
        ("Transacao", "text"),
        ("Banco", "text"),
        ("Data", "date"),
        ("ValorEntrada", "numeric"),
        ("ValorSaida", "numeric"),
        ("Valor", "numeric"),
        ("CodContaCred", "text"),
        ("CodContaDeb", "text"),
        ("Descricao", "text"),
        ("Descricao2", "text"),
        ("Tipo", "text"),
        ("Rotina", "text"),
        ("Moeda", "text"),
        ("Estorno", "text"),
        ("Origem", "text"),
        ("CodConta", "text"),
        ("ColunaVerificacao", "text"),
        ("Id_LANN", "text"),
        ("Id_Descritivo", "text"),
    ),
    "f_lancamentos_agrupados_pj": (
        ("Empresa", "text"),
        ("Data", "date"),
        ("Id_Descritivo", "text"),
        ("Valor", "numeric"),
    ),
    "f_metas_comercial_pj": (
        ("Data", "date"),
        ("Volume", "numeric"),
        ("Valor", "numeric"),
        ("Mix", "numeric"),
        ("NDeClientes", "numeric"),
        ("en_vendedor", "text"),
        ("EMPRESA", "text"),
    ),
    "f_orcamento_pj": (
        ("Data", "date"),
        ("Valor", "numeric"),
        ("Id_Descritivo", "text"),
        ("Cenario", "text"),
        ("EMPRESA", "text"),
    ),
    "f_receitas_pj": (
        ("en_numdoc", "text"),
        ("en_dataemissao", "date"),
        ("en_datavencimento", "date"),
        ("en_datapagamento", "date"),
        ("codigoplanocliente", "text"),
        ("en_observacao", "text"),
        ("en_codigovendedor", "text"),
        ("en_codigocliente", "text"),
        ("en_valor", "numeric"),
        ("en_valorpago", "numeric"),
        ("en_valorjuro", "numeric"),
        ("EMPRESA", "text"),
    ),
    "f_vendas_pj": (
        ("en_dataemissao", "date"),
        ("en_numpedido", "text"),
        ("en_codigoproduto", "text"),
        ("en_codigocliente", "text"),
        ("en_quantidade", "numeric"),
        ("en_valortotal", "numeric"),
        ("en_custototal", "numeric"),
        ("en_codigovendedor", "text"),
        ("it_grupo", "text"),
        ("EMPRESA", "text"),
    ),
}

TIPOS_ARROW: Mapping[Tipo, pa.DataType] = {
    "text": pa.string(),
    "numeric": pa.float64(),
    "date": pa.date32(),
}


def esquema(tabela: str) -> pa.Schema:
    return pa.schema(
        [pa.field(nome, TIPOS_ARROW[tipo]) for nome, tipo in ESQUEMAS[tabela]]
        + [pa.field("_data_snapshot", pa.date32())]
    )


def montar(tabela: str, linhas: Iterable[Mapping[str, Any]], data_snapshot: date) -> pa.Table:
    """As linhas (dicionários coluna → valor; coluna ausente = vazio) no formato do arquivo."""
    colunas = [nome for nome, _ in ESQUEMAS[tabela]]
    registros = [
        {**{c: linha.get(c) for c in colunas}, "_data_snapshot": data_snapshot} for linha in linhas
    ]
    return pa.Table.from_pylist(registros, schema=esquema(tabela))
