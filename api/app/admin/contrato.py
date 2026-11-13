"""Contrato das rotas da Gestão (/api/admin/*), no formato do front (camelCase).

Textos, rótulos e datas saem prontos. `status` usa o vocabulário de cor do front: saudavel,
atencao, critico ou neutro. Estas rotas não usam `response_model_exclude_defaults`: todo
campo vem sempre, com null quando não há valor.
"""

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.telas.moldura import ModeloCamel

Status = Literal["saudavel", "atencao", "critico", "neutro"]


class ItemCliente(ModeloCamel):
    slug: str
    nome: str


class Rotulado(ModeloCamel):
    id: str
    rotulo: str


# ---------- moldura da Gestão ----------


class CamadaResumo(ModeloCamel):
    id: str
    rotulo: str
    # ativa, implantacao ou desligada
    estado: str
    estado_rotulo: str
    status: Status


class ClienteDaLista(ModeloCamel):
    slug: str
    nome: str
    # setor da empresa, ex.: "Distribuição de alimentos"; null = não informado
    segmento: str | None = None
    camadas: list[CamadaResumo]


class PerfilDaGestao(ModeloCamel):
    id: str
    rotulo: str
    descricao: str
    # quais empresas o perfil alcança, ex.: "As da carteira"
    clientes: str
    sessao_padrao_minutos: int
    sessao_padrao_rotulo: str
    # pessoas ativas com o perfil
    pessoas: int


class OpcaoSessao(ModeloCamel):
    minutos: int
    rotulo: str
    # só para o perfil Admin BORD (mais de 12 h)
    so_admin: bool


class GestaoPayload(ModeloCamel):
    clientes: list[ClienteDaLista]
    perfis: list[PerfilDaGestao]
    sessoes: list[OpcaoSessao]


# ---------- pessoas ----------


class AoEntrar(ModeloCamel):
    # direto (abre numa empresa), lista (escolhe a empresa) ou nada
    tipo: Literal["direto", "lista", "nada"]
    rotulo: str


class Situacao(ModeloCamel):
    # ativo, fila (convite pedido, ainda não saiu), nao-enviado (o envio falhou), convite
    # (enviado), vencido, inativo ou sem-empresa
    id: str
    rotulo: str
    # ex.: "último acesso hoje, 9h14", "enviado ontem, 16h10", "ainda não entrou"
    quando: str
    status: Status
    # só em nao-enviado: por que o convite não saiu; nas outras, null
    detalhe: str | None = None


class AcaoConvite(ModeloCamel):
    # texto do botão: "Reenviar convite" ou "Tentar de novo"
    rotulo: str


class Acessos(ModeloCamel):
    # logins nos últimos 30 dias (o gerador de camelCase faria "entradas30D")
    entradas30d: int = Field(alias="entradas30d")
    # ex.: "hoje, 9h14"; null = nunca entrou
    ultimo: str | None


class Usuario(ModeloCamel):
    id: UUID
    nome: str
    email: str
    perfil: Rotulado
    # Admin BORD: vê todas as empresas, sem vínculo (`clientes` vem vazio)
    todos_os_clientes: bool
    clientes: list[ItemCliente]
    ao_entrar: AoEntrar
    # minutos de sessão parada da pessoa (o dela ou o padrão do perfil)
    sessao_minutos: int
    situacao: Situacao
    acessos: Acessos
    # false = não oferecer "Desativar acesso" (já desativado, ou o último Admin BORD ativo)
    pode_desativar: bool
    # botão que manda o convite de novo (POST /api/admin/usuarios/{id}/convite); null = não
    # oferecer (já entrou, está desativado ou o convite já está na fila)
    convite: AcaoConvite | None = None


class FiltroUsuarios(ModeloCamel):
    # todos, um perfil (admin, consultor, …), pendentes (fila, nao-enviado, convite ou
    # vencido) ou sem-empresa
    id: str
    rotulo: str
    total: int
    # filtro do grupo "Pedem atenção"
    atencao: bool


class UsuariosPayload(ModeloCamel):
    usuarios: list[Usuario]
    filtros: list[FiltroUsuarios]
    # texto da gaveta de convite: como o convite chega e em quanto tempo sai
    nota_convite: str


class UsuarioDoCliente(Usuario):
    outros_clientes: list[ItemCliente]
    # ex.: "5 de 8"
    paineis_que_ve: str


# ---------- um cliente ----------


class ClienteDaGestao(ModeloCamel):
    slug: str
    nome: str
    # setor da empresa; null = não informado
    segmento: str | None = None
    # só caminho do próprio domínio (a CSP do front recusa imagem de fora)
    logo_url: str | None


class Chip(ModeloCamel):
    rotulo: str
    status: Status


class CamadaDoCliente(CamadaResumo):
    grupo: str
    # o que falta para a camada ficar ativa, ex.: "Pessoas: desligado"
    pendencias: list[str]


class FonteDoCliente(ModeloCamel):
    tabela: str
    # nomes dos painéis que leem a tabela
    usada_por: list[str]
    # linhas gravadas na última carga ok; null = nunca carregou
    linhas: int | None
    # data dos dados da última carga ok, ex.: "13/10/2026"
    ultima_data: str | None
    status: Status
    # ok, erro, sem carga, rodando ou inativa
    status_rotulo: str
    observacao: str | None


class PainelDoCliente(ModeloCamel):
    codigo: str
    nome: str
    grupo: str
    ligado: bool
    # false = chave de ligar travada (sem dado no cliente); `nota` diz o porquê
    pode_ligar: bool
    status: Status
    # disponível ou indisponível
    status_rotulo: str
    nota: str | None


class PerfilDaMatriz(ModeloCamel):
    id: str
    rotulo: str
    # true = coluna travada e sempre marcada (Admin BORD)
    fixo: bool


class LinhaDaMatriz(ModeloCamel):
    codigo: str
    nome: str
    # perfil.id → vê o painel
    ve: dict[str, bool]


class Matriz(ModeloCamel):
    perfis: list[PerfilDaMatriz]
    # só os painéis ligados
    linhas: list[LinhaDaMatriz]


class OpcaoPreferencia(ModeloCamel):
    id: str
    rotulo: str


class PreferenciaDoCliente(ModeloCamel):
    id: str
    rotulo: str
    descricao: str
    opcoes: list[OpcaoPreferencia]
    valor: str
    editavel: bool


class Feriado(ModeloCamel):
    data: date
    nome: str


class CargaDoCliente(ModeloCamel):
    # ex.: "13/10"
    data: str
    # ex.: "9h00"
    inicio: str
    # ex.: "2 min 04 s"; null = ainda rodando
    duracao: str | None
    # fontes do cliente na execução
    tabelas: int
    status: Status
    status_rotulo: str
    observacao: str | None


class ItemDoMenu(ModeloCamel):
    nome: str


class GrupoDoMenu(ModeloCamel):
    grupo: str
    itens: list[ItemDoMenu]


class ClientePayload(ModeloCamel):
    cliente: ClienteDaGestao
    resumo: list[Chip]
    camadas: list[CamadaDoCliente]
    fontes: list[FonteDoCliente]
    paineis: list[PainelDoCliente]
    acesso: Matriz
    usuarios: list[UsuarioDoCliente]
    preferencias: list[PreferenciaDoCliente]
    feriados: list[Feriado]
    cargas: list[CargaDoCliente]
    # como o Cliente principal vê o menu
    menu: list[GrupoDoMenu]


# ---------- corpos das gravações ----------


class MudancaPainel(ModeloCamel):
    ligado: bool


class MudancaAcessoPerfil(ModeloCamel):
    ve: bool


class MudancaPreferencia(ModeloCamel):
    valor: str = Field(max_length=40)


class MudancaFeriado(ModeloCamel):
    nome: str = Field(min_length=1, max_length=80)


class MudancaUsuario(ModeloCamel):
    nome: str = Field(min_length=1, max_length=120)
    perfil: str = Field(max_length=40)
    # slugs das empresas da pessoa; ignorado no perfil Admin BORD
    clientes: list[str] = Field(max_length=200)
    # null = o padrão do perfil
    sessao_minutos: int | None = None


class NovaEmpresa(ModeloCamel):
    nome: str = Field(min_length=1, max_length=120)
    # setor, texto livre; null ou vazio = não informado
    segmento: str | None = Field(default=None, max_length=80)
    # identificador da empresa nas URLs (minúsculas, números e hífen); null = a API monta
    # a partir do nome
    slug: str | None = Field(default=None, max_length=80)


class MudancaEmpresa(ModeloCamel):
    nome: str = Field(min_length=1, max_length=120)
    # setor, texto livre; null ou vazio = não informado
    segmento: str | None = Field(default=None, max_length=80)


class EmpresaCriada(ModeloCamel):
    # para abrir a empresa: GET /api/admin/clientes/{slug}
    slug: str
    nome: str
    # texto do passo final
    mensagem: str


class NovoUsuario(ModeloCamel):
    # o login; não muda depois
    email: str = Field(min_length=3, max_length=254)
    nome: str = Field(min_length=1, max_length=120)
    perfil: str = Field(max_length=40)
    # slugs das empresas da pessoa; pode vir vazio (fica "Sem empresa"); ignorado no perfil
    # Admin BORD
    clientes: list[str] = Field(default_factory=list, max_length=200)
    # null = o padrão do perfil
    sessao_minutos: int | None = None


class ConvitePedido(ModeloCamel):
    # a pessoa convidada
    id: UUID
    # texto do passo final, ex.: "Convite na fila: sai em instantes."
    mensagem: str


class MudancaSessao(ModeloCamel):
    minutos: int


class MudancaAcesso(ModeloCamel):
    ativo: bool
