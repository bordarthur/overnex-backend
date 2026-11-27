"""Cadastros fictícios: produtos, fornecedores, clientes, postos de trabalho e pessoas.

Tudo é sorteado até HORIZONTE, numa ordem fixa, sem olhar "hoje": o cliente C00123 e o
vendedor V0301 são sempre os mesmos. Os arquivos mostram só quem já existe na data da carga.

Nomes de pessoas e empresas são combinações inventadas; nada vem de cliente real.
"""

from dataclasses import dataclass, field
from datetime import date
from functools import cache

from demo.calendario import somar_meses
from demo.modelo import (
    EMPRESAS,
    FORTALEZA,
    HORIZONTE,
    INICIO,
    LINHAS,
    MES_BASE,
    RECIFE,
    Empresa,
    Linha,
    receita_esperada,
)
from demo.sorteio import lognormal, rng

PRENOMES = (
    "Ana", "Antônio", "Beatriz", "Bruno", "Camila", "Carlos", "Cícero", "Cláudia", "Daniel",
    "Daniela", "Davi", "Débora", "Diego", "Edson", "Elaine", "Emanuel", "Érica", "Fábio",
    "Fernanda", "Francisca", "Francisco", "Gabriel", "Gabriela", "Geovana", "Gilberto",
    "Gustavo", "Helena", "Heitor", "Igor", "Isabela", "Ítalo", "Jéssica", "João", "Joana",
    "Jorge", "José", "Josefa", "Juliana", "Júlio", "Karla", "Larissa", "Leandro", "Letícia",
    "Lucas", "Luana", "Luciana", "Luiz", "Manoel", "Marcela", "Marcos", "Maria", "Mariana",
    "Mateus", "Natália", "Nathan", "Otávio", "Patrícia", "Paulo", "Pedro", "Priscila",
    "Rafael", "Raimundo", "Raquel", "Regina", "Renata", "Ricardo", "Rita", "Roberto", "Rodrigo",
    "Samuel", "Sandra", "Sebastião", "Sérgio", "Simone", "Tatiane", "Thiago", "Valéria",
    "Vanessa", "Vinícius", "Vitória", "Wellington", "Yasmin",
)  # fmt: skip
SOBRENOMES = (
    "Albuquerque", "Alencar", "Almeida", "Alves", "Andrade", "Araújo", "Barbosa", "Barros",
    "Bezerra", "Brito", "Cabral", "Campos", "Cardoso", "Carvalho", "Castro", "Cavalcante",
    "Coelho", "Correia", "Costa", "Cruz", "Dantas", "Dias", "Duarte", "Farias", "Feitosa",
    "Ferreira", "Freitas", "Gomes", "Holanda", "Lima", "Lopes", "Macedo", "Magalhães",
    "Maia", "Marinho", "Martins", "Medeiros", "Melo", "Menezes", "Monteiro", "Moraes",
    "Moreira", "Nascimento", "Nogueira", "Oliveira", "Pereira", "Pinheiro", "Queiroz",
    "Ramos", "Rocha", "Sales", "Sampaio", "Santana", "Santos", "Silva", "Siqueira", "Soares",
    "Sousa", "Tavares", "Teixeira", "Torres", "Vasconcelos", "Viana", "Xavier",
)  # fmt: skip
NOMES_LOJA = (
    "São José", "Bom Jesus", "Esperança", "Nova Vida", "Dois Irmãos", "Boa Vista",
    "Primavera", "Central", "do Povo", "Familiar", "Estrela", "Aurora", "Progresso",
    "Bom Preço", "Economia", "União", "Santa Luzia", "São Francisco", "Nossa Senhora",
    "Paraíso", "Vitória", "Sol Nascente", "Avenida", "da Praça", "Bela Vista", "Cristo Rei",
    "Recanto", "Ideal", "Modelo", "Popular", "Rainha", "Santa Rita", "Brasil", "Sertanejo",
)  # fmt: skip
TIPOS_LOJA = {
    "Varejo": (
        "Mercadinho", "Mercantil", "Supermercado", "Mercearia", "Minibox", "Armazém",
        "Conveniência", "Padaria",
    ),
    "Food service": ("Restaurante", "Lanchonete", "Hotel", "Pousada", "Cantina", "Pizzaria"),
}  # fmt: skip
RAMO = {"Varejo": "Varejo alimentar", "Food service": "Food service", "Key account": "Redes"}
REDES = {
    "FOR": (
        "Supermercados Brisa do Mar",
        "Rede Mandacaru de Supermercados",
        "Atacarejo Sertão Forte",
        "Mercantil Jangada",
        "Supermercados Carnaúba",
        "Rede Iracema Atacarejo",
    ),
    "REC": (
        "Rede Frevo Supermercados",
        "Supermercados Maracatu",
        "Atacarejo Capibaribe",
        "Rede Boa Viagem de Supermercados",
    ),
}
# a rede de Recife que paga tudo com 35 a 55 dias de atraso (a história de "Quem cobrar")
REDE_ATRASADA = "Rede Frevo Supermercados"
RUAS = (
    "Rua das Flores", "Avenida Principal", "Rua São Pedro", "Rua do Comércio",
    "Avenida Beira Rio", "Rua Sete de Setembro", "Rua da Paz", "Travessa Boa Esperança",
    "Rua Coronel Pinto", "Avenida das Palmeiras", "Rua Padre Cícero", "Rua do Sol",
)  # fmt: skip
CIDADES_FORNECEDOR = (
    ("Fortaleza", "CE"), ("Recife", "PE"), ("Natal", "RN"), ("João Pessoa", "PB"),
    ("Teresina", "PI"), ("Salvador", "BA"), ("Campina Grande", "PB"), ("São Paulo", "SP"),
    ("Goiânia", "GO"), ("Uberlândia", "MG"),
)  # fmt: skip
PRAZOS_CLIENTE = {
    "Varejo": ((21,), (28,), (28, 35), (14, 28), (28,)),
    "Food service": ((14,), (21,), (28,)),
    "Key account": ((28, 42, 56), (30, 60), (35, 49)),
}
PRAZOS_FORNECEDOR = ((28,), (28, 42), (30, 60), (21, 35, 49), (35,))


@dataclass(frozen=True)
class Produto:
    codigo: str
    nome: str
    linha: Linha
    marca: str
    fornecedor: str
    preco_base: float
    # diferença da margem do produto para a da linha (fixa)
    ajuste_margem: float
    popularidade: float


@dataclass(frozen=True)
class Fornecedor:
    codigo: str
    fantasia: str
    razao: str
    cidade: str
    uf: str
    canal: str
    prazos: tuple[int, ...] = (28,)
    linha: str | None = None


@dataclass(frozen=True)
class Ocupante:
    """Uma pessoa num posto, de `admissao` (mês) até antes de `saida` (mês)."""

    codigo: str
    nome: str
    posto: str
    admissao: date
    saida: date
    salario_fator: float
    transporte: bool

    def ativo(self, m: date) -> bool:
        return self.admissao <= m < self.saida


@dataclass(frozen=True)
class Cargo:
    nome: str
    setor: str
    salario: float
    # tempo médio no cargo, em meses (a rotatividade)
    permanencia: int
    id_funcao: str


@dataclass
class Posto:
    id: str
    empresa: Empresa
    cargo: Cargo
    desde: date
    # só dos vendedores
    canal: str | None = None
    supervisor: str | None = None
    ocupantes: list[Ocupante] = field(default_factory=list)

    def ocupante(self, m: date) -> Ocupante | None:
        for o in self.ocupantes:
            if o.ativo(m):
                return o
        return None


@dataclass(frozen=True)
class Cliente:
    codigo: str
    fantasia: str
    razao: str
    empresa: Empresa
    cidade: str
    bairro: str
    endereco: str
    cep: str
    canal: str
    grupo: str
    # peso no faturamento (o tamanho do cliente)
    peso: float
    inicio: date
    fim: date
    posto: str
    linhas: tuple[str, ...]
    pesos_linhas: tuple[float, ...]
    prazos: tuple[int, ...]
    # pontual, tardio, ruim ou calote
    perfil: str
    atraso_fixo: tuple[int, int] | None = None
    calote: date | None = None
    recupera: bool = False

    def ativo(self, d: date) -> bool:
        return self.inicio <= d < self.fim


CARGOS = {
    c.nome: c
    for c in (
        Cargo("Vendedor externo", "Comercial", 2900, 54, "VEN01"),
        Cargo("Supervisor de vendas", "Comercial", 6800, 84, "VEN02"),
        Cargo("Gerente comercial", "Comercial", 13500, 120, "VEN03"),
        Cargo("Televendas", "Comercial", 2200, 30, "VEN04"),
        Cargo("Assistente comercial", "Comercial", 2500, 48, "VEN05"),
        Cargo("Auxiliar de armazém", "Logística", 1750, 22, "LOG01"),
        Cargo("Conferente", "Logística", 2150, 40, "LOG02"),
        Cargo("Operador de empilhadeira", "Logística", 2450, 48, "LOG03"),
        Cargo("Líder de armazém", "Logística", 3600, 84, "LOG04"),
        Cargo("Gerente de logística", "Logística", 9500, 120, "LOG05"),
        Cargo("Motorista", "Entregas", 2950, 48, "ENT01"),
        Cargo("Ajudante de entrega", "Entregas", 1650, 28, "ENT02"),
        Cargo("Comprador", "Compras", 4800, 84, "COM01"),
        Cargo("Analista financeiro", "Financeiro", 4300, 72, "FIN01"),
        Cargo("Assistente de cobrança", "Financeiro", 2400, 48, "FIN02"),
        Cargo("Gerente administrativo-financeiro", "Financeiro", 12000, 144, "FIN03"),
        Cargo("Assistente administrativo", "Administrativo", 2300, 60, "ADM01"),
        Cargo("Analista de RH", "Administrativo", 3900, 72, "ADM02"),
        Cargo("Faturista", "Administrativo", 2200, 60, "ADM03"),
        Cargo("Recepcionista", "Administrativo", 1700, 40, "ADM04"),
    )
}

# (empresa, cargo, quantidade, desde)
QUADRO: tuple[tuple[Empresa, str, int, date], ...] = (
    (FORTALEZA, "Gerente comercial", 1, INICIO),
    (FORTALEZA, "Supervisor de vendas", 2, INICIO),
    (FORTALEZA, "Televendas", 4, INICIO),
    (FORTALEZA, "Televendas", 1, date(2025, 8, 1)),
    (FORTALEZA, "Assistente comercial", 1, INICIO),
    (FORTALEZA, "Auxiliar de armazém", 14, INICIO),
    (FORTALEZA, "Conferente", 4, INICIO),
    (FORTALEZA, "Operador de empilhadeira", 3, INICIO),
    (FORTALEZA, "Líder de armazém", 1, INICIO),
    (FORTALEZA, "Gerente de logística", 1, INICIO),
    (FORTALEZA, "Motorista", 8, INICIO),
    (FORTALEZA, "Ajudante de entrega", 8, INICIO),
    (FORTALEZA, "Comprador", 2, INICIO),
    (FORTALEZA, "Analista financeiro", 2, INICIO),
    (FORTALEZA, "Assistente de cobrança", 2, INICIO),
    (FORTALEZA, "Gerente administrativo-financeiro", 1, INICIO),
    (FORTALEZA, "Assistente administrativo", 2, INICIO),
    (FORTALEZA, "Analista de RH", 1, INICIO),
    (FORTALEZA, "Faturista", 2, INICIO),
    (FORTALEZA, "Recepcionista", 1, INICIO),
    (RECIFE, "Supervisor de vendas", 1, INICIO),
    (RECIFE, "Televendas", 2, INICIO),
    (RECIFE, "Assistente comercial", 1, INICIO),
    (RECIFE, "Auxiliar de armazém", 7, INICIO),
    (RECIFE, "Auxiliar de armazém", 2, date(2026, 3, 1)),
    (RECIFE, "Conferente", 2, INICIO),
    (RECIFE, "Operador de empilhadeira", 2, INICIO),
    (RECIFE, "Líder de armazém", 1, INICIO),
    (RECIFE, "Motorista", 4, INICIO),
    (RECIFE, "Motorista", 1, date(2026, 3, 1)),
    (RECIFE, "Ajudante de entrega", 4, INICIO),
    (RECIFE, "Ajudante de entrega", 1, date(2026, 3, 1)),
    (RECIFE, "Comprador", 1, INICIO),
    (RECIFE, "Assistente de cobrança", 1, INICIO),
    (RECIFE, "Assistente administrativo", 1, INICIO),
    (RECIFE, "Faturista", 1, INICIO),
)
# vendedores: (empresa, canal, quantidade, desde)
CARTEIRAS: tuple[tuple[Empresa, str, int, date], ...] = (
    (FORTALEZA, "Varejo", 9, INICIO),
    (FORTALEZA, "Food service", 3, INICIO),
    (FORTALEZA, "Key account", 2, INICIO),
    (RECIFE, "Varejo", 5, INICIO),
    (RECIFE, "Varejo", 1, date(2026, 2, 1)),
    (RECIFE, "Food service", 2, INICIO),
    (RECIFE, "Key account", 1, INICIO),
)


@dataclass(frozen=True)
class Cadastro:
    produtos: tuple[Produto, ...]
    fornecedores: tuple[Fornecedor, ...]
    servicos: dict[str, Fornecedor]
    postos: tuple[Posto, ...]
    clientes: tuple[Cliente, ...]
    # limite de crédito por cliente (0 = sem limite)
    limites: dict[str, float]
    por_id: dict[str, Posto]

    @property
    def vendedores(self) -> tuple[Posto, ...]:
        return tuple(p for p in self.postos if p.canal is not None)

    def posto(self, id_: str) -> Posto:
        return self.por_id[id_]


def _nome_pessoa(chave: object, usados: set[str]) -> str:
    r = rng("nome", chave)
    while True:
        nome = f"{r.choice(PRENOMES)} {r.choice(SOBRENOMES)} {r.choice(SOBRENOMES)}"
        if nome not in usados and len(set(nome.split()[1:])) == 2:
            usados.add(nome)
            return nome


def _produtos_e_fornecedores() -> tuple[tuple[Produto, ...], tuple[Fornecedor, ...]]:
    produtos: list[Produto] = []
    fornecedores: list[Fornecedor] = []
    for i, linha in enumerate(LINHAS):
        for j, marca in enumerate(linha.marcas):
            r = rng("fornecedor", linha.nome, marca)
            cidade, uf = r.choice(CIDADES_FORNECEDOR)
            codigo = f"F{i + 1:02d}{j + 1}"
            fornecedores.append(
                Fornecedor(
                    codigo,
                    marca,
                    f"{marca} Indústria e Comércio Ltda",
                    cidade,
                    uf,
                    "Indústria",
                    r.choice(PRAZOS_FORNECEDOR),
                    linha.nome,
                )
            )
            for k, item in enumerate(linha.itens):
                rp = rng("produto", linha.nome, marca, item)
                if rp.random() < 0.15:  # nem toda marca tem todos os itens
                    continue
                produtos.append(
                    Produto(
                        f"{i + 1:02d}{j + 1}{k + 1:02d}",
                        f"{item.split(' (')[0]} {marca}"
                        + (f" ({item.split(' (')[1]}" if " (" in item else ""),
                        linha,
                        marca,
                        codigo,
                        round(rp.uniform(linha.preco_min, linha.preco_max), 2),
                        rp.uniform(-0.03, 0.03),
                        rp.paretovariate(1.6),
                    )
                )
    return tuple(produtos), tuple(fornecedores)


def _servicos() -> dict[str, Fornecedor]:
    lista = (
        ("S01", "Imobiliária Litoral", "Imobiliária Litoral Empreendimentos Ltda", "Aluguel"),
        ("S02", "Energia Atlântica", "Energia Atlântica Distribuidora S.A.", "Concessionária"),
        ("S03", "Águas do Litoral", "Águas do Litoral Saneamento S.A.", "Concessionária"),
        ("S04", "Conecta Nordeste", "Conecta Nordeste Telecomunicações Ltda", "Serviços"),
        ("S05", "Sistema Gestor", "Sistema Gestor Software Ltda", "Serviços"),
        ("S06", "Contábil Precisão", "Contábil Precisão Assessoria Ltda", "Serviços"),
        ("S07", "Vigilância Sentinela", "Vigilância Sentinela Segurança Ltda", "Serviços"),
        ("S08", "Papelaria Central", "Papelaria Central Atacado Ltda", "Materiais"),
        ("S09", "Seguradora Horizonte", "Seguradora Horizonte S.A.", "Serviços"),
        ("S10", "Agência Xique-Xique", "Agência Xique-Xique Comunicação Ltda", "Serviços"),
        ("S11", "Auto Posto Carcará", "Auto Posto Carcará Ltda", "Combustível"),
        ("S12", "Transportes Rota Nordeste", "Transportes Rota Nordeste Ltda", "Transporte"),
        ("S13", "Banco Mandacaru", "Banco Mandacaru S.A.", "Banco"),
        ("S14", "Oficina Diesel Forte", "Oficina Diesel Forte Ltda", "Serviços"),
        ("S15", "Fazenda estadual (ICMS)", "Fazenda estadual - ICMS", "Tributos"),
        ("S16", "Fazenda federal", "Fazenda federal - PIS, COFINS, IRPJ e CSLL", "Tributos"),
        ("S17", "Folha de pagamento", "Colaboradores - folha de pagamento", "Folha"),
        ("S18", "FGTS", "Fundo de Garantia do Tempo de Serviço", "Tributos"),
        ("S19", "INSS", "Previdência Social - INSS", "Tributos"),
        ("S20", "Benefícios Alimenta Mais", "Alimenta Mais Benefícios Ltda", "Benefícios"),
        ("S21", "Saúde Integral", "Saúde Integral Planos de Saúde S.A.", "Benefícios"),
        ("S22", "Nordeste Caminhões", "Nordeste Caminhões e Veículos Ltda", "Veículos"),
        ("S23", "Paraíso Equipamentos", "Paraíso Equipamentos Logísticos Ltda", "Máquinas"),
        ("S24", "Sócios", "Retirada dos sócios", "Sócios"),
    )
    return {c: Fornecedor(c, f, r, "Fortaleza", "CE", canal) for c, f, r, canal in lista}


def _ocupantes(posto: Posto, numero: int, usados: set[str]) -> list[Ocupante]:
    saida: list[Ocupante] = []
    k = 0
    r = rng("posto", posto.id)
    if posto.desde <= INICIO:
        admissao = somar_meses(INICIO, -r.randint(1, 96))
        restante = max(1, round(r.expovariate(1 / posto.cargo.permanencia)))
        fim = somar_meses(INICIO, restante)
    else:
        admissao = posto.desde
        fim = somar_meses(admissao, max(3, round(r.expovariate(1 / posto.cargo.permanencia))))
    while admissao < HORIZONTE:
        ro = rng("ocupante", posto.id, k)
        nome = _nome_pessoa((posto.id, k), usados)
        codigo = f"V{numero:02d}{k + 1:02d}" if posto.canal else f"P{numero:03d}{k + 1:02d}"
        saida.append(
            Ocupante(
                codigo,
                nome,
                posto.id,
                admissao,
                fim,
                ro.uniform(0.92, 1.10),
                ro.random() < 0.55,
            )
        )
        k += 1
        # vendedor sai e outro entra no mesmo mês; nos outros postos, até 1 mês vago
        vago = 0 if posto.canal else ro.choice((0, 0, 1))
        admissao = somar_meses(fim, vago)
        duracao = max(3, round(ro.expovariate(1 / posto.cargo.permanencia)))
        fim = somar_meses(admissao, duracao)
    return saida


def _postos() -> tuple[Posto, ...]:
    postos: list[Posto] = []
    usados: set[str] = set()
    numero = 0
    supervisores: dict[str, list[str]] = {}
    for empresa, cargo, qtd, desde in QUADRO:
        for _ in range(qtd):
            numero += 1
            p = Posto(f"{empresa.id}-{numero:03d}", empresa, CARGOS[cargo], desde)
            p.ocupantes.extend(_ocupantes(p, numero, usados))
            postos.append(p)
            if cargo in ("Supervisor de vendas", "Gerente comercial"):
                supervisores.setdefault(empresa.id, []).append(p.id)
    vendedor = 0
    for empresa, canal, qtd, desde in CARTEIRAS:
        for _ in range(qtd):
            vendedor += 1
            numero += 1
            # Fortaleza: o gerente cuida das redes; os 2 supervisores dividem varejo e food
            sups = supervisores[empresa.id]
            sup = sups[0]
            if empresa is FORTALEZA and canal != "Key account":
                sup = sups[1] if vendedor % 2 else sups[2]
            p = Posto(
                f"{empresa.id}-V{vendedor:02d}",
                empresa,
                CARGOS["Vendedor externo"],
                desde,
                canal,
                sup,
            )
            p.ocupantes.extend(_ocupantes(p, vendedor, usados))
            postos.append(p)
    return tuple(postos)


def _cliente(
    codigo: str,
    empresa: Empresa,
    inicio: date,
    carteiras: dict[tuple[str, str], list[Posto]],
    redes_usadas: dict[str, int],
    nomes_usados: set[str],
) -> Cliente:
    r = rng("cliente", codigo)
    sorteio = r.random()
    canal = "Key account" if sorteio < 0.012 else "Food service" if sorteio < 0.24 else "Varejo"
    cidade = r.choices(empresa.cidades, weights=[c.peso for c in empresa.cidades])[0]
    bairro = r.choice(cidade.bairros)
    sobrenome1, sobrenome2 = r.sample(SOBRENOMES, 2)
    atraso: tuple[int, int] | None = None
    if canal == "Key account":
        n = redes_usadas.get(empresa.id, 0)
        redes_usadas[empresa.id] = n + 1
        lista = REDES[empresa.id]
        fantasia = lista[n] if n < len(lista) else f"Supermercados {sobrenome1}"
        razao = f"{fantasia} S.A." if n < len(lista) else f"{sobrenome1} Supermercados S.A."
        peso = 10.0 * lognormal(r, 0.4)
        if fantasia == REDE_ATRASADA:
            atraso = (35, 55)
            peso = 14.0
    else:
        tipo = r.choice(TIPOS_LOJA[canal])
        nome = r.choice(NOMES_LOJA) if r.random() < 0.6 else sobrenome1
        fantasia = f"{tipo} {nome}"
        ramo = "Comércio de Alimentos" if canal == "Varejo" else "Restaurante e Alimentação"
        razao = f"{sobrenome1} & {sobrenome2} {ramo} Ltda"
        peso = (0.6 if canal == "Food service" else 1.0) * lognormal(r, 0.9)
    # nome repetido: com o bairro e, se ainda repetir, com a cidade
    for candidato in (fantasia, f"{fantasia} - {bairro}", f"{fantasia} - {bairro}, {cidade.nome}"):
        if candidato not in nomes_usados:
            fantasia = candidato
            break
    else:
        fantasia = f"{fantasia} ({codigo})"
    nomes_usados.add(fantasia)
    grupo = fantasia if canal == "Key account" else ""
    # linhas que o cliente compra: as maiores quase sempre; as menores às vezes. O peso da
    # linha é a participação dividida pela chance de o cliente comprá-la, para a soma de
    # todos os clientes dar a participação de cada linha (sem isso, as grandes dominam)
    linhas: list[str] = []
    pesos: list[float] = []
    for linha in LINHAS:
        chance = min(0.97, 0.35 + linha.participacao * 5)
        sorteada = chance
        if canal == "Food service" and linha.secao == "Não alimentos":
            sorteada *= 0.4
        if canal == "Key account" or r.random() < sorteada:
            linhas.append(linha.nome)
            pesos.append(linha.participacao / chance * r.uniform(0.6, 1.4))
    if not linhas:
        linhas, pesos = [LINHAS[0].nome], [1.0]
    perfil_sorteio = r.random()
    if canal == "Key account":
        perfil = "tardio" if perfil_sorteio < 0.25 or atraso else "pontual"
    else:
        perfil = (
            "pontual"
            if perfil_sorteio < 0.78
            else "tardio"
            if perfil_sorteio < 0.94
            else "ruim"
            if perfil_sorteio < 0.98
            else "calote"
        )
    vida = r.expovariate(1 / (240 if canal == "Key account" else 70))
    fim = somar_meses(max(inicio, INICIO), max(2, round(vida)))
    calote: date | None = None
    if perfil == "calote":
        calote = somar_meses(max(inicio, INICIO), r.randint(3, 30)).replace(day=r.randint(1, 28))
        fim = min(fim, calote)
    postos = carteiras[(empresa.id, canal)]
    abertos = [p for p in postos if p.desde <= max(inicio, INICIO)] or postos
    return Cliente(
        codigo,
        fantasia,
        razao,
        empresa,
        cidade.nome,
        bairro,
        f"{r.choice(RUAS)}, {r.randint(10, 2500)}",
        f"{cidade.cep}{r.randint(0, 999):03d}-{r.randint(0, 999):03d}",
        canal,
        grupo,
        peso,
        inicio,
        fim,
        r.choice(abertos).id,
        tuple(linhas),
        tuple(pesos),
        r.choice(PRAZOS_CLIENTE[canal]),
        perfil,
        atraso,
        calote,
        r.random() < 0.4,
    )


def _clientes(postos: tuple[Posto, ...]) -> tuple[Cliente, ...]:
    carteiras: dict[tuple[str, str], list[Posto]] = {}
    for p in postos:
        if p.canal:
            carteiras.setdefault((p.empresa.id, p.canal), []).append(p)
    clientes: list[Cliente] = []
    redes: dict[str, int] = {}
    nomes: set[str] = set()
    n = 0
    iniciais = {"FOR": 690, "REC": 360}
    for empresa in EMPRESAS:
        for _ in range(iniciais[empresa.id]):
            n += 1
            r = rng("inicio cliente", n)
            inicio = somar_meses(INICIO, -r.randint(1, 120))
            clientes.append(_cliente(f"C{n:05d}", empresa, inicio, carteiras, redes, nomes))
    m = INICIO
    while m < HORIZONTE:
        for empresa in EMPRESAS:
            novos = {"FOR": 9, "REC": 7}[empresa.id]
            for _ in range(rng("novos clientes", empresa.id, m).randint(novos - 3, novos + 3)):
                n += 1
                dia = rng("dia cliente", n).randint(1, 28)
                clientes.append(
                    _cliente(f"C{n:05d}", empresa, m.replace(day=dia), carteiras, redes, nomes)
                )
        m = somar_meses(m, 1)
    return tuple(clientes)


def _limites(clientes: tuple[Cliente, ...]) -> dict[str, float]:
    """Limite de crédito de ~60% dos clientes: ~1,1 mês da compra esperada."""
    soma = {
        e.id: sum(c.peso for c in clientes if c.empresa is e and c.ativo(MES_BASE))
        for e in EMPRESAS
    }
    limites: dict[str, float] = {}
    for c in clientes:
        if rng("limite", c.codigo).random() < 0.6:
            mensal = receita_esperada(MES_BASE, c.empresa) * c.peso / soma[c.empresa.id]
            limites[c.codigo] = max(3000.0, round(mensal * 1.1, -3))
        else:
            limites[c.codigo] = 0.0
    return limites


@cache
def cadastro() -> Cadastro:
    produtos, fornecedores = _produtos_e_fornecedores()
    postos = _postos()
    clientes = _clientes(postos)
    return Cadastro(
        produtos,
        fornecedores,
        _servicos(),
        postos,
        clientes,
        _limites(clientes),
        {p.id: p for p in postos},
    )
