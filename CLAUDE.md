# CLAUDE.md — Overnex backend

Painel financeiro e comercial dos clientes da LANN. Este repositório tem o banco (Supabase), a API que serve o front e os jobs diários que trazem os dados. O front fica em outro repositório (`overnex-frontend`, React + Vite) e só fala com esta API, em `/api/*` no mesmo domínio (sem CORS).

## Regras de trabalho

- **Tudo em português:** código, comentários, mensagens de erro, docs, commits e respostas.
- **Atualize o [TODO.md](TODO.md) no mesmo trabalho**, no formato dele: "Última atualização: dd/mm/aaaa" e as seções "A fazer", "A decidir" e "Concluído" (cada item concluído com data dd/mm/aaaa).
- **Não faça commit nem push** sem pedido. O fluxo de branches e PRs está em [CONTRIBUTING.md](CONTRIBUTING.md): `feat/*` → PR para `dev`; `release/X.Y.Z` → PR para `main`.
- **Diga o que foi e o que não foi testado.** Os testes com banco (`db` na API, `integracao` na carga) e o pgTAP precisam do Supabase local (Docker), que nem toda máquina tem. Escreva-os assim mesmo. SQL novo sem banco à mão: confira com o parser do Postgres (`uv run --no-project --with pglast python …`) e avise que não rodou contra um banco.
- **Textos que o front mostra saem prontos da API.** O front não calcula número, status nem mensagem.

## Mapa

| Pasta | O que é | Detalhes |
|---|---|---|
| [api/](api/) | FastAPI: login, checagem de acesso e o JSON de cada tela | [api/README.md](api/README.md) |
| [supabase/](supabase/) | migrations, seed (base fictícia "Padrão LANN"), testes pgTAP, scripts de produção | [supabase/README.md](supabase/README.md) |
| [jobs/snapshot/](jobs/snapshot/) | Azure SQL → Parquet no Supabase Storage (9h de Fortaleza) | [jobs/snapshot/README.md](jobs/snapshot/README.md) |
| [jobs/carga/](jobs/carga/) | Parquet do Storage → `mart` | [jobs/carga/README.md](jobs/carga/README.md) |
| [.github/workflows/](.github/workflows/) | `ci.yml` (checks `api` e `carga`), `diario.yml` (snapshot + carga), `convites.yml` (manda os convites da fila da Gestão), `pr-automacao.yml` (merge automático depois da aprovação) | |

## Comandos

```bash
# api/ — as mesmas checagens do CI
uv run ruff format --check . && uv run ruff check . && uv run mypy app && uv run pytest -m "not db"
uv run fastapi dev --port 8001                       # http://127.0.0.1:8001/docs (AMBIENTE=dev; o proxy do front aponta para a 8001)
uv run python -m scripts.export_openapi              # regenera api/openapi.json (o front gera os tipos dele)
uv run python -m app.cli.convidar_usuario --help     # cadastro de usuário pela LANN

# jobs/carga/
uv run ruff format --check . && uv run ruff check . && uv run mypy && uv run pytest -m "not integracao"

# raiz (precisa de Docker)
supabase start && supabase db reset && supabase test db
```

Python 3.12 com `uv`; mypy em modo strict, ruff com linha de 100.

## Como a API funciona

- **Login:** e-mail e senha no Supabase Auth, pelas rotas `POST /api/auth/*` (`app/auth/`). A API repassa ao GoTrue; o refresh token fica no cookie httpOnly `ovx_refresh` e o access token só na memória do front. Só a LANN cadastra usuários (pela Gestão ou pelo CLI acima; depois de criada, a pessoa é editada pela Gestão). Sessão parada não renova: o limite é de cada pessoa (`seg.usuario.sessao_minutos` ou o padrão do perfil: admin 24 h, consultor 8 h, clientes 4 h), e o último uso (pedido autenticado ou renovação) fica em `seg.sessao_uso` (`app/auth/uso.py`). O Entra ID existe no código (`validar_token_entra`), desligado por `AUTH_PROVEDOR`. Contrato das rotas e configuração do painel do Supabase: [api/README.md](api/README.md#login-com-e-mail-e-senha-apiauth).
- **Perfis:** cada pessoa tem um dos 4 perfis fixos (`seg.usuario.perfil`, em `app/seguranca/perfis.py`): `admin` (Admin BORD: vê todos os clientes, sem vínculo, e abre a Gestão), `consultor`, `cliente_principal` e `cliente_usuario` (só os clientes dos vínculos). `seg.usuario.tipo` não dá acesso a nada. Por cliente, `cfg.cliente_tela` diz quais painéis estão ligados e os 3 perfis fixos de `cfg.perfil_acesso` dizem quem vê cada um.
- **Acesso em 6 etapas** (`app/seguranca/acesso.py`): usuário ativo → cliente escolhido → papel → tela ligada no cliente e liberada ao perfil → empresa no vínculo → dado nominal. Os fatos vêm de `seg.contexto_acesso` numa ida ao banco, e as decisões são tomadas na API. Toda decisão vai para `seg.log_acesso`; a negação é sempre `403 {"detail": "Acesso negado."}`, sem dizer a etapa.
- **Cliente escolhido:** `?cliente=<slug>` em `/api/sessao`, `/api/telas/*` e `/api/dados/*`. Opcional para quem tem um cliente só; o admin e quem tem mais de um vínculo precisam mandar.
- **Uma transação por requisição** (`app/core/db.py`) com `app.usuario_id` e `app.cliente_id` definidos nela. O RLS do banco só mostra o cliente escolhido: **o cliente e a empresa da requisição são só uma escolha entre o que o banco libera à pessoa, nunca uma permissão.**
- **Gestão** (`app/admin/`, `/api/admin/*`, só o admin): empresas com as camadas (e a criação de empresa nova, com nome, slug e setor, e a edição de nome e setor; CNPJs e fontes continuam por script), painéis ligados por cliente, matriz perfil × painel, pessoas (perfil, empresas, sessão, desativar), preferências, feriados e últimas cargas. Convite e reenvio por fila: a API grava o pedido em `seg.convite_fila` e aciona o job `convites.yml`, que manda o e-mail com a chave secreta (`app/admin/convites.py` e `app/cli/enviar_convites.py`); a API não cria login. Catálogo dos painéis e a regra de "tem dado" em `app/admin/catalogo.py` (provisória).
- **Telas:** `GET /api/sessao` (moldura do front: perfil, clientes que a pessoa pode abrir e, do cliente escolhido, empresas, telas e períodos), `GET /api/telas/visao-geral` (VG-01, seções em paralelo, cada uma com a sua conexão; resposta em cache por 1 h, com a última carga e a versão do cadastro do cliente na chave; as escolhas salvas do cliente, como as linhas de custo, gravadas por `PUT /api/telas/visao-geral/preferencias/{id}`) e os painéis por cartões `GET /api/telas/vendas|equipe-comercial|clientes` (COM-01 a 03) `resultado|receber-pagar|fluxo-caixa` (FIN-02 a 04; o fluxo de caixa com a projeção de 60 dias e as provisões que quem vê a tela grava por `PUT /api/telas/fluxo-caixa/cartoes/provisoes/periodo|grupos/{grupo}`) e `pessoas` (PES-01, folha sem nomes, só diretoria e RH pelo cadastro; os nomes de "Cargos e salários" só para o perfil com dado nominal, em `GET /api/telas/pessoas/cartoes/cargos-salarios/nomes`), em `app/telas/paineis/`, mesmo padrão, com filtros próprios. Cartões "A decidir" do mockup só com `PAINEIS_A_DECIDIR=1` (dev). Contratos em Pydantic camelCase (`ModeloCamel`). Muitas regras de indicador são provisórias: veja o TODO antes de mudar números.
- **Latência:** o banco fica em us-east-1, a ~120 ms do Brasil por ida e volta. Contar idas ao banco importa mais que otimizar uma consulta.

## Banco

- Schemas `cfg` (cadastros), `seg` (usuários, vínculos, logs), `mart` (dados), sem nada em `public`. RLS ligado e forçado em tudo.
- Roles: `api_site` (API; lê o cliente escolhido e insere nos logs; com um usuário admin, lê o cadastro de todos os clientes e grava o que a Gestão muda, pelas políticas `seg.eh_admin()`) e `carga` (jobs; em `seg`, só as duas funções da fila de convites). Nenhum dos dois tem BYPASSRLS.
- **Migrations:** numeradas (`AAAAMMDD0000NN_nome.sql`), nunca edite uma que já foi aplicada; crie outra. Comente no topo o porquê. Liste a nova em [supabase/README.md](supabase/README.md). Quem aplica em produção é uma pessoa, com `supabase db push`; se a API nova depende da migration, diga que ela vai antes.
- **`supabase/producao/`:** scripts por cliente para o SQL Editor, rodados à mão. Não são migrations nem rodam no `db reset`.

## Cuidados

- **Segredos:** nunca exiba valores de `.env` (`api/.env`, `jobs/carga/.env`). Para conferir, mostre só o nome da variável, o prefixo da chave ou um hash. A chave secreta do Supabase (`sb_secret_…`) é só do CLI de cadastro e dos jobs (snapshot, carga e convites), nunca do ambiente da API publicada; a API usa a publishable.
- **O `api/.env` local pode apontar para o banco de produção.** Antes de qualquer coisa que escreva no banco a partir da API local, confira para onde ele aponta.
- **Pergunte antes de rodar o CLI do Supabase contra o projeto remoto** (`--linked`, `db push`, `db reset --linked`).
- **Nunca registre senha nem token em log.** Erro de configuração não ecoa valores (`hide_input_in_errors`).
- `AUTH_FAKE=1` (token `fake:<id_autenticacao>`) e `VISAO_GERAL_DEMO=1` só valem com `AMBIENTE=dev`; a API se recusa a subir com eles em outro ambiente.
- **Mudou um contrato que o front usa?** Regenere o `openapi.json` e entregue um resumo (ou um prompt pronto) para quem mexe no front.
