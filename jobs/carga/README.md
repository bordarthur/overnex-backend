# Overnex — carga diária

CLI que lê os Parquet do snapshot no Supabase Storage e alimenta o `mart`. Roda logo depois do snapshot (job `carga` em [.github/workflows/diario.yml](../../.github/workflows/diario.yml)), mas não depende de onde roda: tudo vem de variáveis de ambiente. Trocar o GitHub Actions por um Container Apps Job no Azure mexe só no agendamento.

```
Azure SQL ──snapshot (9h)──► Storage: overnex-bucket/<PREFIXO>/<tabela>/<tabela>.parquet
                                         │
                                         ▼  carga (role carga, pooler em modo sessão)
                              mart.lancamento + mart.carga + mart.carga_fonte
```

## O que a carga faz

Para cada fonte ativa em `cfg.fonte_carga` (por enquanto só o tipo `lancamento`):

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
  fontes.py       cfg.fonte_carga ativas; recusa duas fontes do mesmo tipo para o mesmo cliente
  validacao.py    regras de data e de colunas (funções puras)
  lancamento.py   carga da fonte 'lancamento'
```
