"""
Snapshot diário de tabelas do Azure SQL Database para o Supabase Storage.

Como funciona:
  1. Conecta no Azure SQL Database.
  2. Para cada tabela listada em TABELAS, lê todas as linhas (SELECT *).
  3. Mantém os tipos do banco (inteiros, decimais, datas etc.), adiciona a
     coluna _data_snapshot e grava um Parquet (zstd) em:
        <bucket>/<PREFIXO>/<tabela>/<tabela>.parquet
  4. Cada execução sobrescreve o arquivo anterior da tabela
     (fica sempre só a versão mais recente, sem histórico).

Agende para rodar uma vez por dia, depois da última carga das tabelas.

Variáveis de ambiente:
  SQL_SERVER            servidor, ex.: meuservidor.database.windows.net
  SQL_DATABASE          nome do banco
  TABELAS               tabelas separadas por vírgula ou uma por linha, no
                        formato schema.tabela (sem schema = dbo),
                        ex.: dbo.Vendas,Clientes
  SUPABASE_URL          ex.: https://xxxx.supabase.co
  SUPABASE_SECRET_KEY   chave secret (ou service_role legada) do projeto
  SUPABASE_BUCKET       bucket de destino (crie como privado)
  PREFIXO               (opcional) pasta raiz no bucket, padrão "dataflow"
  FUSO                  (opcional) fuso usado para a data do snapshot,
                        padrão America/Fortaleza
  DATA_SNAPSHOT         (opcional) força a data da coluna _data_snapshot,
                        AAAA-MM-DD
  SQL_DRIVER            (opcional) driver ODBC, padrão
                        "ODBC Driver 18 for SQL Server"

Autenticação no banco (uma das duas):
  - usuário SQL: SQL_USER e SQL_PASSWORD, ou
  - service principal do Entra ID: AZURE_CLIENT_ID e AZURE_CLIENT_SECRET,
    com um usuário criado no banco para o app e o papel db_datareader.
"""

import io
import os
import re
import struct
import sys
import unicodedata
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from time import sleep
from urllib.parse import quote
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq
import pyodbc
import requests


# ---------------------------------------------------------------- utilitários

def env(nome, padrao=None, obrigatoria=False):
    valor = os.environ.get(nome) or padrao  # vazio conta como não definida
    if obrigatoria and not valor:
        sys.exit(f"Variável de ambiente obrigatória ausente: {nome}")
    return valor


def slug(texto):
    """'dbo.Vendas Diárias' -> 'dbo_vendas_diarias' (seguro para caminho de arquivo)."""
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", texto.lower()).strip("_") or "tabela"


def separar_tabela(nome):
    """'dbo.Vendas' -> ('dbo', 'Vendas'); 'Vendas' -> ('dbo', 'Vendas')."""
    schema, _, tabela = nome.rpartition(".")
    return schema or "dbo", tabela


def colchetes(identificador):
    return "[" + identificador.replace("]", "]]") + "]"


# ---------------------------------------------------------------- Azure SQL

TENTATIVAS = 5  # conexões tentadas antes de desistir
ESPERA = 30     # segundos entre tentativas

def _valor_odbc(valor):
    """Protege valores com ; ou } na string de conexão."""
    return "{" + valor.replace("}", "}}") + "}"


def _datetimeoffset(valor):
    """pyodbc não converte datetimeoffset sozinho; devolve datetime em UTC."""
    a, mes, d, h, mi, s, ns, oh, om = struct.unpack("<6hI2h", valor)
    local = datetime(a, mes, d, h, mi, s, ns // 1000, timezone(timedelta(hours=oh, minutes=om)))
    return local.astimezone(timezone.utc)


def conectar():
    partes = [
        f"DRIVER={_valor_odbc(env('SQL_DRIVER', 'ODBC Driver 18 for SQL Server'))}",
        f"SERVER=tcp:{env('SQL_SERVER', obrigatoria=True)},1433",
        f"DATABASE={_valor_odbc(env('SQL_DATABASE', obrigatoria=True))}",
        "Encrypt=yes",
        "TrustServerCertificate=no",
        "Connection Timeout=120",
    ]
    if env("SQL_USER"):
        partes += [f"UID={_valor_odbc(env('SQL_USER'))}", f"PWD={_valor_odbc(env('SQL_PASSWORD', ''))}"]
    elif env("AZURE_CLIENT_ID"):
        partes += [
            "Authentication=ActiveDirectoryServicePrincipal",
            f"UID={_valor_odbc(env('AZURE_CLIENT_ID'))}",
            f"PWD={_valor_odbc(env('AZURE_CLIENT_SECRET', obrigatoria=True))}",
        ]
    else:
        sys.exit("Defina SQL_USER e SQL_PASSWORD, ou AZURE_CLIENT_ID e AZURE_CLIENT_SECRET.")

    # Banco serverless pausado demora para acordar: a 1ª tentativa costuma
    # dar timeout (HYT00) ou "not currently available" (40613).
    for tentativa in range(1, TENTATIVAS + 1):
        try:
            conexao = pyodbc.connect(";".join(partes))
            break
        except pyodbc.Error as erro:
            if tentativa == TENTATIVAS or not any(c in str(erro) for c in ("HYT00", "40613", "40197", "40501")):
                raise
            print(f"Banco indisponível (tentativa {tentativa}/{TENTATIVAS}), aguardando {ESPERA}s...")
            sleep(ESPERA)
    conexao.add_output_converter(-155, _datetimeoffset)  # -155 = datetimeoffset
    return conexao


# ---------------------------------------------------------------- tipos

def tipo_arrow(descricao):
    """Tipo do Parquet a partir de cursor.description (None = deixar o pyarrow inferir)."""
    _, tipo_py, _, _, precisao, escala, _ = descricao
    if tipo_py is Decimal and precisao:
        return pa.decimal128(min(precisao, 38), escala or 0)
    return {
        bool: pa.bool_(),
        int: pa.int64(),
        float: pa.float64(),
        str: pa.string(),
        bytes: pa.binary(),
        bytearray: pa.binary(),
        date: pa.date32(),
        datetime: pa.timestamp("us"),
        time: pa.time64("us"),
    }.get(tipo_py)


def coluna_arrow(valores, tipo):
    if tipo is not None:
        try:
            return pa.array(valores, type=tipo)
        except (pa.ArrowInvalid, pa.ArrowTypeError):
            pass  # ex.: datetimeoffset chega como datetime com fuso; deixa inferir
    return pa.array(valores)


def ler_tabela(conexao, schema, tabela):
    cursor = conexao.cursor()
    cursor.execute(f"SELECT * FROM {colchetes(schema)}.{colchetes(tabela)}")
    linhas = cursor.fetchall()
    colunas = list(zip(*linhas)) if linhas else [[] for _ in cursor.description]
    return pa.table({
        d[0]: coluna_arrow(list(valores), tipo_arrow(d))
        for d, valores in zip(cursor.description, colunas)
    })


def para_parquet(tabela):
    buffer = io.BytesIO()
    pq.write_table(tabela, buffer, compression="zstd")
    return buffer.getvalue()


# ---------------------------------------------------------------- Supabase

def enviar_supabase(url_projeto, chave, bucket, caminho, dados):
    destino = f"{url_projeto.rstrip('/')}/storage/v1/object/{bucket}/{quote(caminho)}"
    resposta = requests.post(
        destino,
        headers={
            "Authorization": f"Bearer {chave}",
            "apikey": chave,
            "x-upsert": "true",  # sobrescreve o arquivo do dia se já existir
            "Content-Type": "application/octet-stream",
        },
        data=dados,
        timeout=600,
    )
    if resposta.status_code >= 300:
        raise RuntimeError(f"Falha ao enviar {caminho}: {resposta.status_code} {resposta.text}")


# ---------------------------------------------------------------- principal

def main():
    tabelas = [t.strip() for t in re.split(r"[,\n]", env("TABELAS", obrigatoria=True)) if t.strip()]
    sb_url = env("SUPABASE_URL", obrigatoria=True)
    sb_chave = env("SUPABASE_SECRET_KEY", obrigatoria=True)
    bucket = env("SUPABASE_BUCKET", obrigatoria=True)
    prefixo = env("PREFIXO", "dataflow").strip("/")
    fuso = ZoneInfo(env("FUSO", "America/Fortaleza"))
    dia = env("DATA_SNAPSHOT") or datetime.now(fuso).date().isoformat()

    conexao = conectar()
    falhas = []
    for nome in tabelas:
        schema, tabela = separar_tabela(nome)
        try:
            dados = ler_tabela(conexao, schema, tabela)
            dados = dados.append_column(
                "_data_snapshot", pa.array([date.fromisoformat(dia)] * dados.num_rows, pa.date32())
            )
            arquivo = para_parquet(dados)
            nome_slug = slug(nome)
            destino = f"{prefixo}/{nome_slug}/{nome_slug}.parquet"
            enviar_supabase(sb_url, sb_chave, bucket, destino, arquivo)
            print(f"OK  {nome}: {dados.num_rows:,} linhas, {len(arquivo) / 1e6:.1f} MB -> {bucket}/{destino}")
        except Exception as erro:  # segue para as próximas tabelas e falha no final
            print(f"ERRO  {nome}: {erro}", file=sys.stderr)
            falhas.append(nome)
    conexao.close()

    if falhas:
        sys.exit(f"Falha em {len(falhas)} tabela(s): {', '.join(falhas)}")


if __name__ == "__main__":
    main()
