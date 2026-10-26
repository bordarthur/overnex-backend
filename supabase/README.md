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
| `seg` | usuários (ligados ao Supabase Auth por `id_autenticacao`), vínculos (cliente, perfil, empresa), log de acesso e de alteração |
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
11. `..._colunas_modelo.sql` — `colunas_modelo` em `cfg.fonte_carga`: nomes das colunas por papel (data, valor, custo…) para o resumo mensal e a Visão geral, quando o DW do cliente não usa os nomes padrão (ex.: PJ Cortes).
12. `..._tabelas_pj_cortes.sql` — as 25 tabelas de dados da PJ Cortes, no padrão da migration 5.
13. `..._datas_pj_cortes.sql` — as 24 colunas de data das tabelas de fatos da PJ passam de texto para `date` (a carga converte o texto do arquivo). Só depois de a carga e a API estarem com o código que trata coluna `date`.
14. `..._conta_dre.sql` — `cfg.conta_dre`: de-para da conta do ERP do cliente (ex.: `en_codigoplanocliente` do Winthor) para o Id_Descritivo do plano gerencial, para as despesas do contas a pagar entrarem na DRE da seção Resultado. `fora_da_dre` = motivo de a conta não entrar. Conteúdo por cliente em `producao/<cliente>_contas_dre.sql`.
15. `..._usuario_id_autenticacao.sql` — login com e-mail e senha no Supabase Auth: `seg.usuario.object_id_entra` vira `id_autenticacao` (o `sub` do token, `auth.users.id`; continua `uuid not null unique`, sem FK para `auth.users`), entra `seg.usuario.nome` (cadastrado pela LANN, usado em `/api/sessao`), `seg.log_acesso.object_id_entra` vira `id_autenticacao` e `seg.resolver_usuario` passa a procurar pelo id do provedor. Os scripts de `producao/` que criam usuário já usam o nome novo: rode-os só depois desta migration.
16. `..._contexto_acesso.sql` — `seg.contexto_acesso(id_autenticacao, codigo_tela)`: numa chamada, define `app.usuario_id` e devolve os fatos da checagem de acesso (vínculos com cliente ativo, perfis que liberam a tela, empresas). SECURITY INVOKER: roda como `api_site`, sob o RLS. A API passa a depender dela: aplique antes de subir a API com o código de 23/10/2026 (tarde).

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
- `pj_cortes_cadastro.sql` — cliente, empresa, mapa de empresas, as 25 fontes (ativas, com `colunas_modelo` nas de vendas e metas), janela de 3 meses em 7 tabelas de movimento grandes (os títulos a receber e a pagar entram inteiros) e o nome da abertura do CMV (`custos.abertura`). Depois das migrations 11 e 12.
- `pj_cortes_titulos_sem_janela.sql` — tira a janela de `f_receitas_pj` e `f_despesas_pj`. Só se o cadastro da PJ rodou antes de 21/10/2026 à noite, quando ainda punha janela nelas.
- `pj_cortes_vendas_13_meses.sql` — janela de 13 meses em `f_vendas_pj` e as colunas da devolução da Agile (`colunas_modelo`). Só se o cadastro da PJ rodou antes de 22/10/2026.
- `pj_cortes_contas_dre.sql` — de-para das 238 contas do Winthor da PJ para o plano gerencial (gerado dos arquivos `Codigo_Sistema_x_Id_Descritivo` e `Plano_de_Conta` do Power BI), com as exclusões conhecidas da DRE do Power BI em `fora_da_dre`. Depois da migration 14; pode rodar de novo (troca todas as contas da PJ).
- `pj_cortes_telas_comerciais.sql` — painéis comerciais da PJ: `COM-01`, `COM-02` e `COM-03` no perfil "Desenvolvimento" (parte 1, pode rodar a qualquer hora) e os papéis `vendedor`, `linha` e `quantidade` nas duas tabelas de vendas (parte 2: só depois de a carga com o código dos painéis estar em `main`; ela passa a gravar o faturado por vendedor, para a rampa). A API acha essas colunas sem o script. Pode rodar de novo.
- `pj_cortes_usuario_dev.sql` — perfil "Desenvolvimento" da PJ Cortes; o usuário de desenvolvimento sai da Nilo Maia e entra na PJ. Depois da migration 15.
- `usuario_dev_supabase_auth.sql` — troca o `id_autenticacao` provisório do usuário de desenvolvimento pelo id dele no Supabase Auth (lê `auth.users` pelo e-mail). Antes, crie o usuário em Authentication → Users. Depois, o token fake antigo para de valer: o front que usa `VITE_AUTH_FAKE_OBJECT_ID` precisa do id novo (o script mostra).
- `nilo_maia_remover_tabelas.sql` — remove as tabelas `*_nilo_maia` do mart, com as fontes, o histórico de carga, o resumo mensal e as permissões delas. Mantém cliente, empresas e usuários.

## Testes (pgTAP)

| Arquivo | O que garante |
|---|---|
| `01_isolamento_api_site` | o usuário do cliente A não lê nada do cliente B em `mart` (inclusive `carga_fonte`), `cfg` e `seg`; `api_site` não lê `mart.carga` nem os logs |
| `02_sem_usuario` | sem `app.usuario_id` (ou vazio), nenhuma linha |
| `03_role_carga` | `carga` não lê `seg`, não escreve em `cfg`, escreve no `mart` e cria tabela temporária |
| `04_integridade` | `cfg.mapa_empresa` (e os outros vínculos) recusam empresa de outro cliente; `mart.carga_fonte` `ok` exige `data_snapshot` |
| `05_tabelas_de_dados` | as 46 tabelas de dados (Nilo Maia e PJ Cortes) têm RLS; nomes e tipos preservados; isolamento por cliente e por empresa; linhas sem empresa visíveis ao cliente inteiro; `carga` escreve, `api_site` só lê |
| `09_conta_dre` | cada cliente lê só o próprio de-para de contas; `api_site` não cadastra; conta sem Id_Descritivo precisa do motivo |
| `10_usuario_id_autenticacao` | `seg.usuario` com `id_autenticacao` (not null, único, sem FK) e `nome`; `seg.resolver_usuario` acha o usuário pelo id do provedor |
| `11_contexto_acesso` | `seg.contexto_acesso` define `app.usuario_id` e devolve vínculos, perfis da tela e empresas do usuário (na ordem por nome); desconhecido: listas vazias |
