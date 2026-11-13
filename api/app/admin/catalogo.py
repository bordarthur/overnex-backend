"""Catálogo fechado dos painéis que a Gestão liga e desliga por cliente.

"Configuração, não personalização" (mockup Gestão M2.1.0): a BORD só liga ou desliga painéis
deste catálogo; dentro de cada painel, o cartão sem dado some sozinho (os montadores de
app/telas já devolvem só o que tem dado).

Só entram painéis que existem na API. Do mockup ficam fora Carteira de clientes, Estoque e
Compras (módulo Distribuidora), que ainda não têm tela; a FIN-01 (tela antiga de
lançamentos) existe mas não é painel do catálogo.

Camada = grupo de painéis da implantação. Padrão da matriz "o que cada perfil vê" (aplicado
quando o painel é ligado pela primeira vez no cliente): Cliente principal vê tudo; Consultor
não vê Pessoas; Cliente usuário não vê Pessoas, Resultado nem Fluxo de caixa.

Dado do painel: `exige` são prefixos de tabela do mart (cfg.fonte_carga.tabela_destino); o
painel está disponível se alguma fonte ativa com um desses prefixos já teve carga `ok`.
REGRA PROVISÓRIA: é a fonte principal de cada painel, tirada dos montadores (vendas.py,
paineis/base.py, receber_pagar.py, pessoas.py, resultado.py, fluxo_caixa.py); se um painel
passar a ler outra fonte, ajuste aqui. `usa` é só para a coluna "Usada por" da aba
Prontidão dos dados.
"""

from dataclasses import dataclass

from app.seguranca.perfis import CLIENTE_PRINCIPAL, CLIENTE_USUARIO, CONSULTOR
from app.telas.paineis.router import CODIGOS, NOMINAL
from app.telas.visao_geral.router import CODIGO_TELA as CODIGO_VISAO_GERAL


@dataclass(frozen=True)
class Camada:
    id: str
    rotulo: str
    # "Universal Básico" sobe só com as tabelas do ERP; "Universal Completo" depende do plano
    # de contas, do de-para, do orçamento e do saldo inicial
    grupo: str


BASICO = "Universal Básico"
COMPLETO = "Universal Completo"

CAMADAS: tuple[Camada, ...] = (
    Camada("comercial", "Comercial", BASICO),
    Camada("pessoas", "Pessoas", BASICO),
    Camada("receber-pagar", "Receber e pagar", BASICO),
    Camada("completo", "Universal Completo", COMPLETO),
)
CAMADA_POR_ID: dict[str, Camada] = {c.id: c for c in CAMADAS}

TODOS = frozenset({CONSULTOR, CLIENTE_PRINCIPAL, CLIENTE_USUARIO})


@dataclass(frozen=True)
class Painel:
    codigo: str
    # rota da tela no front e na API (/api/telas/<tela>)
    tela: str
    nome: str
    camada: str
    # prefixos de tabela: basta uma fonte com carga ok em um deles
    exige: tuple[str, ...]
    # prefixos de todas as tabelas que o painel lê
    usa: tuple[str, ...]
    # perfis do cliente que veem o painel quando ele é ligado pela primeira vez
    padrao: frozenset[str] = TODOS
    # a tela mostra nomes de pessoas: quem a tem na matriz precisa ver dado nominal
    nominal: bool = False

    @property
    def grupo(self) -> str:
        return CAMADA_POR_ID[self.camada].grupo


PAINEIS: tuple[Painel, ...] = (
    Painel(
        CODIGO_VISAO_GERAL,
        "visao-geral",
        "Visão geral",
        "comercial",
        exige=("f_vendas_",),
        usa=("f_vendas_", "f_metas_", "f_receitas_", "f_despesas_", "f_lancamentos_"),
    ),
    Painel(
        CODIGOS["vendas"],
        "vendas",
        "Vendas",
        "comercial",
        exige=("f_vendas_",),
        usa=("f_vendas_", "f_metas_", "d_bitem_"),
    ),
    Painel(
        CODIGOS["equipe-comercial"],
        "equipe-comercial",
        "Equipe comercial",
        "comercial",
        exige=("f_vendas_",),
        usa=("f_vendas_", "f_metas_", "d_vendedor_"),
        nominal="equipe-comercial" in NOMINAL,
    ),
    Painel(
        CODIGOS["clientes"],
        "clientes",
        "Clientes e atendimentos",
        "comercial",
        exige=("f_vendas_",),
        usa=("f_vendas_", "d_bitem_"),
    ),
    Painel(
        CODIGOS["receber-pagar"],
        "receber-pagar",
        "Receber e pagar",
        "receber-pagar",
        exige=("f_receitas_", "f_despesas_"),
        usa=("f_receitas_", "f_despesas_", "d_cliente_", "d_fornecedor_", "d_vendedor_"),
        nominal="receber-pagar" in NOMINAL,
    ),
    Painel(
        CODIGOS["pessoas"],
        "pessoas",
        "Pessoas",
        "pessoas",
        exige=("f_folha_",),
        usa=("f_folha_", "f_vendas_"),
        padrao=frozenset({CLIENTE_PRINCIPAL}),
    ),
    Painel(
        CODIGOS["resultado"],
        "resultado",
        "Resultado",
        "completo",
        exige=("f_lancamentos_",),
        usa=("f_lancamentos_", "f_orcamento_", "f_despesas_", "f_devolucao_", "f_vendas_"),
        padrao=frozenset({CONSULTOR, CLIENTE_PRINCIPAL}),
    ),
    Painel(
        CODIGOS["fluxo-caixa"],
        "fluxo-caixa",
        "Fluxo de caixa",
        "completo",
        exige=("f_fluxo_",),
        usa=("f_fluxo_", "f_receitas_", "f_despesas_"),
        padrao=frozenset({CONSULTOR, CLIENTE_PRINCIPAL}),
    ),
)
POR_CODIGO: dict[str, Painel] = {p.codigo: p for p in PAINEIS}


def tem_dado(painel: Painel, tabelas_com_carga: frozenset[str]) -> bool:
    """Alguma tabela com carga `ok` do cliente começa com um dos prefixos que o painel exige."""
    return any(t.startswith(prefixo) for t in tabelas_com_carga for prefixo in painel.exige)


def usada_por(tabela: str) -> list[str]:
    """Nomes dos painéis que leem a tabela, na ordem do catálogo."""
    return [p.nome for p in PAINEIS if any(tabela.startswith(prefixo) for prefixo in p.usa)]
