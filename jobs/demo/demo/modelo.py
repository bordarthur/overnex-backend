"""O negócio fictício: filiais, linhas de produto, contas do ERP e as curvas do faturamento.

Distribuidora Overnex: atacadista de alimentos, bebidas, limpeza e higiene no Nordeste, com
a matriz em Fortaleza e uma filial em Recife. Fatura perto de R$ 9 milhões por mês, com
margem bruta de ~31% sobre o preço e EBITDA de 4% a 6%.

O que está aqui e no cadastro de produção precisa bater
(supabase/producao/distribuidora_overnex_cadastro.sql): os valores de EMPRESA (mapa de
empresas), as contas do ERP (de-para da DRE e grupos de pagamento) e o saldo inicial. O
teste tests/test_cadastro_sql.py confere.
"""

import math
from dataclasses import dataclass
from datetime import date
from functools import cache

from demo.calendario import dias_uteis, meses_entre, somar_meses
from demo.sorteio import rng

# primeiro mês das vendas, dos títulos e da folha
INICIO = date(2024, 9, 1)
# primeiro dia do fluxo de caixa: o saldo inicial vale no começo dele
INICIO_FLUXO = date(2024, 11, 1)
SALDO_INICIAL = 1_900_000.00
# as pessoas e os vendedores são sorteados até aqui, sempre: o sorteio não depende de "hoje"
HORIZONTE = date(2040, 1, 1)
# mês 0 das curvas de crescimento
MES_BASE = date(2025, 1, 1)


@dataclass(frozen=True)
class Cidade:
    nome: str
    peso: float
    cep: str
    bairros: tuple[str, ...]


@dataclass(frozen=True)
class Empresa:
    id: str
    numero: int
    # valor da coluna EMPRESA nos arquivos (cfg.mapa_empresa.empresa_origem)
    origem: str
    # cfg.empresa.nome
    nome: str
    uf: str
    # faturamento esperado no MES_BASE e crescimento ao mês
    faturamento_base: float
    crescimento: float
    cidades: tuple[Cidade, ...]


FORTALEZA = Empresa(
    "FOR",
    1,
    "OVERNEX FORTALEZA",
    "Overnex Fortaleza",
    "CE",
    5_900_000.0,
    0.007,
    (
        Cidade(
            "Fortaleza",
            0.62,
            "60",
            (
                "Aldeota",
                "Messejana",
                "Parangaba",
                "Montese",
                "Benfica",
                "Barra do Ceará",
                "Conjunto Ceará",
                "Jangurussu",
                "Maraponga",
                "Papicu",
                "Bom Jardim",
                "Antônio Bezerra",
            ),
        ),
        Cidade("Caucaia", 0.12, "61", ("Centro", "Jurema", "Icaraí", "Tabapuá")),
        Cidade("Maracanaú", 0.10, "61", ("Centro", "Jereissati", "Pajuçara", "Timbó")),
        Cidade("Sobral", 0.08, "62", ("Centro", "Junco", "Dom Expedito", "Expectativa")),
        Cidade("Juazeiro do Norte", 0.08, "63", ("Centro", "Triângulo", "Pirajá", "Salesianos")),
    ),
)
RECIFE = Empresa(
    "REC",
    2,
    "OVERNEX RECIFE",
    "Overnex Recife",
    "PE",
    3_100_000.0,
    0.011,
    (
        Cidade(
            "Recife",
            0.55,
            "50",
            (
                "Boa Viagem",
                "Casa Amarela",
                "Afogados",
                "Imbiribeira",
                "Várzea",
                "Encruzilhada",
                "Ibura",
                "Torre",
                "Cordeiro",
            ),
        ),
        Cidade("Jaboatão dos Guararapes", 0.17, "54", ("Piedade", "Candeias", "Cavaleiro")),
        Cidade("Olinda", 0.12, "53", ("Casa Caiada", "Rio Doce", "Peixinhos")),
        Cidade("Paulista", 0.08, "53", ("Janga", "Pau Amarelo", "Maranguape")),
        Cidade("Caruaru", 0.08, "55", ("Centro", "Maurício de Nassau", "Universitário")),
    ),
)
EMPRESAS = (FORTALEZA, RECIFE)


@dataclass(frozen=True)
class Linha:
    """Linha de produto (it_grupo), com as contas da receita e do CMV no plano da LANN."""

    nome: str
    secao: str
    conta_receita: str
    conta_cmv: str
    participacao: float
    # margem bruta sobre o preço, antes dos impostos
    margem: float
    preco_min: float
    preco_max: float
    itens: tuple[str, ...]
    marcas: tuple[str, ...]


LINHAS = (
    Linha(
        "Grãos e cereais",
        "Alimentos",
        "100.01.45",
        "120.01.89",
        0.16,
        0.24,
        60,
        190,
        (
            "Arroz branco tipo 1 5 kg (fardo 6 un.)",
            "Arroz parboilizado 5 kg (fardo 6 un.)",
            "Feijão carioca 1 kg (fardo 10 un.)",
            "Feijão de corda 1 kg (fardo 10 un.)",
            "Feijão preto 1 kg (fardo 10 un.)",
            "Açúcar cristal 2 kg (fardo 10 un.)",
            "Açúcar refinado 1 kg (fardo 10 un.)",
            "Flocão de milho 500 g (fardo 20 un.)",
            "Farinha de mandioca 1 kg (fardo 10 un.)",
            "Fubá de milho 1 kg (fardo 10 un.)",
        ),
        ("Grão Nobre", "Sertão Dourado", "Boa Colheita", "Vale do Jaguaribe"),
    ),
    Linha(
        "Massas",
        "Alimentos",
        "100.01.41",
        "120.01.69",
        0.07,
        0.32,
        35,
        110,
        (
            "Macarrão espaguete 500 g (fardo 20 un.)",
            "Macarrão parafuso 500 g (fardo 20 un.)",
            "Macarrão instantâneo 80 g (caixa 50 un.)",
            "Lasanha 500 g (caixa 12 un.)",
            "Macarrão de arroz 500 g (fardo 12 un.)",
        ),
        ("Massas Aurora", "Massas Jangada", "Trigo Bom", "Massa Fina"),
    ),
    Linha(
        "Biscoitos",
        "Alimentos",
        "100.01.02",
        "120.01.05",
        0.08,
        0.35,
        40,
        120,
        (
            "Biscoito cream cracker 400 g (caixa 20 un.)",
            "Biscoito maria 400 g (caixa 20 un.)",
            "Biscoito recheado chocolate 140 g (caixa 48 un.)",
            "Biscoito de polvilho 100 g (caixa 30 un.)",
            "Rosquinha de coco 400 g (caixa 20 un.)",
        ),
        ("Crocante", "Fornalha", "Delícia do Norte", "Casa do Biscoito"),
    ),
    Linha(
        "Café",
        "Alimentos",
        "100.01.124",
        "120.01.110",
        0.06,
        0.28,
        90,
        260,
        (
            "Café torrado e moído 250 g (fardo 20 un.)",
            "Café torrado e moído 500 g (fardo 10 un.)",
            "Café extraforte 250 g (fardo 20 un.)",
            "Café solúvel 100 g (caixa 24 un.)",
        ),
        ("Grão do Cariri", "Aroma do Sertão", "Cafezal Ibiapaba", "Torra Fina"),
    ),
    Linha(
        "Enlatados e conservas",
        "Alimentos",
        "100.01.06",
        "120.01.16",
        0.06,
        0.34,
        45,
        160,
        (
            "Sardinha em óleo 125 g (caixa 50 un.)",
            "Milho verde 200 g (caixa 24 un.)",
            "Ervilha 200 g (caixa 24 un.)",
            "Extrato de tomate 340 g (caixa 24 un.)",
            "Atum ralado 170 g (caixa 24 un.)",
            "Leite condensado 395 g (caixa 27 un.)",
        ),
        ("Mar Azul", "Horta Viva", "Sabor da Terra", "Conserva Real"),
    ),
    Linha(
        "Embutidos e frios",
        "Alimentos",
        "100.01.05",
        "120.01.14",
        0.08,
        0.32,
        50,
        220,
        (
            "Salsicha 3 kg (caixa 4 un.)",
            "Linguiça calabresa 2,5 kg (caixa 4 un.)",
            "Mortadela 1 kg (caixa 8 un.)",
            "Presunto fatiado 1 kg (caixa 6 un.)",
            "Queijo coalho 1 kg (caixa 6 un.)",
        ),
        ("Frigorífico Serrano", "Campo Bom", "Sabor Nordestino", "Fazenda Velha"),
    ),
    Linha(
        "Laticínios",
        "Alimentos",
        "100.01.08",
        "120.01.22",
        0.10,
        0.29,
        45,
        180,
        (
            "Leite UHT integral 1 L (caixa 12 un.)",
            "Leite em pó 400 g (caixa 24 un.)",
            "Manteiga 200 g (caixa 24 un.)",
            "Margarina 500 g (caixa 12 un.)",
            "Iogurte 1 L (caixa 6 un.)",
            "Requeijão 200 g (caixa 24 un.)",
        ),
        ("Leite Bom", "Vaca Malhada", "Laticínios Sertânia", "Campo Verde"),
    ),
    Linha(
        "Bebidas",
        "Bebidas",
        "100.01.44",
        "120.01.04",
        0.15,
        0.27,
        25,
        140,
        (
            "Refrigerante 2 L (fardo 6 un.)",
            "Refrigerante lata 350 ml (fardo 12 un.)",
            "Água mineral 500 ml (fardo 12 un.)",
            "Suco pronto 1 L (caixa 12 un.)",
            "Néctar de caju 1 L (caixa 12 un.)",
            "Energético 473 ml (fardo 6 un.)",
            "Água de coco 1 L (caixa 12 un.)",
        ),
        ("Frescor", "Brisa", "Tropical do Nordeste", "Fonte Clara"),
    ),
    Linha(
        "Limpeza",
        "Não alimentos",
        "100.01.10",
        "120.01.26",
        0.09,
        0.38,
        40,
        150,
        (
            "Detergente líquido 500 ml (caixa 24 un.)",
            "Sabão em pó 1 kg (caixa 20 un.)",
            "Água sanitária 1 L (caixa 12 un.)",
            "Desinfetante 2 L (caixa 6 un.)",
            "Amaciante 2 L (caixa 6 un.)",
            "Esponja multiuso (caixa 60 un.)",
        ),
        ("Brilho Total", "Casa Limpa", "Lavanda Nordeste", "Clarinha"),
    ),
    Linha(
        "Higiene pessoal",
        "Não alimentos",
        "100.01.07",
        "120.01.20",
        0.08,
        0.42,
        45,
        200,
        (
            "Papel higiênico folha dupla (fardo 16 rolos)",
            "Sabonete 85 g (caixa 72 un.)",
            "Creme dental 90 g (caixa 72 un.)",
            "Xampu 350 ml (caixa 12 un.)",
            "Desodorante aerossol (caixa 12 un.)",
            "Fralda descartável M (fardo 4 pacotes)",
        ),
        ("Suave", "Bem Cuidar", "Pele de Seda", "Algodão Doce"),
    ),
    Linha(
        "Bomboniere",
        "Alimentos",
        "100.01.49",
        "120.01.75",
        0.04,
        0.40,
        30,
        120,
        (
            "Bala de goma (pacote 600 g)",
            "Pirulito (pacote 50 un.)",
            "Chocolate ao leite 20 g (caixa 50 un.)",
            "Paçoca (pote 50 un.)",
            "Chiclete (caixa 100 un.)",
        ),
        ("Doce Lar", "Docemania", "Cacau do Sul", "Festa"),
    ),
    Linha(
        "Descartáveis",
        "Não alimentos",
        "100.01.52",
        "120.01.78",
        0.03,
        0.37,
        25,
        110,
        (
            "Copo descartável 200 ml (caixa 2.500 un.)",
            "Prato descartável (caixa 500 un.)",
            "Guardanapo (fardo 50 pacotes)",
            "Saco de lixo 100 L (fardo 100 un.)",
            "Marmitex alumínio (caixa 100 un.)",
        ),
        ("Prático", "Útil", "Plastinor", "Eco Pack"),
    ),
)
LINHA_POR_NOME = {linha.nome: linha for linha in LINHAS}

# sazonalidade do faturamento por mês do ano (dezembro forte; janeiro e fevereiro fracos)
SAZONALIDADE = {
    1: 0.92,
    2: 0.93,
    3: 1.00,
    4: 0.98,
    5: 1.00,
    6: 1.01,
    7: 1.03,
    8: 1.00,
    9: 0.99,
    10: 1.03,
    11: 1.05,
    12: 1.13,
}
# peso de cada dia da semana nos pedidos (segunda forte, sexta fraca)
DIA_DA_SEMANA = (1.15, 1.05, 1.0, 0.95, 0.85)

# valor médio do pedido (R$) e itens por pedido (1 + Poisson)
VALOR_MEDIO_PEDIDO = 10_500.0
ITENS_EXTRAS_MEDIA = 2.5

# impostos e despesas sobre a receita bruta
ICMS = 0.070
PIS = 0.009
COFINS = 0.041
IRPJ_CSLL = 0.009
DEVOLUCOES = 0.008
DESCONTOS = 0.003
COMISSAO = 0.022
COMBUSTIVEL = 0.009
FRETE_TERCEIROS = 0.005
FRETE_COMPRAS = 0.004
TARIFA_BOLETO = 2.40
INFLACAO_ANUAL = 0.045
# reajuste dos salários em março (dissídio)
DISSIDIO = 0.05


@dataclass(frozen=True)
class ContaErp:
    """Conta do plano do ERP fictício (en_codigoplanocliente) e o de-para dela."""

    codigo: str
    nome: str
    id_descritivo: str
    # cfg.conta_dre.grupo_pagamento; None = fora dos grupos do cartão
    grupo: str | None = None
    # cfg.conta_dre.fora_da_dre (a demo usa os lançamentos agrupados: só vale sem eles)
    fora_da_dre: str | None = None


CONTAS = (
    ContaErp(
        "201001",
        "Compra de mercadorias para revenda",
        "120.01.01",
        "fornecedores",
        "o CMV entra pelas vendas",
    ),
    ContaErp("201002", "Frete sobre compras", "120.04.04", "fornecedores"),
    ContaErp("301001", "Salários", "140.01.01", "custos"),
    ContaErp("301002", "FGTS", "140.01.05", "custos"),
    ContaErp("301003", "INSS", "140.01.04", "custos"),
    ContaErp("301004", "Vale-alimentação e transporte", "140.01.07", "custos"),
    ContaErp("301005", "Plano de saúde", "140.01.16", "custos"),
    ContaErp("301006", "13º salário", "140.01.02", "custos"),
    ContaErp("301007", "Férias", "140.01.03", "custos"),
    ContaErp("301008", "Rescisões", "140.01.11", "custos"),
    ContaErp("401001", "ICMS", "110.03.04", "custos"),
    ContaErp("401002", "PIS", "110.03.01", "custos"),
    ContaErp("401003", "COFINS", "110.03.02", "custos"),
    ContaErp("401004", "IRPJ e CSLL", "200.01.01", "custos"),
    ContaErp("501001", "Aluguel", "140.02.01", "custos"),
    ContaErp("501002", "Energia elétrica", "140.02.02", "custos"),
    ContaErp("501003", "Água e esgoto", "140.02.03", "custos"),
    ContaErp("501004", "Telefone e internet", "140.02.04", "custos"),
    ContaErp("501005", "Sistema ERP", "140.02.09", "custos"),
    ContaErp("501006", "Contabilidade", "140.06.02", "custos"),
    ContaErp("501007", "Segurança e monitoramento", "140.02.11", "custos"),
    ContaErp("501008", "Material de escritório", "140.02.07", "custos"),
    ContaErp("501009", "Seguros", "140.02.18", "custos"),
    ContaErp("501010", "Publicidade", "140.04.04", "custos"),
    ContaErp("502001", "Comissões de vendedores", "130.01.01", "custos"),
    ContaErp("502002", "Combustível da frota", "130.03.01", "custos"),
    ContaErp("502003", "Fretes de entrega terceirizados", "130.03.04", "custos"),
    ContaErp("502004", "Tarifas de boleto", "130.02.01", "custos"),
    ContaErp("502005", "Manutenção da frota", "130.03.03", "custos"),
    ContaErp("601001", "Juros de empréstimos", "170.01.05", "divida"),
    ContaErp("601002", "Amortização de empréstimos", "400.02.01", "divida"),
    ContaErp("601003", "Tarifas bancárias", "170.01.02", "custos"),
    ContaErp("602001", "Empréstimos tomados", "410.01.02"),
    ContaErp("701001", "Veículos", "420.02.02", "capex"),
    ContaErp("701002", "Máquinas e equipamentos", "420.02.01", "capex"),
    ContaErp("801001", "Retirada de sócios", "210.01.01"),
)
CONTA = {c.codigo: c for c in CONTAS}

# contas do plano que só aparecem no fluxo de caixa e na DRE (sem título)
JUROS_CLIENTES = "160.01.01"
RENDIMENTO = "160.01.03"
JUROS_MORA = "170.01.07"
DEPRECIACAO = "150.01.01"
DESCONTOS_CONCEDIDOS = "110.01.01"
DEVOLUCOES_VENDA = "110.02.01"


def indice(m: date) -> int:
    """Meses desde MES_BASE (negativo antes dele)."""
    return meses_entre(MES_BASE, m)


def inflacao(m: date) -> float:
    """Índice de preços do mês, 1 no MES_BASE."""
    return float((1 + INFLACAO_ANUAL) ** (indice(m) / 12))


def receita_esperada(m: date, empresa: Empresa) -> float:
    """Faturamento esperado do mês na empresa (a curva; o realizado sai dos pedidos)."""
    tendencia = (1 + empresa.crescimento) ** indice(m)
    return empresa.faturamento_base * tendencia * SAZONALIDADE[m.month]


def receita_esperada_dia(d: date, empresa: Empresa) -> float:
    m = d.replace(day=1)
    uteis = dias_uteis(m)
    pesos = sum(DIA_DA_SEMANA[u.weekday()] for u in uteis)
    return receita_esperada(m, empresa) * DIA_DA_SEMANA[d.weekday()] / pesos


@cache
def _desvios(chave: str, desvio: float, retorno: float, limite: float) -> dict[date, float]:
    """Passeio aleatório mensal com volta à média, de INICIO a HORIZONTE.

    O desvio de cada mês depende só do anterior e do sorteio do mês: o passado nunca muda.
    """
    saida: dict[date, float] = {}
    valor = 0.0
    m = INICIO
    while m < HORIZONTE:
        passo = rng("passeio", chave, m).gauss(0, desvio)
        valor = max(-limite, min(limite, valor * retorno + passo))
        saida[m] = valor
        m = somar_meses(m, 1)
    return saida


def margem(linha: Linha, m: date) -> float:
    """Margem bruta da linha no mês: a de base, com um passeio de até ±4 p.p."""
    return linha.margem + _desvios(f"margem:{linha.nome}", 0.007, 0.85, 0.04)[m]


def fator_vendedor(codigo: str, m: date) -> float:
    """Desempenho do vendedor no mês (multiplica o valor dos pedidos dele), de 0,82 a 1,22."""
    return math.exp(_desvios(f"vendedor:{codigo}", 0.045, 0.8, 0.2)[m])


# fração do ritmo pleno nos primeiros meses de casa do vendedor
RAMPA = (0.45, 0.6, 0.72, 0.82, 0.9, 0.96)


def rampa(meses_de_casa: int) -> float:
    return RAMPA[meses_de_casa] if 0 <= meses_de_casa < len(RAMPA) else 1.0
