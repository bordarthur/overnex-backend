"""Gera as 13 tabelas da demonstração para uma data de carga (`hoje`).

Tudo até a véspera de `hoje`: vendas dos dias úteis, títulos emitidos e pagos, folha dos
meses fechados, fluxo de caixa, DRE e orçamento, e o estoque de hoje. O passado é sempre o
mesmo (ver sorteio.py); o estoque é só o do dia, como no DW.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

import pyarrow as pa

from demo.cadastros import Cadastro, cadastro
from demo.calendario import inicio_mes, meses, somar_meses
from demo.estoque import LinhaEstoque, gerar_estoque
from demo.fluxo import Fluxo, LinhaFluxo, agrupar, gerar_fluxo
from demo.folha import LANCAMENTOS, LinhaFolha, folha_do_mes
from demo.modelo import CONTA, FORTALEZA, HORIZONTE, INICIO, inflacao, margem
from demo.resultado import LinhaDre, LinhaOrcamento, gerar_dre, gerar_orcamento
from demo.tabelas import ESQUEMAS, montar
from demo.titulos import TituloPagar, TituloReceber, gerar_pagar, gerar_receber
from demo.vendas import ItemVenda, Meta, gerar_metas, gerar_vendas

Linha = dict[str, Any]


@dataclass(frozen=True)
class Dados:
    hoje: date
    cadastro: Cadastro
    vendas: list[ItemVenda]
    metas: list[Meta]
    receber: list[TituloReceber]
    pagar: list[TituloPagar]
    folha: list[LinhaFolha]
    fluxo: Fluxo
    dre: list[LinhaDre]
    orcamento: list[LinhaOrcamento]
    estoque: list[LinhaEstoque]


def gerar_dados(hoje: date) -> Dados:
    cad = cadastro()
    vendas, pedidos = gerar_vendas(cad, hoje)
    metas = gerar_metas(cad, vendas, hoje)
    receber = gerar_receber(pedidos, hoje)
    pagar = gerar_pagar(cad, vendas, receber, hoje)
    fluxo = gerar_fluxo(receber, pagar, hoje)
    pagar = pagar + fluxo.retiradas
    folha = [
        linha
        for m in meses(INICIO, somar_meses(inicio_mes(hoje), -1))
        for linha in folha_do_mes(cad, m)
    ]
    dre = gerar_dre(cad, vendas, receber, pagar, fluxo.movimentos, hoje)
    orcamento = gerar_orcamento(cad, hoje)
    estoque = gerar_estoque(vendas, hoje)
    return Dados(hoje, cad, vendas, metas, receber, pagar, folha, fluxo, dre, orcamento, estoque)


def _data_br(d: date | None) -> str:
    return "" if d is None else f"{d:%d/%m/%Y}"


def linhas(dados: Dados) -> dict[str, list[Linha]]:
    """As linhas de cada tabela, como dicionários coluna → valor."""
    cad = dados.cadastro
    hoje = dados.hoje
    mes_hoje = inicio_mes(hoje)
    saida: dict[str, list[Linha]] = {}

    saida["d_bitem_pj"] = [
        {
            "en_codigoproduto": p.codigo,
            "en_nome": p.nome,
            "en_grupo": p.linha.nome,
            "en_secao": p.linha.secao,
            "en_valorunitario": round(p.preco_base * inflacao(mes_hoje), 2),
            "en_custounitario": round(
                p.preco_base
                * inflacao(mes_hoje)
                * (1 - margem(p.linha, mes_hoje) - p.ajuste_margem),
                2,
            ),
            "EMPRESA": FORTALEZA.origem,
            "id_secao": p.linha.conta_receita,
            "id_secao_cmv": p.linha.conta_cmv,
        }
        for p in cad.produtos
    ]

    def vendedor_atual(posto_id: str) -> str:
        posto = cad.posto(posto_id)
        o = posto.ocupante(mes_hoje)
        return o.codigo if o else posto.ocupantes[0].codigo

    saida["d_cliente_pj"] = [
        {
            "en_codigocliente": c.codigo,
            "en_razaosocial": c.razao,
            "en_fantasia": c.fantasia,
            "en_endereco": c.endereco,
            "en_bairro": c.bairro,
            "en_cidade": c.cidade,
            "en_UF": c.empresa.uf,
            "en_cep": c.cep,
            "en_ramo": {"Varejo": "Varejo alimentar", "Food service": "Food service"}.get(
                c.canal, "Redes"
            ),
            "en_limitecredito": cad.limites[c.codigo],
            "ven_codigo": vendedor_atual(c.posto),
            "Grupo": c.grupo,
            "en_canal": c.canal,
            "EMPRESA": c.empresa.origem,
        }
        for c in cad.clientes
        if c.inicio < hoje
    ]

    saida["d_fornecedor_pj"] = [
        {
            "en_codigofornecedor": f.codigo,
            "en_razaosocial": f.razao,
            "en_fantasia": f.fantasia,
            "en_endereco": "",
            "en_cidade": f.cidade,
            "en_uf": f.uf,
            "en_canal": f.canal,
            "EMPRESA": "",
        }
        for f in (*cad.fornecedores, *cad.servicos.values())
    ]

    vendedores: list[Linha] = []
    for posto in cad.vendedores:
        sup_posto = cad.posto(posto.supervisor) if posto.supervisor else None
        sup = sup_posto.ocupante(mes_hoje) if sup_posto else None
        for o in posto.ocupantes:
            if o.admissao > mes_hoje or o.saida <= INICIO:
                continue
            vendedores.append(
                {
                    "en_codigovendedor": o.codigo,
                    "en_nome": o.nome,
                    "en_cpf": "",
                    "en_dtentrada": _data_br(o.admissao),
                    "en_dtsaida": _data_br(o.saida if o.saida <= mes_hoje else None),
                    "Canal": posto.canal,
                    "sp_supervisor": sup.nome if sup else "",
                    "EMPRESA": posto.empresa.origem,
                }
            )
    saida["d_vendedor_pj"] = vendedores

    saida["f_estoque_pj"] = [
        {
            "en_filial": str(e.empresa.numero),
            "en_codigoproduto": e.produto,
            "en_saldoestoque": e.saldo,
            "EMPRESA": e.empresa.origem,
        }
        for e in dados.estoque
    ]

    saida["f_vendas_pj"] = [
        {
            "en_dataemissao": it.data,
            "en_numpedido": it.pedido,
            "en_codigoproduto": it.produto,
            "en_codigocliente": it.cliente,
            "en_quantidade": it.quantidade,
            "en_valortotal": it.valor,
            "en_custototal": it.custo,
            "en_codigovendedor": it.vendedor,
            "it_grupo": it.linha,
            "EMPRESA": it.empresa.origem,
        }
        for it in dados.vendas
    ]

    saida["f_metas_comercial_pj"] = [
        {
            "Data": m.mes,
            "Volume": m.volume,
            "Valor": m.valor,
            "Mix": m.mix,
            "NDeClientes": m.clientes,
            "en_vendedor": m.vendedor,
            "EMPRESA": m.empresa.origem,
        }
        for m in dados.metas
    ]

    saida["f_receitas_pj"] = [
        {
            "en_numdoc": t.numdoc,
            "en_dataemissao": t.emissao,
            "en_datavencimento": t.vencimento,
            "en_datapagamento": t.pagamento,
            "codigoplanocliente": "101001",
            "en_observacao": t.observacao,
            "en_codigovendedor": t.vendedor,
            "en_codigocliente": t.cliente,
            "en_valor": t.valor,
            "en_valorpago": t.valor_pago,
            "en_valorjuro": t.juro,
            "EMPRESA": t.empresa.origem,
        }
        for t in dados.receber
    ]

    saida["f_despesas_pj"] = [
        {
            "en_numdoc": t.numdoc,
            "en_numlancamento": t.numlanc,
            "en_datemi": t.emissao,
            "en_datven": t.vencimento,
            "en_datpag": t.pagamento,
            "en_codigoplanocliente": t.conta,
            "en_nomeplanocliente": CONTA[t.conta].nome,
            "en_observacao": t.observacao,
            "en_codigofornecedor": t.fornecedor,
            "en_valor": t.valor,
            "en_valorpago": t.valor_pago,
            "en_valorjuro": t.juro,
            "EMPRESA": t.empresa.origem,
        }
        for t in dados.pagar
    ]

    saida["f_folha_pj_cortes"] = [
        {
            "NOME": f.pessoa.nome,
            "CARGO": f.posto.cargo.nome,
            "SETOR": f.posto.cargo.setor,
            "EMPRESA": f.empresa.origem,
            "REGIME": "CLT",
            "CONTRATO": "Indeterminado",
            "Id_Funcao": f.posto.cargo.id_funcao,
            "Data": f.mes,
            "Lancamento": f.lancamento,
            "Valor": f.valor,
            "Descritivo": LANCAMENTOS[f.lancamento][1],
            "Id_Descritivo": f.conta,
        }
        for f in dados.folha
    ]

    saida["f_fluxo_pj"] = [
        _linha_fluxo(i, f) for i, f in enumerate(agrupar(dados.fluxo.movimentos))
    ]

    saida["f_lancamentos_agrupados_pj"] = [
        {"Empresa": d.empresa.origem, "Data": d.mes, "Id_Descritivo": d.conta, "Valor": d.valor}
        for d in dados.dre
    ]

    saida["f_orcamento_pj"] = [
        {
            "Data": o.mes,
            "Valor": o.valor,
            "Id_Descritivo": o.conta,
            "Cenario": "Base",
            "EMPRESA": o.empresa.origem,
        }
        for o in dados.orcamento
    ]
    return saida


def _linha_fluxo(i: int, f: LinhaFluxo) -> Linha:
    entrada = f.valor >= 0
    return {
        "Empresas": f.empresa.origem,
        "Transacao": f"{f.data:%Y%m%d}{i:06d}",
        "Banco": "Banco Mandacaru",
        "Data": f.data,
        "ValorEntrada": f.valor if entrada else 0.0,
        "ValorSaida": 0.0 if entrada else -f.valor,
        "Valor": f.valor,
        "CodContaCred": f.codigo if entrada else "",
        "CodContaDeb": "" if entrada else f.codigo,
        "Descricao": f.descricao,
        "Descricao2": "",
        "Tipo": "C" if entrada else "D",
        "Rotina": "Demonstração",
        "Moeda": "R$",
        "Estorno": "N",
        "Origem": "Demonstração",
        "CodConta": f.codigo,
        "ColunaVerificacao": "",
        "Id_LANN": f.conta,
        "Id_Descritivo": f.conta,
    }


def gerar(hoje: date) -> Mapping[str, pa.Table]:
    """As tabelas da carga de `hoje` (_data_snapshot = hoje)."""
    if not INICIO < hoje < HORIZONTE:
        raise ValueError(f"data fora do período da demonstração: {hoje:%d/%m/%Y}")
    por_tabela = linhas(gerar_dados(hoje))
    return {t: montar(t, por_tabela[t], hoje) for t in ESQUEMAS}
