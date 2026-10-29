# Snapshot diário do Azure SQL Database → Supabase Storage

Copia todo dia tabelas do Azure SQL Database para o Supabase Storage, em Parquet.

Cada tabela vira um único arquivo, que é sobrescrito a cada execução. O bucket guarda sempre só a versão mais recente, sem histórico.

## 1. Visão geral

```
┌──────────────────────┐      ┌───────────────────────┐      ┌──────────────────────────┐
│  Azure SQL Database  │      │  snapshot_dataflow.py │      │  Supabase Storage        │
│  dbo.Vendas          │ ───► │  SELECT *, adiciona   │ ───► │  bucket privado          │
│  dbo.Clientes ...    │      │  coluna, gera Parquet │      │  .../<tabela>.parquet    │
└──────────────────────┘      └───────────────────────┘      └──────────────────────────┘
                                  agendado no GitHub Actions (1x/dia)
```

O script faz o seguinte:

1. Conecta no banco pelo driver ODBC 18 da Microsoft.
2. Para cada tabela listada em `TABELAS`, lê todas as linhas com `SELECT *`.
3. Mantém os tipos do banco no Parquet:

   | Tipo no SQL Server | Tipo no Parquet |
   |---|---|
   | `tinyint`, `smallint`, `int`, `bigint` | `int64` |
   | `decimal`, `numeric`, `money` | `decimal` com a mesma precisão e escala |
   | `float`, `real` | `float64` |
   | `bit` | booleano |
   | `date` | data |
   | `datetime`, `datetime2`, `smalldatetime` | timestamp sem fuso |
   | `datetimeoffset` | timestamp convertido para UTC |
   | `time` | hora |
   | `char`, `varchar`, `nchar`, `nvarchar`, `uniqueidentifier` | texto |
   | `binary`, `varbinary` | binário |

4. Adiciona a coluna `_data_snapshot`, com a data da execução (`AAAA-MM-DD`).
5. Gera um Parquet com compressão zstd e o envia ao Supabase com upsert (`x-upsert: true`).

Se uma tabela falhar (por exemplo, nome errado ou falta de permissão), o script segue com as demais e, no final, termina com erro listando as que falharam.

## 2. Pré-requisitos

- Python 3.12 (é a versão usada no workflow).
- **Driver ODBC 18 for SQL Server** da Microsoft. No GitHub Actions, o workflow instala o driver se ele não vier no runner. Para rodar no Windows, instale o "Microsoft ODBC Driver 18 for SQL Server" pelo site da Microsoft.
- **Azure SQL**: um login com permissão de leitura nas tabelas. Use uma das duas opções:
  - **usuário SQL** (`SQL_USER` / `SQL_PASSWORD`) com o papel `db_datareader`; ou
  - **service principal do Entra ID** (`AZURE_CLIENT_ID` / `AZURE_CLIENT_SECRET`). O app precisa de um usuário no banco. Rode no banco, conectado como administrador do Entra:

    ```sql
    CREATE USER [nome-do-app] FROM EXTERNAL PROVIDER;
    ALTER ROLE db_datareader ADD MEMBER [nome-do-app];
    ```
- **Firewall do Azure SQL**: o servidor precisa aceitar conexões de onde o script roda. A opção "Permitir que serviços do Azure acessem" **não** inclui o GitHub Actions. Veja a seção 9.
- **Supabase**:
  - um bucket **privado** no Storage;
  - a chave **secret** do projeto (ou a `service_role` legada). Ela ignora RLS, então guarde-a só em segredos.

## 3. Variáveis de ambiente

| Variável | Obrigatória | Onde fica no GitHub | Descrição |
|---|---|---|---|
| `SQL_SERVER` | sim | Variable | Ex.: `meuservidor.database.windows.net` |
| `SQL_DATABASE` | sim | Variable | Nome do banco |
| `TABELAS` | sim | Variable | Tabelas separadas por vírgula ou uma por linha, no formato `schema.tabela`. Sem schema, usa `dbo`. Ex.: `dbo.Vendas,Clientes` |
| `SUPABASE_URL` | sim | Variable | Ex.: `https://xxxx.supabase.co` |
| `SUPABASE_BUCKET` | sim | Variable | Bucket de destino |
| `SUPABASE_SECRET_KEY` | sim | **Secret** | Chave secret (ou `service_role` legada) |
| `SQL_USER` | * | **Secret** | Usuário SQL |
| `SQL_PASSWORD` | * | **Secret** | Senha do usuário SQL |
| `AZURE_CLIENT_ID` | * | **Secret** | Client ID do service principal |
| `AZURE_CLIENT_SECRET` | * | **Secret** | Segredo do service principal |
| `PREFIXO` | não | Variable | Pasta raiz no bucket. Padrão: `dataflow` |
| `FUSO` | não | Variable | Fuso usado para calcular a data do snapshot. Padrão: `America/Fortaleza` |
| `SQL_DRIVER` | não | — | Nome do driver ODBC. Padrão: `ODBC Driver 18 for SQL Server` |
| `DATA_SNAPSHOT` | não | — | Força a data da coluna `_data_snapshot` (`AAAA-MM-DD`). Para uso pontual (veja a seção 7) |

\* Use o usuário SQL (`SQL_USER` + `SQL_PASSWORD`) **ou** o service principal (`AZURE_CLIENT_ID` + `AZURE_CLIENT_SECRET`). Se `SQL_USER` estiver preenchido, ele tem prioridade.

Variáveis vazias valem o mesmo que não definidas. Por isso, o workflow já repassa `PREFIXO` e `FUSO`, e você só precisa cadastrá-las se for mudar o padrão.

### Exemplo de `TABELAS`

| O que você quer | Valor |
|---|---|
| Uma tabela no schema `dbo` | `Vendas` ou `dbo.Vendas` |
| Tabelas de schemas diferentes | `dbo.Vendas,fin.ContasReceber` |
| Nome com espaço | `dbo.Vendas Diarias` (sem colchetes e sem aspas) |
| Lista longa | uma tabela por linha, colada direto no campo Value do GitHub |

O nome da pasta no bucket sai do texto **como você escreveu** em `TABELAS`: tira acentos, passa para minúsculas e troca o que não for letra ou número por `_`. Assim, `dbo.Vendas` vira `dbo_vendas` e `Vendas` vira `vendas`. Escolha um formato e mantenha, para a mesma tabela não acabar em duas pastas.

### Views do Azure (`azure/`)

Algumas fontes são views criadas no Azure SQL, que o snapshot lê como qualquer tabela (`SELECT *`). O SQL delas fica em [azure/](azure/) e é criado à mão no banco (SSMS ou Query editor do portal); depois, a view entra em `TABELAS`.

- [f_lancamentos_agrupados_pj.sql](azure/f_lancamentos_agrupados_pj.sql) — `dbo.f_Lancamentos_Agrupados_PJ`: a DRE da PJ Cortes por conta (Empresa, Data, Id_Descritivo, Valor), a mesma regra do Power BI. Depende de `d_De_Para_PJ_Cortes` e `d_Plano_Contas` no mesmo banco.

## 4. Como rodar localmente

```bash
pip install -r requirements.txt
```

Copie o `.env.example` para `.env` e preencha os valores. O script não lê o `.env` sozinho; no Bash, carregue o arquivo antes de rodar:

```bash
set -a; source .env; set +a
python snapshot_dataflow.py
```

No Bash, valores com espaço precisam de aspas no `.env`, por exemplo `TABELAS="dbo.Vendas Diarias,dbo.Clientes"`.

**No PowerShell:**

```powershell
$env:SQL_SERVER = "meuservidor.database.windows.net"
$env:SQL_DATABASE = "meubanco"
$env:TABELAS = "dbo.Vendas,dbo.Clientes"
$env:SQL_USER = "..."
$env:SQL_PASSWORD = "..."
$env:SUPABASE_URL = "https://xxxx.supabase.co"
$env:SUPABASE_SECRET_KEY = "sb_secret_..."
$env:SUPABASE_BUCKET = "snapshots"

python snapshot_dataflow.py
```

Lembre de liberar o IP da sua máquina no firewall do Azure SQL. Para cada tabela enviada, o script imprime uma linha:

```
OK  dbo.Vendas: 152,340 linhas, 4.2 MB -> snapshots/dataflow/dbo_vendas/dbo_vendas.parquet
```

## 5. Configurar o GitHub Actions

1. Crie um repositório **privado**. Em repositório público, o GitHub desativa os agendamentos depois de 60 dias sem atividade.
2. Suba `snapshot_dataflow.py`, `requirements.txt` e o workflow, que já está em `.github/workflows/snapshot-dataflow.yml`.
3. Em **Settings → Secrets and variables → Actions**:
   - na aba **Secrets**, cadastre `SUPABASE_SECRET_KEY` e as credenciais do banco: `SQL_USER` e `SQL_PASSWORD`, **ou** `AZURE_CLIENT_ID` e `AZURE_CLIENT_SECRET`;
   - na aba **Variables**, cadastre `SQL_SERVER`, `SQL_DATABASE`, `TABELAS`, `SUPABASE_URL`, `SUPABASE_BUCKET` e, se quiser, `PREFIXO` e `FUSO`.
4. Libere o acesso do GitHub Actions no firewall do Azure SQL (seção 9).
5. Revise o horário. O cron `0 12 * * *` corresponde a 12:00 UTC, ou seja, **09:00 em Fortaleza**. Ele precisa cair **depois da carga das tabelas** que você quer copiar. Lembre que o cron do GitHub é sempre em UTC e que execuções agendadas podem atrasar alguns minutos.
6. Faça o primeiro teste manual: abra a aba **Actions**, escolha **snapshot-dataflow**, clique em **Run workflow** e confira no log as linhas `OK ...`. Depois verifique os arquivos no bucket.

Quando uma execução falha, o GitHub avisa por e-mail quem configurou o agendamento (conforme as preferências de notificação da conta).

## 6. Arquivos gerados no bucket

```
<bucket>/
└── <PREFIXO>/                      (padrão: dataflow)
    └── <tabela>/
        └── <tabela>.parquet        (sobrescrito a cada execução)
```

Fica sempre só a versão mais recente de cada tabela, sem histórico. A coluna `_data_snapshot` mostra de que dia é o arquivo.

### Ler com pandas

Baixe o arquivo pela API do Storage (o bucket é privado) e abra com pandas:

```python
import io, requests, pandas as pd

url = "https://xxxx.supabase.co/storage/v1/object/snapshots/dataflow/dbo_vendas/dbo_vendas.parquet"
r = requests.get(url, headers={"Authorization": f"Bearer {CHAVE}", "apikey": CHAVE})
r.raise_for_status()
df = pd.read_parquet(io.BytesIO(r.content))
```

### Ler com DuckDB

O DuckDB lê o bucket pelo endpoint S3 do Supabase. Gere as chaves de acesso S3 em **Project Settings → Storage**.

```sql
INSTALL httpfs; LOAD httpfs;

CREATE SECRET supabase (
    TYPE s3,
    KEY_ID 'sua_access_key',
    SECRET 'sua_secret_key',
    ENDPOINT 'xxxx.supabase.co/storage/v1/s3',
    REGION 'sa-east-1',          -- a região do seu projeto
    URL_STYLE 'path'
);

SELECT _data_snapshot, count(*)
FROM read_parquet('s3://snapshots/dataflow/dbo_vendas/dbo_vendas.parquet')
GROUP BY ALL;
```

## 7. Rodar de novo

Para atualizar o arquivo fora do horário agendado, use **Run workflow** no GitHub. A execução substitui o arquivo de cada tabela pela versão atual do banco.

O script sempre lê o estado **atual** das tabelas. `DATA_SNAPSHOT=AAAA-MM-DD` só muda a data gravada na coluna `_data_snapshot`, não os dados lidos. Ela serve, por exemplo, quando a execução agendada falhou e você roda depois da meia-noite, mas quer manter a data do dia anterior. O workflow não repassa essa variável; para usá-la, rode localmente (seção 4) ou acrescente-a ao `env:` do workflow temporariamente.

Como cada execução sobrescreve a anterior, não há como recuperar a versão de dias passados pelo bucket.

## 8. Limitações conhecidas

- **Memória:** cada tabela é carregada inteira em memória (todas as linhas e o Parquet) antes do envio.
- **Leitura completa:** o script sempre faz `SELECT *` da tabela inteira; não há filtro nem carga incremental.
- **Tamanho por arquivo:** o Supabase Storage limita o tamanho de cada arquivo, e o limite depende do plano (no gratuito, 50 MB). O script envia cada arquivo em uma única requisição. Para tabelas muito grandes, seria preciso implementar upload em partes (resumable/TUS) ou dividir o arquivo.
- **Tipos não suportados pelo pyodbc**, como `sql_variant`, `geography` e `geometry`, fazem a leitura da tabela falhar.
- **Tabela vazia** gera um Parquet só com as colunas.

## 9. Solução de problemas

**Firewall do Azure SQL** (`Cannot open server ... requested by the login. Client with IP address ... is not allowed`):
- Os runners do GitHub usam IPs variáveis, então não basta liberar um IP fixo. As opções são:
  - liberar a faixa toda (`0.0.0.0` a `255.255.255.255`) no firewall do servidor e depender só da autenticação. É simples, mas expõe o servidor à internet;
  - usar um runner self-hosted com IP fixo, liberado no firewall;
  - usar GitHub larger runners com IP estático (recurso pago).
- "Permitir que serviços e recursos do Azure acessem este servidor" não cobre o GitHub Actions.

**Tempo esgotado na conexão** (`Login timeout expired`, `HYT00` ou erro `40613`): costuma ser um banco **serverless** pausado. Ele leva um minuto ou mais para acordar. O script tenta conectar até 5 vezes, com 30 segundos de intervalo (`TENTATIVAS` e `ESPERA` no início da seção Azure SQL do script). Se esgotar as tentativas, confira o firewall e se o banco está online no portal.

**Erro de login** (`Login failed for user ...`):
- Com usuário SQL, confira `SQL_USER` e `SQL_PASSWORD`, e se o usuário existe **nesse banco** (`SQL_DATABASE`).
- Com service principal, confira se o `CREATE USER ... FROM EXTERNAL PROVIDER` foi rodado no banco certo e se o segredo do app não expirou.

**Sem permissão na tabela** (`The SELECT permission was denied on the object ...`): adicione o usuário ao papel `db_datareader`, ou conceda `SELECT` no schema ou na tabela.

**Tabela não encontrada** (`Invalid object name ...`): confira o schema e o nome em `TABELAS`. Sem schema, o script usa `dbo`.

**Driver não encontrado** (`Can't open lib 'ODBC Driver 18 for SQL Server'` ou `Data source name not found`): instale o driver ODBC 18. Se tiver outra versão instalada, informe o nome dela em `SQL_DRIVER`. O service principal exige o driver 17.3 ou mais novo.

**401/403 no Supabase** (`Falha ao enviar ...: 401` ou `403`):
- Use a chave **secret** (ou `service_role`), não a publishable/anon.
- Confira `SUPABASE_URL` (sem barra extra ou caminho) e se a chave é do mesmo projeto.
- Se aparecer `Bucket not found`, confira `SUPABASE_BUCKET`. Um `413` ou erro de tamanho indica o limite da seção 8.

**`Variável de ambiente obrigatória ausente: ...`**: a variável não foi definida ou está vazia. No GitHub, confira se ela foi cadastrada na aba certa (Secrets ou Variables).
