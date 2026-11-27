"""Catálogo fechado dos painéis que a Gestão liga e desliga por cliente.

"Configuração, não personalização" (mockup Gestão M2.1.0): a BORD só liga ou desliga painéis
deste catálogo; dentro de cada painel, o cartão sem dado some sozinho (os montadores de
app/telas já devolvem só o que tem dado).

Só entram painéis que existem na API. Do mockup ficam fora Carteira de clientes e Compras
(módulo Distribuidora), que ainda não têm tela; a FIN-01 (tela antiga de lançamentos)
existe mas não é painel do catálogo.

Camada = seção de painéis do menu (Comercial, Financeiro, Pessoas, Estoque), desde
27/11/2026; as abas Implantação e Painéis da Gestão se organizam por ela. `PAINEIS` está na
ordem do menu: seção a seção e, dentro dela, como o menu mostra. O nível de cada painel
(`NIVEIS`: Universal Básico, Universal Completo, Módulo Distribuidora) vai como etiqueta ao
lado dele. Universal Básico sobe só com as tabelas do ERP; Universal Completo depende do plano
de contas, do de-para, do orçamento e do saldo inicial.

Padrão da matriz "o que cada perfil vê" (aplicado quando o painel é ligado pela primeira vez
no cliente): Cliente principal vê tudo; Consultor não vê Pessoas; Cliente usuário não vê
Pessoas, Resultado nem Fluxo de caixa.

Setor (migrations 34 e 35): painel ou camada com `setores` só existe nas empresas com
alguma unidade desses setores (app/seguranca/setores.py). Só o Estoque (EST-01, seção Estoque,
nível Módulo Distribuidora) é de um setor; os outros valem em todos.

Dado do painel: `exige` são prefixos de tabela do mart (cfg.fonte_carga.tabela_destino); o
painel está disponível se alguma fonte ativa com um desses prefixos já teve carga `ok`.
REGRA PROVISÓRIA: é a fonte principal de cada painel, tirada dos montadores (vendas.py,
paineis/base.py, receber_pagar.py, pessoas.py, resultado.py, fluxo_caixa.py); se um painel
passar a ler outra fonte, ajuste aqui. `usa` é só para a coluna "Usada por" da aba
Prontidão dos dados.
"""

from dataclasses import dataclass

from app.seguranca.perfis import CLIENTE_PRINCIPAL, CLIENTE_USUARIO, CONSULTOR
from app.seguranca.setores import DISTRIBUIDORA, setores_da_tela
from app.telas.paineis.router import CODIGOS, NOMES, NOMINAL
from app.telas.visao_geral.router import CODIGO_TELA as CODIGO_VISAO_GERAL


@dataclass(frozen=True)
class Nivel:
    id: str
    rotulo: str


BASICO = "basico"
COMPLETO = "completo"
MODULO_DISTRIBUIDORA = "distribuidora"

# na ordem: o front dá a cor da etiqueta pela posição (azul, roxo e cinza)
NIVEIS: tuple[Nivel, ...] = (
    Nivel(BASICO, "Universal Básico"),
    Nivel(COMPLETO, "Universal Completo"),
    Nivel(MODULO_DISTRIBUIDORA, "Módulo Distribuidora"),
)


@dataclass(frozen=True)
class Camada:
    """Uma seção de painéis do menu."""

    id: str
    rotulo: str
    # setores em que a seção existe; None = todos. Seção de outro setor não aparece na
    # empresa (nem na barra de pontos da lista)
    setores: frozenset[str] | None = None


# na ordem do menu
CAMADAS: tuple[Camada, ...] = (
    Camada("comercial", "Comercial"),
    Camada("financeiro", "Financeiro"),
    Camada("pessoas", "Pessoas"),
    # só nas empresas Distribuidora; hoje só o Estoque (depois Compras, Custo × preço,
    # Logística e Carteira de clientes, cada um na seção dele)
    Camada("estoque", "Estoque", setores=frozenset({DISTRIBUIDORA})),
)
CAMADA_POR_ID: dict[str, Camada] = {c.id: c for c in CAMADAS}

# grupo da Visão geral no menu do app (os outros painéis ficam no grupo da seção)
MENU_VISAO = "Visão"

TODOS = frozenset({CONSULTOR, CLIENTE_PRINCIPAL, CLIENTE_USUARIO})


@dataclass(frozen=True)
class Painel:
    codigo: str
    # rota da tela no front e na API (/api/telas/<tela>)
    tela: str
    nome: str
    # id da seção (CAMADAS)
    camada: str
    # id do nível (NIVEIS)
    nivel: str
    # prefixos de tabela: basta uma fonte com carga ok em um deles
    exige: tuple[str, ...]
    # prefixos de todas as tabelas que o painel lê
    usa: tuple[str, ...]
    # perfis do cliente que veem o painel quando ele é ligado pela primeira vez
    padrao: frozenset[str] = TODOS
    # a tela mostra nomes de pessoas: quem a tem na matriz precisa ver dado nominal
    nominal: bool = False
    # a tela abre sem nomes, e algum cartão tem a chave "Mostrar nomes" (NOMES): a matriz
    # ganha a chave "vê nomes" por perfil
    nomes_opcionais: bool = False
    # grupo do painel no menu do app, quando não é o da seção (a Visão geral fica em "Visão")
    grupo_no_menu: str | None = None

    @property
    def secao(self) -> str:
        """Rótulo da seção do painel ("Financeiro")."""
        return CAMADA_POR_ID[self.camada].rotulo

    @property
    def menu(self) -> str:
        """Grupo do painel no menu do app."""
        return self.grupo_no_menu or self.secao

    @property
    def setores(self) -> frozenset[str] | None:
        """Setores em que o painel vale (app/seguranca/setores.py); None = todos."""
        return setores_da_tela(self.codigo)


PAINEIS: tuple[Painel, ...] = (
    Painel(
        CODIGO_VISAO_GERAL,
        "visao-geral",
        "Visão geral",
        # depende das vendas
        "comercial",
        BASICO,
        exige=("f_vendas_",),
        usa=("f_vendas_", "f_metas_", "f_receitas_", "f_despesas_", "f_lancamentos_"),
        grupo_no_menu=MENU_VISAO,
    ),
    Painel(
        CODIGOS["vendas"],
        "vendas",
        "Vendas",
        "comercial",
        BASICO,
        exige=("f_vendas_",),
        usa=("f_vendas_", "f_metas_", "d_bitem_"),
    ),
    Painel(
        CODIGOS["equipe-comercial"],
        "equipe-comercial",
        "Equipe comercial",
        "comercial",
        BASICO,
        exige=("f_vendas_",),
        usa=("f_vendas_", "f_metas_", "d_vendedor_"),
        nominal="equipe-comercial" in NOMINAL,
    ),
    Painel(
        CODIGOS["clientes"],
        "clientes",
        "Clientes e atendimentos",
        "comercial",
        BASICO,
        exige=("f_vendas_",),
        usa=("f_vendas_", "d_bitem_"),
    ),
    Painel(
        CODIGOS["resultado"],
        "resultado",
        "Resultado",
        "financeiro",
        COMPLETO,
        exige=("f_lancamentos_",),
        usa=("f_lancamentos_", "f_orcamento_", "f_despesas_", "f_devolucao_", "f_vendas_"),
        padrao=frozenset({CONSULTOR, CLIENTE_PRINCIPAL}),
    ),
    Painel(
        CODIGOS["receber-pagar"],
        "receber-pagar",
        "Receber e pagar",
        "financeiro",
        BASICO,
        exige=("f_receitas_", "f_despesas_"),
        usa=("f_receitas_", "f_despesas_", "d_cliente_", "d_fornecedor_", "d_vendedor_"),
        nominal="receber-pagar" in NOMINAL,
    ),
    Painel(
        CODIGOS["fluxo-caixa"],
        "fluxo-caixa",
        "Fluxo de caixa",
        "financeiro",
        COMPLETO,
        exige=("f_fluxo_",),
        usa=("f_fluxo_", "f_receitas_", "f_despesas_"),
        padrao=frozenset({CONSULTOR, CLIENTE_PRINCIPAL}),
    ),
    Painel(
        CODIGOS["pessoas"],
        "pessoas",
        "Pessoas",
        "pessoas",
        BASICO,
        exige=("f_folha_",),
        usa=("f_folha_", "f_vendas_"),
        padrao=frozenset({CLIENTE_PRINCIPAL}),
        nomes_opcionais=any(tela == "pessoas" for tela, _ in NOMES),
    ),
    Painel(
        CODIGOS["estoque"],
        "estoque",
        "Estoque",
        "estoque",
        MODULO_DISTRIBUIDORA,
        exige=("f_estoque_",),
        usa=("f_estoque_", "f_vendas_", "d_bitem_"),
    ),
)
POR_CODIGO: dict[str, Painel] = {p.codigo: p for p in PAINEIS}


def tem_dado(painel: Painel, tabelas_com_carga: frozenset[str]) -> bool:
    """Alguma tabela com carga `ok` do cliente começa com um dos prefixos que o painel exige."""
    return any(t.startswith(prefixo) for t in tabelas_com_carga for prefixo in painel.exige)


def usada_por(tabela: str) -> list[str]:
    """Nomes dos painéis que leem a tabela, na ordem do catálogo."""
    return [p.nome for p in PAINEIS if any(tabela.startswith(prefixo) for prefixo in p.usa)]
