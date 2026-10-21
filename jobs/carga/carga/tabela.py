"""Carga do tipo 'tabela': o Parquet entra em mart.<tabela_destino> com as mesmas colunas.

1. baixa o Parquet e valida a data do snapshot (falhou → para sem tocar no mart);
2. confere as colunas contra a tabela do mart (criada por migration):
   - coluna da tabela que falta no arquivo → falha;
   - coluna nova no arquivo, ainda sem coluna na tabela → ignorada, com aviso;
   se a fonte tem janela de datas (coluna_data + meses_retidos), só seguem as linhas
   dos últimos N meses (ou com a data vazia), antes de qualquer coisa ir ao banco;
3. em UMA transação: temp table + COPY; se a fonte tem coluna de empresa, traduz por
   cfg.mapa_empresa (valor sem mapeamento → falha listando); apaga TODAS as linhas do
   cliente na tabela e insere as do arquivo, numeradas em _linha; se a tabela tem modelo
   de resumo (carga/resumo.py), troca também os totais mensais em mart.resumo_mensal,
   calculados sobre o arquivo INTEIRO, antes da janela;
4. commit só se tudo deu certo. Até o commit, o painel vê os dados anteriores.
"""

from datetime import timedelta

import psycopg
import pyarrow as pa
from psycopg import sql

from carga import janela, resumo
from carga.arquivo import Baixar, Progresso, baixar_e_validar_data, listar_valores
from carga.config import Settings
from carga.db import Conexao
from carga.erros import ErroCarga
from carga.esquema import COLUNAS_SISTEMA
from carga.fontes import Fonte
from carga.validacao import conferir_colunas

LOTE_COPY = 50_000


def colunas_da_tabela(conn: Conexao, tabela: str) -> list[str]:
    return [
        r[0]
        for r in conn.execute(
            """
            select column_name
              from information_schema.columns
             where table_schema = 'mart' and table_name = %s
             order by ordinal_position
            """,
            (tabela,),
        ).fetchall()
    ]


def carregar(
    conn: Conexao,
    settings: Settings,
    fonte: Fonte,
    baixar: Baixar,
    progresso: Progresso,
) -> None:
    tabela = fonte.tabela_destino
    if not tabela:
        raise ErroCarga("fonte do tipo 'tabela' sem tabela_destino em cfg.fonte_carga")

    destino = colunas_da_tabela(conn, tabela)
    if not destino:
        raise ErroCarga(
            f"a tabela mart.{tabela} não existe: crie com uma migration "
            "(o inventário do bucket sugere o SQL)"
        )
    if any(c not in destino for c in COLUNAS_SISTEMA):
        raise ErroCarga(f"mart.{tabela} não é uma tabela de dados (faltam colunas de sistema)")
    colunas = [c for c in destino if c not in COLUNAS_SISTEMA]
    if fonte.coluna_empresa and fonte.coluna_empresa not in colunas:
        raise ErroCarga(
            f"coluna de empresa {fonte.coluna_empresa!r} (cfg.fonte_carga.coluna_empresa) "
            f"não existe em mart.{tabela}"
        )

    if fonte.coluna_data and fonte.coluna_data not in colunas:
        raise ErroCarga(
            f"coluna de data {fonte.coluna_data!r} (cfg.fonte_carga.coluna_data) "
            f"não existe em mart.{tabela}"
        )

    arquivo = baixar_e_validar_data(conn, settings, fonte, baixar, progresso)

    nomes = arquivo.schema_arrow.names
    conferir_colunas(nomes, colunas)
    novas = [c for c in nomes if c not in colunas]
    if novas:
        progresso.avisos.append(
            f"colunas novas no arquivo, ainda sem coluna em mart.{tabela} (ignoradas): "
            f"{', '.join(novas)}"
        )

    dados = arquivo.read(columns=colunas)
    # antes da janela: o resumo mensal usa o arquivo inteiro
    totais = _resumo(tabela, colunas, fonte, dados, progresso)
    if fonte.coluna_data and fonte.meses_retidos and progresso.data_snapshot:
        dados, fora, inicio, fim = janela.filtrar(
            dados,
            fonte.coluna_data,
            progresso.data_snapshot,
            fonte.meses_retidos,
            meses_fechados=fonte.meses_fechados,
        )
        if fim is None:
            progresso.avisos.append(
                f"janela de {fonte.meses_retidos} meses em {fonte.coluna_data}: "
                f"{fora} linhas antes de {inicio:%d/%m/%Y} ficaram de fora"
            )
        else:
            ultimo_dia = fim - timedelta(days=1)
            progresso.avisos.append(
                f"janela de {fonte.meses_retidos} meses fechados em {fonte.coluna_data} "
                f"({inicio:%d/%m/%Y} a {ultimo_dia:%d/%m/%Y}): "
                f"{fora} linhas fora do período ficaram de fora"
            )
    try:
        progresso.linhas_gravadas = _gravar(conn, fonte, tabela, colunas, dados, totais, progresso)
    except psycopg.errors.DataError as exc:
        raise ErroCarga(
            f"valor incompatível com o tipo de uma coluna de mart.{tabela}: {exc}"
        ) from exc


def _resumo(
    tabela: str,
    colunas: list[str],
    fonte: Fonte,
    dados: pa.Table,
    progresso: Progresso,
) -> list[resumo.Total] | None:
    """Totais mensais do arquivo inteiro; None se a tabela não tem modelo de resumo.

    Falha no resumo não derruba a carga da tabela: vira aviso e o resumo anterior fica.
    """
    try:
        modelo = resumo.modelo_da_tabela(tabela, fonte.colunas_modelo)
    except ErroCarga as exc:
        progresso.avisos.append(f"resumo mensal não calculado: {exc}")
        return None
    if modelo is None:
        return None
    faltando = [c for c in modelo.colunas if c not in colunas]
    if faltando:
        progresso.avisos.append(
            f"resumo mensal não calculado: faltam em mart.{tabela} as colunas {faltando}"
        )
        return None
    try:
        return resumo.calcular(dados, modelo, fonte.coluna_empresa)
    except ErroCarga as exc:
        progresso.avisos.append(f"resumo mensal não calculado: {exc}")
        return None


def _gravar(
    conn: Conexao,
    fonte: Fonte,
    tabela: str,
    colunas: list[str],
    dados: pa.Table,
    totais: list[resumo.Total] | None,
    progresso: Progresso,
) -> int:
    alvo = sql.Identifier("mart", tabela)
    lista = sql.SQL(", ").join(sql.Identifier(c) for c in colunas)

    with conn.transaction():
        # Mesmos tipos da tabela de destino, sem as restrições.
        conn.execute(
            sql.SQL(
                "create temp table tmp_dados on commit drop as "
                "select _linha, {lista} from {alvo} with no data"
            ).format(lista=lista, alvo=alvo)
        )
        with (
            conn.cursor() as cur,
            cur.copy(
                sql.SQL("copy tmp_dados (_linha, {lista}) from stdin").format(lista=lista)
            ) as copy,
        ):
            numero = 0
            for lote in dados.to_batches(max_chunksize=LOTE_COPY):
                valores = [lote.column(i).to_pylist() for i in range(lote.num_columns)]
                for linha in zip(*valores, strict=True):
                    numero += 1
                    copy.write_row((numero, *linha))

        if fonte.coluna_empresa:
            col = sql.Identifier(fonte.coluna_empresa)
            sem_mapa = [
                r[0]
                for r in conn.execute(
                    sql.SQL(
                        """
                        select distinct t.{col}
                          from tmp_dados t
                         where not exists (
                                select 1 from cfg.mapa_empresa m
                                 where m.cliente_id = %s and m.empresa_origem = t.{col})
                         order by 1
                        """
                    ).format(col=col),
                    (fonte.cliente_id,),
                ).fetchall()
            ]
            if sem_mapa:
                raise ErroCarga(
                    f"{fonte.coluna_empresa} sem mapeamento em cfg.mapa_empresa para o cliente "
                    f"{fonte.cliente_id}: {listar_valores(sem_mapa)}"
                )
            empresa: sql.Composable = sql.SQL("m.empresa_id")
            juncao: sql.Composable = sql.SQL(
                "join cfg.mapa_empresa m"
                " on m.cliente_id = %(cliente)s and m.empresa_origem = t.{col}"
            ).format(col=col)
        else:
            empresa = sql.SQL("null::uuid")
            juncao = sql.SQL("")

        # TODAS as linhas do cliente nesta tabela saem; as do arquivo entram.
        conn.execute(
            sql.SQL("delete from {alvo} where _cliente_id = %s").format(alvo=alvo),
            (fonte.cliente_id,),
        )
        colunas_t = sql.SQL(", ").join(sql.Identifier("t", c) for c in colunas)
        gravadas = conn.execute(
            sql.SQL(
                "insert into {alvo} (_linha, _cliente_id, _empresa_id, {lista}) "
                "select t._linha, %(cliente)s, {empresa}, {colunas_t} from tmp_dados t {juncao}"
            ).format(alvo=alvo, lista=lista, empresa=empresa, colunas_t=colunas_t, juncao=juncao),
            {"cliente": fonte.cliente_id},
        ).rowcount

        # o resumo troca na mesma transação: tabela e resumo sempre do mesmo arquivo
        if totais is not None and progresso.data_snapshot is not None:
            if resumo.tabela_existe(conn):
                n = resumo.gravar(
                    conn,
                    fonte.cliente_id,
                    tabela,
                    totais,
                    progresso.data_snapshot,
                    progresso.avisos,
                )
                progresso.avisos.append(f"resumo mensal: {n} totais gravados")
            else:
                progresso.avisos.append(
                    "resumo mensal não gravado: mart.resumo_mensal não existe (migration 8)"
                )
        return gravadas
