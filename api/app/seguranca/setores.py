"""Painéis que só valem em certos setores (migrations 34 e 35).

O setor é da unidade (`cfg.empresa.setor`, desde a migration 35), um id da lista fechada
`cfg.setor` (Distribuidora, Indústria, Varejo, Serviços), cadastrado por SQL. Os setores de
um cliente são os das unidades dele, sem repetir; para uma pessoa, os das unidades
liberadas a ela no cliente escolhido (`seg.contexto_acesso.setores`).

Painel fora de SETORES_DA_TELA vale em todos os setores (e nas unidades sem setor). Painel
dentro vale no cliente se **alguma unidade liberada** à pessoa for de um dos setores dele;
senão:
- não entra em `telas` de GET /api/sessao (o menu do front);
- a tela responde o 403 genérico (etapa 4 da checagem de acesso);
- na Gestão, a chave de ligar fica travada, com a nota "Só para empresas com unidade do
  setor …", e o painel sai da matriz, da prévia do menu e das camadas (lá vale qualquer
  unidade do cliente).

Quando vale, o painel soma só as unidades pedidas que são dos setores dele (`escopo` no
PainelPayload; ver app/seguranca/acesso.py). Sem nenhuma, responde 200 só com o aviso.

Mudar o setor de uma unidade não apaga cfg.cliente_tela nem a matriz: o painel do setor
antigo só deixa de valer, e volta se o setor voltar.
"""

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass

DISTRIBUIDORA = "distribuidora"
# setor das unidades sem setor na árvore da bandeja "Negócios"
SEM_SETOR = "sem-setor"
ROTULO_SEM_SETOR = "Sem setor"

# código da tela → setores em que ela vale. Os módulos seguintes do mockup com a etiqueta
# "Dist." (Compras, Custo × preço, Logística, Carteira de clientes) entram aqui quando
# existirem.
SETORES_DA_TELA: dict[str, frozenset[str]] = {
    "EST-01": frozenset({DISTRIBUIDORA}),
}


@dataclass(frozen=True)
class Genero:
    """Como o setor entra numa frase: "a Distribuidora", "da Distribuidora", "ela"."""

    nome: str
    # a, o, os
    artigo: str
    # ela, ele, eles
    pronome: str
    # marcada, marcado, marcados
    marcado: str

    @property
    def com_artigo(self) -> str:
        return f"{self.artigo} {self.nome}"

    @property
    def com_de(self) -> str:
        return f"d{self.com_artigo}"


# os textos com artigo dos setores de cfg.setor (migration 34); setor novo sem entrada aqui
# sai sem artigo ("só Padaria")
GENEROS: dict[str, Genero] = {
    DISTRIBUIDORA: Genero("Distribuidora", "a", "ela", "marcada"),
    "industria": Genero("Indústria", "a", "ela", "marcada"),
    "varejo": Genero("Varejo", "o", "ele", "marcado"),
    "servicos": Genero("Serviços", "os", "eles", "marcados"),
}


def setores_da_tela(codigo: str) -> frozenset[str] | None:
    """Setores em que a tela vale; None = todos."""
    return SETORES_DA_TELA.get(codigo)


def vale_no_setor(exigidos: frozenset[str] | None, setores: Iterable[str]) -> bool:
    """A tela (ou camada) com estes setores vale com as unidades destes `setores`? None =
    vale em todos."""
    return exigidos is None or not exigidos.isdisjoint(setores)


def tela_vale(codigo: str, setores: Iterable[str]) -> bool:
    return vale_no_setor(setores_da_tela(codigo), setores)


def _lista(itens: list[str], conjuncao: str = "e") -> str:
    if len(itens) == 1:
        return itens[0]
    return f"{', '.join(itens[:-1])} {conjuncao} {itens[-1]}"


def _generos(exigidos: Collection[str]) -> list[Genero]:
    return [GENEROS.get(s, Genero(s, "", "ele", "marcado")) for s in sorted(exigidos)]


def _com_artigo(g: Genero) -> str:
    return g.com_artigo if g.artigo else g.nome


def _com_de(g: Genero) -> str:
    return g.com_de if g.artigo else f"de {g.nome}"


def so_para(exigidos: Collection[str], rotulos: Mapping[str, str]) -> str:
    """ "empresas com unidade do setor Distribuidora" / "... dos setores Distribuidora e
    Varejo", pelos rótulos de cfg.setor (id desconhecido sai como está)."""
    nomes = sorted(rotulos.get(s, s) for s in exigidos)
    return (
        f"empresas com unidade {'do setor' if len(nomes) == 1 else 'dos setores'} {_lista(nomes)}"
    )


def nota_fora_do_setor(exigidos: Collection[str], rotulos: Mapping[str, str]) -> str:
    """Nota do painel travado na aba Painéis da Gestão."""
    return f"Só para {so_para(exigidos, rotulos)}."


def msg_ligar_fora_do_setor(exigidos: Collection[str], rotulos: Mapping[str, str]) -> str:
    """422 ao ligar o painel num cliente sem unidade do setor."""
    return f"Este painel é só para {so_para(exigidos, rotulos)}."


def rotulo_escopo(exigidos: Collection[str]) -> str:
    """Rodapé da bandeja "Negócios" num painel de setor: "Este painel soma só a
    Distribuidora"."""
    return f"Este painel soma só {_lista([_com_artigo(g) for g in _generos(exigidos)])}"


def rotulo_escopo_secao(exigidos: Collection[str]) -> str:
    """Seção de um setor na Visão geral, com unidades de outros setores pedidas: "Conta só a
    Distribuidora"."""
    return f"Conta só {_lista([_com_artigo(g) for g in _generos(exigidos)])}"


def aviso_fora_do_escopo(nome_tela: str, exigidos: Collection[str]) -> str:
    """Painel de setor sem nenhuma unidade pedida do setor: "Estoque é um painel da
    Distribuidora. Volta quando ela estiver marcada em Negócios."."""
    generos = _generos(exigidos)
    de = _lista([_com_de(g) for g in generos])
    if len(generos) == 1:
        g = generos[0]
        volta = f"Volta quando {g.pronome} estiver{'em' if g.pronome == 'eles' else ''} {g.marcado}"
    else:
        volta = "Volta quando um deles estiver marcado"
    return f"{nome_tela} é um painel {de}. {volta} em Negócios."
