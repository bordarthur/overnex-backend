# Overnex — banco (Supabase)

## Subir local

```bash
# na raiz do repositório (precisa de Docker)
supabase start
supabase db reset     # aplica migrations/ e seed.sql
supabase test db      # roda os testes pgTAP de tests/database/
```

Postgres local: `127.0.0.1:54322`. O seed define as senhas locais dos roles:

| Role | Senha local | Uso |
|---|---|---|
| `api_site` | `api_site_local` | API |
| `carga` | `carga_local` | carga diária (rodada 2) |

## Schemas

| Schema | Conteúdo |
|---|---|
| `cfg` | cadastros: cliente, empresa, painel, perfis, parâmetros, regras, fontes da carga, mapa de empresas |
| `seg` | usuários, vínculos (cliente, perfil, empresa), log de acesso e de alteração |
| `mart` | `lancamento` (dado das telas), `carga` e `carga_fonte` (controle da carga) |
| `stg` | reservado, sem tabelas: a carga usa tabela temporária |

Nada fica em `public`, e esses schemas não são expostos pelo PostgREST (`config.toml` mantém só `public` e `graphql_public`).

## Migrations

1. `..._schemas_e_tabelas.sql` — schemas e tabelas. Integridade entre clientes por FKs compostas: `mapa_empresa`, `usuario_empresa`, `usuario_perfil` e `carga_fonte` só aceitam registros do mesmo cliente.
2. `..._roles_e_grants.sql` — roles `api_site` e `carga` (sem BYPASSRLS) e grants.
3. `..._rls.sql` — ENABLE + FORCE RLS em todas as tabelas de `mart`, `cfg` e `seg`, as funções `seg.clientes_do_usuario()` / `seg.empresas_do_usuario()` (SECURITY DEFINER, STABLE, `search_path` fixo) e as políticas.
4. `..._carga_fonte_data_opcional.sql` — `mart.carga_fonte.data_snapshot` passa a aceitar null. A carga grava a fonte como `rodando` antes de baixar o arquivo e precisa registrar falhas sem data (arquivo inexistente, chave errada, `_data_snapshot` misturado). Linha `ok` continua exigindo a data (check), e a API só lê linhas `ok`.
5. `..._tabelas_de_dados.sql` — uma tabela no `mart` para cada Parquet do bucket, com as mesmas colunas (nomes preservados; texto → `text`, double → `numeric`, data → `date`) mais `_linha`, `_cliente_id` e `_empresa_id`. Inclui `cfg.perfil_tabela` (quais tabelas cada perfil lê), o tipo `tabela` em `cfg.fonte_carga` e a função `mart.preparar_tabela_dados()`, que aplica RLS, políticas, grants e índice a uma tabela nova. Para tabela ou coluna nova, veja [jobs/carga/README.md](../jobs/carga/README.md#tabelas-de-dados-tipo-tabela).
6. `..._janela_de_datas.sql` — `coluna_data` e `meses_retidos` em `cfg.fonte_carga`: a carga só grava as linhas dos últimos N meses de uma coluna de data (tabelas de movimento grandes).
7. `..._janela_meses_fechados.sql` — `meses_fechados` em `cfg.fonte_carga`: a janela termina no mês anterior ao do snapshot (só meses completos).
8. `..._resumo_mensal.sql` — `mart.resumo_mensal` (totais por mês das tabelas de movimento, calculados pela carga sobre o arquivo inteiro, antes da janela: histórico da Visão geral) e `cfg.parametro_cliente` (parâmetros do cliente inteiro, como as faixas do farol `farol.vendas_meta`).
9. `..._feriado.sql` — `cfg.feriado`: feriados do cliente (estaduais, municipais, ponto facultativo) para os dias úteis da projeção de vendas. Os nacionais vêm da biblioteca `holidays`, na API.
10. `..._resumo_dimensao.sql` — `dimensao` em `mart.resumo_mensal`: totais abertos por um valor do arquivo (ex.: faturado e custo por `en_ramo`, para o CMV por ramo). Vazio = total sem abertura.

### Storage

O `config.toml` declara o bucket privado `overnex-bucket`, criado no `supabase start`. Em produção, o bucket já existe (o snapshot grava nele). Só a carga lê o bucket, com a chave secret; a API nunca acessa o Storage.

### Quem vê o quê

- `api_site` só enxerga linhas a partir de `current_setting('app.usuario_id', true)`, que a API define a cada transação. Sem ele, nenhuma linha.
  - lê `cfg` e `mart.lancamento` / `mart.carga_fonte` do próprio cliente/empresas; `mart.carga` não;
  - em `seg`, lê só os próprios registros e só insere nos logs;
  - tem grant de escrita nos cadastros, mas ainda não há política de escrita, então o RLS nega (vem com o admin).
- `carga` escreve em `mart.lancamento`, `mart.carga` e `mart.carga_fonte`; lê só `cfg.fonte_carga`, `cfg.mapa_empresa` e `cfg.empresa`; cria tabela temporária; nenhum acesso a `seg`.

## Produção

Os roles nascem sem LOGIN e sem senha. Defina-os uma vez por ambiente, fora das migrations:

```sql
alter role api_site with login password '<segredo>';
alter role carga    with login password '<segredo>';
```

A migration 3 confere que o dono das funções (`postgres`) tem BYPASSRLS, como no Supabase; se não tiver, ela falha em vez de deixar as funções de segurança devolvendo vazio.

## Scripts de produção (`producao/`)

Cadastros de produção por cliente, para rodar à mão no SQL Editor. Não são migrations e não rodam no `db reset`.

- `nilo_maia_tabelas.sql` — fontes das 21 tabelas de dados da Nilo Maia. A de lançamentos passa a ser do tipo `tabela`; as outras entram inativas.
- `nilo_maia_janela.sql` — janela de 3 meses nas 12 tabelas de movimento (depois da migration 6). Não ativa fontes.

## Testes (pgTAP)

| Arquivo | O que garante |
|---|---|
| `01_isolamento_api_site` | o usuário do cliente A não lê nada do cliente B em `mart` (inclusive `carga_fonte`), `cfg` e `seg`; `api_site` não lê `mart.carga` nem os logs |
| `02_sem_usuario` | sem `app.usuario_id` (ou vazio), nenhuma linha |
| `03_role_carga` | `carga` não lê `seg`, não escreve em `cfg`, escreve no `mart` e cria tabela temporária |
| `04_integridade` | `cfg.mapa_empresa` (e os outros vínculos) recusam empresa de outro cliente; `mart.carga_fonte` `ok` exige `data_snapshot` |
| `05_tabelas_de_dados` | as 21 tabelas de dados têm RLS; nomes e tipos preservados; isolamento por cliente e por empresa; linhas sem empresa visíveis ao cliente inteiro; `carga` escreve, `api_site` só lê |
