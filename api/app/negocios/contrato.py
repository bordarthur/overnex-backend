"""Contrato da bandeja "Negócios" do front (setor › grupo › unidade), em camelCase.

A árvore vai em `negocios` de GET /api/sessao; os valores, em GET /api/negocios.
"""

from app.telas.moldura import ModeloCamel
from app.telas.visao_geral.contrato import Valor


class GrupoNegocio(ModeloCamel):
    # estável: "<setor>/<grupo>" ("varejo/Lupo"); sem grupo, "<setor>/sem-grupo"
    id: str
    # "Lupo"; sem grupo, "Sem grupo"
    rotulo: str
    # ids das unidades (os de `empresas` da sessão), na ordem natural do nome
    unidades: list[str]


class SetorNegocio(ModeloCamel):
    # id de cfg.setor; unidade sem setor: "sem-setor" (no fim)
    id: str
    rotulo: str
    grupos: list[GrupoNegocio]


class NegociosSessao(ModeloCamel):
    # na ordem de cfg.setor; cada unidade de `empresas` em exatamente um grupo
    setores: list[SetorNegocio]
    # código da tela → setores em que ela vale ({"EST-01": ["distribuidora"]}); tela fora
    # daqui vale em todos. Só as telas de `telas`
    telas_por_setor: dict[str, list[str]]


class NegociosValores(ModeloCamel):
    # "Valores: faturado de 1º a 12/10" ou "Valores: faturado em setembro de 2026"
    rotulo: str
    # o faturado do setor, do grupo e da unidade inteiros, sem olhar o que está marcado
    setores: dict[str, Valor]
    grupos: dict[str, Valor]
    unidades: dict[str, Valor]
