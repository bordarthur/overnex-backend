"""Leitura das migrations, do plano de contas e do script de cadastro, para os testes."""

import re
from functools import cache
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[3]
MIGRATIONS = RAIZ / "supabase" / "migrations"
PRODUCAO = RAIZ / "supabase" / "producao"
CADASTRO = PRODUCAO / "distribuidora_overnex_cadastro.sql"


@cache
def colunas_do_mart() -> dict[str, dict[str, str]]:
    """Tabela → coluna → tipo, aplicando as migrations em ordem (create, add e alter type)."""
    tabelas: dict[str, dict[str, str]] = {}
    for arquivo in sorted(MIGRATIONS.glob("*.sql")):
        texto = arquivo.read_text(encoding="utf-8")
        for nome, corpo in re.findall(r"create table mart\.(\w+) \((.*?)\n\);", texto, re.S):
            tabelas[nome] = dict(re.findall(r'^\s+"(\w+)"\s+(\w+)', corpo, re.M))
        for nome, corpo in re.findall(r"alter table mart\.(\w+)\s+(.*?);", texto, re.S):
            if nome not in tabelas:
                continue
            for col, tipo in re.findall(r'alter column "(\w+)"\s+type (\w+)', corpo):
                tabelas[nome][col] = tipo
            for col, tipo in re.findall(r'add column "(\w+)"\s+(\w+)', corpo):
                tabelas[nome][col] = tipo
        for nome in re.findall(r"drop table (?:if exists )?mart\.(\w+)", texto):
            tabelas.pop(nome, None)
    return tabelas


@cache
def plano() -> dict[str, tuple[str, str | None, str | None]]:
    """Id_Descritivo → (descritivo, caixa, competência) do plano de contas padrão."""
    texto = (PRODUCAO / "plano_contas_padrao.sql").read_text(encoding="utf-8")
    linhas = re.findall(
        r"\('([\d.]+)', '((?:[^']|'')*)', '[\d.]+', '(?:[^']|'')*', '[\d.]+', "
        r"'(?:[^']|'')*', -?\d+, (null|'\w+'), (null|'[\w/]+')\)",
        texto,
    )

    def valor(v: str) -> str | None:
        return None if v == "null" else v.strip("'")

    return {i: (d, valor(caixa), valor(comp)) for i, d, caixa, comp in linhas}


def cadastro_sql() -> str:
    return CADASTRO.read_text(encoding="utf-8")
