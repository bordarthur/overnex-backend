# Overnex — carga diária

CLI que lê os Parquet do snapshot no Supabase Storage e alimenta o `mart`. Roda logo depois do snapshot (job `carga` em [.github/workflows/diario.yml](../../.github/workflows/diario.yml)), mas não depende de onde roda: tudo vem de variáveis de ambiente. Trocar o GitHub Actions por um Container Apps Job no Azure mexe só no agendamento.

```
Azure SQL ──snapshot (9h)──► Storage: overnex-bucket/<PREFIXO>/<tabela>/<tabela>.parquet
                                         │
                                         ▼  carga (role carga, pooler em modo sessão)
                              mart.lancamento + mart.carga + mart.carga_fonte
```

## O que a carga faz

Cada fonte ativa em `cfg.fonte_carga` tem um tipo:

- **`tabela`**: o Parquet entra como está numa tabela do mart com as mesmas colunas (seção [Tabelas de dados](#tabelas-de-dados-tipo-tabela));
- **`lancamento`**: o formato agregado `Empresa`, `Data`, `Id_Descritivo`, `Valor` vira `mart.lancamento` (abaixo).

Para uma fonte `lancamento`:

1. baixa o Parquet (`GET /storage/v1/object/<bucket>/<caminho_bucket>`);
2. valida a data do snapshot. Se falhar, para sem tocar no mart:
   - todas as linhas têm o mesmo `_data_snapshot`;
   - se `DATA_ESPERADA` veio, `_data_snapshot` é igual a ela;
   - se `EXIGIR_SNAPSHOT_NOVO=true`, `_data_snapshot` é maior que o da última carga `ok` da mesma fonte (arquivo não renovado = o snapshot daquela tabela falhou);
3. valida colunas (`Empresa`, `Data`, `Id_Descritivo`, `Valor`), tipos e nulos;
4. em **uma** transação: temp table + `COPY`, `Empresa` → `empresa_id` por `cfg.mapa_empresa` do cliente (Empresa sem mapeamento = falha listando os valores), apaga de `mart.lancamento` **todas** as empresas mapeadas do cliente e insere a soma por empresa, mês (primeiro dia de `Data`) e `Id_Descritivo`, com `round(sum(valor::numeric), 2)`;
5. commit só se tudo deu certo. Até o commit, quem está no painel vê o dado anterior; nunca um mart vazio no meio da troca.

Cada execução grava uma linha em `mart.carga` e uma por fonte em `mart.carga_fonte` (data do snapshot, status, linhas lidas e gravadas, mensagem). Cada fonte roda na sua transação: a falha de uma não impede as outras. `mart.carga` termina `ok` só se todas as fontes deram certo.

O "dados atualizados em" da API é a `data_snapshot` da última `mart.carga_fonte` `ok` do cliente.

Códigos de saída: `0` tudo ok · `1` alguma fonte falhou · `2` não rodou (configuração inválida, banco fora, URL do pooler errada).

Não fazem parte desta entrega:
- `cfg.regra_lancamento` ainda não é aplicada (TODO de regra de negócio, em `carga/lancamento.py`);
- arquivo sem linhas é tratado como falha e o mart é mantido.

## Tabelas de dados (tipo `tabela`)

Cada Parquet do bucket pode virar uma tabela em `mart` com **as mesmas colunas** (mesmos nomes; texto → `text`, double → `numeric`, data → `date`) mais três colunas de sistema: `_linha` (posição no arquivo), `_cliente_id` e `_empresa_id`. A API as entrega ao front em `GET /api/dados/<tabela>`.

Para cada fonte `tabela`:

1. baixa o Parquet e valida a data do snapshot (mesmas regras de cima);
2. confere as colunas contra a tabela do mart:
   - coluna da tabela que falta no arquivo → **falha**, mart intacto;
   - coluna nova no arquivo, ainda sem coluna na tabela → **ignorada**, e a fonte termina `ok` com aviso em `mart.carga_fonte.mensagem`;
3. em **uma** transação: temp table + `COPY`; se a fonte tem `coluna_empresa`, traduz o valor por `cfg.mapa_empresa` (valor sem mapeamento → falha listando); apaga **todas** as linhas do cliente na tabela e insere as do arquivo;
4. commit só se tudo deu certo.

Configuração em `cfg.fonte_carga`:

| Coluna | Para quê |
|---|---|
| `tipo` | `tabela` |
| `caminho_bucket` | caminho do Parquet no bucket |
| `tabela_destino` | nome da tabela em `mart` (ex.: `f_vendas_nilo_maia`) |
| `coluna_empresa` | coluna do arquivo com a empresa, ex. `EMPRESA`. Vazia = a tabela é do cliente inteiro: todos os usuários do cliente veem todas as linhas |
| `contem_dado_nominal` | `true` = só perfis com `ve_dado_nominal` em `cfg.perfil_tabela` leem pela API |
| `coluna_data` + `meses_retidos` | janela de datas: só entram as linhas dos últimos N meses dessa coluna (veja abaixo). Vazias = a tabela entra inteira |

### Janela de datas

Para tabelas de movimento grandes (vendas, receitas, lançamentos…), a carga pode gravar só os últimos N meses:

- **Início da janela:** dia 1 do mês do `_data_snapshot` menos N-1. Snapshot de 14/10 com N = 3 → a partir de 01/08. A referência é o snapshot, nunca "hoje".
- **Data vazia:** a linha entra.
- **Formatos aceitos:** colunas `date` ou `timestamp`, e texto como `AAAA-MM-DD`, `DD/MM/AAAA` ou `AAAA/MM/DD`, com ou sem hora. Cada coluna precisa usar um formato só; senão, a fonte falha com exemplos dos valores e o mart fica intacto.
- **Quando filtra:** antes de qualquer coisa ir ao banco. As linhas fora da janela não ocupam nem a tabela temporária.
- **Registro:** quantas linhas ficaram de fora sai em `mart.carga_fonte.mensagem`, com status `ok`.
- **Cuidado:** não use em tabelas de posição (inadimplência, contas a pagar/receber, estoque, saldos). Um título antigo ainda em aberto sumiria.

### Tabela nova

1. Rode o [inventário](#inventário-do-bucket): ele traz, para cada arquivo, o `create table` sugerido.
2. Crie uma migration nova em `supabase/migrations/` com esse SQL. O `select mart.preparar_tabela_dados('<tabela>')` logo depois do `create table` aplica RLS, políticas, grants e índice.
3. Cadastre a fonte em `cfg.fonte_carga` e libere a tabela aos perfis em `cfg.perfil_tabela`.

### Coluna nova no DW

A carga continua funcionando e avisa. Para a coluna entrar no mart, crie uma migration com `alter table mart.<tabela> add column "<Nome>" <tipo>;`. O tipo está no inventário. Nunca renomeie nem apague colunas: o front lê pelos nomes.

## Variáveis

| Variável | Obrigatória | O que faz |
|---|---|---|
| `DATABASE_URL_CARGA` | sim | Conexão do role `carga` pelo **pooler do Supabase em modo sessão** (porta 5432 do pooler): `postgresql://carga.<project-ref>:<senha>@aws-0-<região>.pooler.supabase.com:5432/postgres`. Não use a conexão direta (só IPv6; o runner do GitHub não tem) nem o pooler em modo transação (6543, quebra o COPY e a transação longa; a carga recusa essa porta). |
| `SUPABASE_URL` | sim | Ex.: `https://xxxx.supabase.co` |
| `SUPABASE_SECRET_KEY` | sim | Chave **secret** do projeto (vai nos headers `apikey` e `Authorization: Bearer`). Só existe no ambiente da carga e do snapshot; a API nunca a recebe. |
| `SUPABASE_BUCKET` | não | Bucket dos Parquet. Padrão: `overnex-bucket` |
| `EXIGIR_SNAPSHOT_NOVO` | não | `true`/`false` (padrão `false`). No workflow: `true` no cron e `false` no disparo manual. |
| `DATA_ESPERADA` | não | `AAAA-MM-DD`. Se vier, todo arquivo precisa ter exatamente essa data em `_data_snapshot`. |

Variável vazia vale o mesmo que não definida.

## No GitHub Actions

Cadastre em **Settings → Secrets and variables → Actions** (no repositório, como o job `snapshot` já usa):

| Aba | Nome | Observação |
|---|---|---|
| **Secrets** | `DATABASE_URL_CARGA` | URL completa com a senha do role `carga` (contém segredo, por isso não é Variable) |
| **Secrets** | `SUPABASE_SECRET_KEY` | a mesma que o snapshot já usa |
| **Variables** | `SUPABASE_URL` | a mesma que o snapshot já usa |
| **Variables** | `SUPABASE_BUCKET` | a mesma que o snapshot já usa (`overnex-bucket`) |

`EXIGIR_SNAPSHOT_NOVO` e `DATA_ESPERADA` não são cadastradas: o workflow define a primeira pelo tipo de disparo e a segunda pelo input `data_esperada` do **Run workflow**.

Hoje o job `snapshot` não usa `environment`, então a carga também não usa (os dois leem os segredos do repositório). Se vocês criarem um environment (por exemplo, `producao`) para restringir os segredos, mova os Secrets para ele e coloque o mesmo `environment:` nos dois jobs.

A senha do role `carga` em produção é definida uma vez, fora das migrations (veja [supabase/README.md](../../supabase/README.md)):

```sql
alter role carga with login password '<segredo>';
```

### Rodar de novo

**Actions → snapshot-dataflow → Run workflow** roda snapshot e carga, com `EXIGIR_SNAPSHOT_NOVO=false`. Preencha `data_esperada` para garantir que a carga só aceite arquivos daquela data.

## Rodar local

Pré-requisitos: [uv](https://docs.astral.sh/uv/), [Supabase CLI](https://supabase.com/docs/guides/cli) e Docker.

```bash
# na raiz do repositório
supabase start             # Postgres, Storage e o bucket overnex-bucket (supabase/config.toml)
supabase db reset          # migrations + seed

cd jobs/carga
cp .env.example .env       # preencha SUPABASE_SECRET_KEY com o "Secret key" do `supabase status`
uv sync
uv run python -m carga
```

O bucket local começa vazio, então a carga local falha com "arquivo não existe" até você subir arquivos nos caminhos de `cfg.fonte_carga` (os testes fazem isso).

## Inventário do bucket

Lista os Parquet do bucket e descreve colunas, tipos, nulos, os valores distintos das colunas de empresa e o `create table` sugerido para o mart. Serve para escrever as migrations das tabelas de dados e o `cfg.mapa_empresa`. Só lê o Storage: não conecta no banco e não grava nada.

```bash
uv run python -m carga.inspecionar                  # prefixo dataflow → inventario_bucket.md
uv run python -m carga.inspecionar dataflow saida.md
```

Precisa de `SUPABASE_URL` e `SUPABASE_SECRET_KEY` (no `.env` ou no ambiente). O relatório fica fora do git (`.gitignore`), porque traz os nomes das empresas.

## Testes e qualidade

```bash
uv run ruff check
uv run ruff format --check
uv run mypy
uv run pytest                      # precisa do supabase start + db reset
uv run pytest -m "not integracao"  # só os testes sem banco/Storage
```

Os testes `integracao` geram Parquets com pyarrow, sobem no bucket local, rodam a carga e conferem o mart. Ao final de cada teste, o mart volta exatamente ao estado do seed (a API depende dele). Conectam em `127.0.0.1:54322` como `carga` e como `postgres` (para conferir) e no Storage em `127.0.0.1:54321`. A chave vem de `SUPABASE_SECRET_KEY` ou, se não definida, de `supabase status -o env`. Para sobrescrever: `DATABASE_URL_CARGA`, `DATABASE_URL_ADMIN`, `SUPABASE_URL`.

## Organização

```
carga/
  __main__.py     uv run python -m carga
  main.py         mart.carga / mart.carga_fonte, uma transação por fonte, resumo e código de saída
  config.py       Settings
  db.py           conexão do role carga (recusa o pooler em modo transação)
  storage.py      GET no Storage, com erro claro para 404 e 401/403
  fontes.py       cfg.fonte_carga ativas; recusa duas fontes no mesmo destino para o mesmo cliente
  arquivo.py      baixar, abrir o Parquet e validar a data (comum aos tipos)
  validacao.py    regras de data e de colunas (funções puras)
  tabela.py       carga do tipo 'tabela' (Parquet → mart.<tabela>, mesmas colunas)
  lancamento.py   carga do tipo 'lancamento' (formato agregado → mart.lancamento)
  esquema.py      tipos Parquet → Postgres e o create table das tabelas de dados
  janela.py       janela de datas (últimos N meses de uma coluna)
  inspecionar.py  inventário do bucket (python -m carga.inspecionar)
```
