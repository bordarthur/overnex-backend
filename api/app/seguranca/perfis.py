"""Os 4 perfis fixos do Overnex (mockup Gestão M2.1.0), iguais em todos os clientes.

O perfil é da pessoa (seg.usuario.perfil). O Admin BORD vê todos os clientes, sem vínculo;
os outros três só os clientes dos vínculos, e em cada um o que a matriz da Gestão libera ao
perfil (cfg.perfil_tela do perfil fixo do cliente, migration 25).

A sessão padrão de cada perfil e o papel do vínculo ficam no banco (seg.sessao_padrao e
seg.papel_do_perfil), que é quem os aplica.
"""

from dataclasses import dataclass

ADMIN = "admin"
CONSULTOR = "consultor"
CLIENTE_PRINCIPAL = "cliente_principal"
CLIENTE_USUARIO = "cliente_usuario"


@dataclass(frozen=True)
class Perfil:
    id: str
    rotulo: str
    descricao: str
    # quais empresas o perfil alcança (texto do cartão do perfil na Gestão)
    clientes: str


PERFIS: tuple[Perfil, ...] = (
    Perfil(
        ADMIN,
        "Admin BORD",
        "Equipe BORD que configura o Overnex. Vê todas as empresas e todos os painéis.",
        "Todas, sem vínculo",
    ),
    Perfil(
        CONSULTOR,
        "Consultor por carteira",
        "Consultor da BORD ou parceiro BPO. Vê só as empresas da carteira dele.",
        "As da carteira",
    ),
    Perfil(
        CLIENTE_PRINCIPAL,
        "Cliente principal",
        "Dono ou diretor. Vê todos os painéis ligados da empresa, inclusive Pessoas.",
        "Uma ou mais do grupo",
    ),
    Perfil(
        CLIENTE_USUARIO,
        "Cliente usuário",
        "Equipe do cliente. Vê os painéis que o perfil libera em cada empresa.",
        "Uma ou mais",
    ),
)
POR_ID: dict[str, Perfil] = {p.id: p for p in PERFIS}
# os que têm linha em cfg.perfil_acesso de cada cliente (o admin não tem: vê o que está ligado)
DO_CLIENTE: tuple[Perfil, ...] = tuple(p for p in PERFIS if p.id != ADMIN)


def rotulo(perfil: str) -> str:
    """Nome do perfil para a tela; perfil desconhecido sai como veio."""
    p = POR_ID.get(perfil)
    return p.rotulo if p else perfil
