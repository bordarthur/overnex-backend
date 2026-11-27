"""Vendas (f_vendas_pj) e metas comerciais (f_metas_comercial_pj).

Cada dia útil, em cada filial, sai um número de pedidos em torno do faturamento esperado do
dia ÷ o valor médio do pedido. O cliente é sorteado entre os ativos, com chance maior para
os maiores; o valor do pedido cresce com o tamanho do cliente e com o desempenho do
vendedor da carteira no mês (e é menor nos primeiros meses de casa: a rampa). Os itens vêm
das linhas que o cliente compra, com o preço do produto no mês e o custo pela margem da
linha no mês.
"""

import random
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from demo.cadastros import Cadastro, Cliente, Produto
from demo.calendario import inicio_mes, meses, meses_entre, somar_meses, ultimo_dia, util
from demo.modelo import (
    EMPRESAS,
    INICIO,
    ITENS_EXTRAS_MEDIA,
    LINHAS,
    VALOR_MEDIO_PEDIDO,
    Empresa,
    fator_vendedor,
    inflacao,
    margem,
    rampa,
    receita_esperada,
    receita_esperada_dia,
)
from demo.sorteio import acumulados, lognormal, poisson, rng

DESCONTOS = (0.0, 0.0, 0.0, 0.01, 0.02, 0.03)
# meta de valor: 97% da curva esperada, onde a venda realizada fica em média (a rampa dos
# vendedores novos e os descontos a puxam para baixo): o atingimento fica perto de 100%,
# uns meses acima e outros abaixo
FOLGA_META = 0.97
# metas de clientes, volume e mix: 3% acima da média dos 3 meses anteriores
FOLGA_OUTRAS = 1.03


@dataclass(frozen=True)
class ItemVenda:
    data: date
    pedido: str
    produto: str
    cliente: str
    quantidade: float
    valor: float
    custo: float
    vendedor: str
    linha: str
    empresa: Empresa


@dataclass
class Pedido:
    numero: str
    data: date
    cliente: Cliente
    vendedor: str
    empresa: Empresa
    valor: float = 0.0
    por_linha: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class Carteira:
    """Clientes ativos de uma filial no mês, com os pesos do sorteio."""

    clientes: tuple[Cliente, ...]
    acumulados: tuple[float, ...]
    # corrige o valor do pedido para a média ficar em VALOR_MEDIO_PEDIDO
    ajuste: float


def _carteira(cad: Cadastro, empresa: Empresa, m: date) -> Carteira:
    fim = ultimo_dia(m)
    ativos = tuple(
        c for c in cad.clientes if c.empresa is empresa and c.inicio <= fim and c.fim > m
    )
    chances = [c.peso**0.6 for c in ativos]
    ajuste = sum(chances) / sum(c.peso for c in ativos)
    return Carteira(ativos, tuple(acumulados(chances)), ajuste)


def _catalogo(cad: Cadastro) -> dict[str, tuple[list[Produto], list[float]]]:
    saida: dict[str, tuple[list[Produto], list[float]]] = {}
    for linha in LINHAS:
        lista = [p for p in cad.produtos if p.linha is linha]
        saida[linha.nome] = (lista, acumulados([p.popularidade for p in lista]))
    return saida


def gerar_vendas(cad: Cadastro, ate: date) -> tuple[list[ItemVenda], list[Pedido]]:
    """Vendas dos dias úteis de INICIO até a véspera de `ate`."""
    catalogo = _catalogo(cad)
    itens: list[ItemVenda] = []
    pedidos: list[Pedido] = []
    carteiras: dict[tuple[str, date], Carteira] = {}
    d = INICIO
    while d < ate:
        if not util(d):
            d += timedelta(days=1)
            continue
        m = inicio_mes(d)
        for empresa in EMPRESAS:
            chave = (empresa.id, m)
            if chave not in carteiras:
                carteiras[chave] = _carteira(cad, empresa, m)
            carteira = carteiras[chave]
            r = rng("pedidos", empresa.id, d)
            n = poisson(r, receita_esperada_dia(d, empresa) / VALOR_MEDIO_PEDIDO)
            for i in range(n):
                cliente = r.choices(carteira.clientes, cum_weights=carteira.acumulados)[0]
                ocupante = cad.posto(cliente.posto).ocupante(m)
                if ocupante is None:
                    continue
                valor = (
                    VALOR_MEDIO_PEDIDO
                    * cliente.peso**0.4
                    * carteira.ajuste
                    * lognormal(r, 0.45)
                    * fator_vendedor(ocupante.codigo, m)
                    * rampa(meses_entre(ocupante.admissao, m))
                )
                pedido = Pedido(
                    f"{empresa.numero}{d:%y%m%d}{i + 1:03d}",
                    d,
                    cliente,
                    ocupante.codigo,
                    empresa,
                )
                itens.extend(_itens(r, pedido, valor, catalogo, m))
                if pedido.valor > 0:
                    pedidos.append(pedido)
        d += timedelta(days=1)
    return itens, pedidos


def _itens(
    r: random.Random,
    pedido: Pedido,
    valor: float,
    catalogo: dict[str, tuple[list[Produto], list[float]]],
    m: date,
) -> list[ItemVenda]:
    cliente = pedido.cliente
    k = 1 + poisson(r, ITENS_EXTRAS_MEDIA)
    partes = [r.expovariate(1) for _ in range(k)]
    total_partes = sum(partes)
    desconto = r.choice(DESCONTOS)
    saida: list[ItemVenda] = []
    for parte in partes:
        nome_linha = r.choices(cliente.linhas, weights=cliente.pesos_linhas)[0]
        produtos, acumulado = catalogo[nome_linha]
        produto = r.choices(produtos, cum_weights=acumulado)[0]
        preco = produto.preco_base * inflacao(m)
        quantidade = max(1, round(valor * parte / total_partes / preco))
        venda = round(quantidade * preco * (1 - desconto), 2)
        custo_unitario = preco * (1 - margem(produto.linha, m) - produto.ajuste_margem)
        saida.append(
            ItemVenda(
                pedido.data,
                pedido.numero,
                produto.codigo,
                cliente.codigo,
                float(quantidade),
                venda,
                round(quantidade * custo_unitario, 2),
                pedido.vendedor,
                nome_linha,
                pedido.empresa,
            )
        )
        pedido.valor = round(pedido.valor + venda, 2)
        pedido.por_linha[nome_linha] = pedido.por_linha.get(nome_linha, 0.0) + venda
    return saida


@dataclass(frozen=True)
class Meta:
    mes: date
    vendedor: str
    empresa: Empresa
    valor: float
    volume: float
    mix: float
    clientes: float


def gerar_metas(cad: Cadastro, itens: list[ItemVenda], ate: date) -> list[Meta]:
    """Meta de cada vendedor em cada mês, de INICIO ao mês de `ate`.

    Valor: o faturamento esperado da filial × a parte da carteira dele (pelo peso dos
    clientes ativos) × a rampa (FOLGA_META). Clientes, volume e mix: a média da carteira nos
    3 meses anteriores, com folga de 3% (FOLGA_OUTRAS).
    """
    # realizado por carteira (posto) e mês
    clientes_mes: dict[tuple[str, date], set[str]] = defaultdict(set)
    produtos_mes: dict[tuple[str, date], set[str]] = defaultdict(set)
    volume_mes: dict[tuple[str, date], float] = defaultdict(float)
    posto_do_cliente = {c.codigo: c.posto for c in cad.clientes}
    for it in itens:
        chave = (posto_do_cliente[it.cliente], inicio_mes(it.data))
        clientes_mes[chave].add(it.cliente)
        produtos_mes[chave].add(it.produto)
        volume_mes[chave] += it.quantidade

    metas: list[Meta] = []
    for m in meses(INICIO, ate - timedelta(days=1)):
        fim = ultimo_dia(m)
        ativos = [c for c in cad.clientes if c.inicio <= fim and c.fim > m]
        total = {e.id: sum(c.peso for c in ativos if c.empresa is e) for e in EMPRESAS}
        peso_posto: dict[str, float] = defaultdict(float)
        for c in ativos:
            peso_posto[c.posto] += c.peso
        for posto in cad.vendedores:
            ocupante = posto.ocupante(m)
            if ocupante is None or posto.desde > m:
                continue
            fator = rampa(meses_entre(ocupante.admissao, m))
            parte = peso_posto[posto.id] / total[posto.empresa.id]
            valor = receita_esperada(m, posto.empresa) * parte * fator * FOLGA_META
            anteriores = [(posto.id, somar_meses(m, -k)) for k in (1, 2, 3)]
            anteriores = [a for a in anteriores if a[1] >= INICIO]

            def media(serie: list[float], padrao: float) -> float:
                return sum(serie) / len(serie) if serie else padrao

            clientes = media([len(clientes_mes[a]) for a in anteriores], 0.0)
            mix = media([len(produtos_mes[a]) for a in anteriores], 0.0)
            volume = media([volume_mes[a] for a in anteriores], 0.0)
            if not anteriores:  # primeiro mês: o próprio mês
                atual = (posto.id, m)
                clientes, mix = len(clientes_mes[atual]), len(produtos_mes[atual])
                volume = volume_mes[atual]
            metas.append(
                Meta(
                    m,
                    ocupante.codigo,
                    posto.empresa,
                    round(valor, -2),
                    round(volume * FOLGA_OUTRAS * fator),
                    round(mix * FOLGA_OUTRAS),
                    round(clientes * FOLGA_OUTRAS),
                )
            )
    return metas
