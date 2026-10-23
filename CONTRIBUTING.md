# Como contribuir

## Branches

| Branch           | Sai de | PR para | Para quê                                 |
| ---------------- | ------ | ------- | ---------------------------------------- |
| `feature/<nome>` | `dev`  | `dev`   | qualquer mudança (também aceita `feat/`) |
| `release/X.Y.Z`  | `dev`  | `main`  | levar o que está na `dev` para produção  |
| `hotfix/X.Y.Z`   | `main` | `main`  | correção urgente em produção             |

Ninguém faz push direto em `main` nem em `dev`: tudo entra por PR.

## Fluxo de uma PR

1. Atualize a branch de origem (`git pull`) e crie a sua a partir dela.
2. Antes do push, rode as mesmas checagens do CI, em `api/` e em `jobs/carga/`:
   ```bash
   uv run ruff format --check . && uv run ruff check . && uv run mypy app && uv run pytest -m "not db"   # api/
   uv run ruff format --check . && uv run ruff check . && uv run mypy && uv run pytest -m "not integracao"   # jobs/carga/
   ```
   Os testes com banco (`db`, `integracao`) precisam do Supabase local e ainda não rodam no CI.
3. Abra a PR para o destino da tabela. O CI (`api` e `carga`) roda sozinho.
4. Quando a PR for aprovada, a automação ([pr-automacao.yml](.github/workflows/pr-automacao.yml)) espera o CI passar e faz o merge. Em `release/` e `hotfix/`, ela também cria a tag `vX.Y.Z`, a release e leva a `main` de volta para a `dev`. Não faça merge à mão.
5. Se a automação disser que a branch está desatualizada, faça merge do destino na sua branch e peça nova aprovação.

Migrations, scripts de produção e regras provisórias: ver [supabase/README.md](supabase/README.md) e [TODO.md](TODO.md). Mudança em `supabase/producao/` não roda sozinha: alguém aplica no SQL Editor depois do merge.

## Configuração do repositório (só o dono)

Feita uma vez, em Settings no GitHub:

1. **Branch `dev`** criada a partir da `main`.
2. **GitHub App** (Settings da conta > Developer settings > GitHub Apps), instalado neste repositório, com permissões de repositório: Contents, Pull requests e Workflows em leitura e escrita; Checks, Commit statuses e Metadata em leitura. Em Settings > Secrets and variables > Actions: variável `APP_ID` e segredo `APP_PRIVATE_KEY` (a chave `.pem` do App). O mesmo App serve para o front e o back.
3. **Rulesets** (Settings > Rules > Rulesets) para `main` e `dev`: PR obrigatória com 1 aprovação, aprovação do dono do código (CODEOWNERS), checks obrigatórios `api` e `carga`, bloqueio de force push e de exclusão. No bypass: o GitHub App e o papel Admin.
4. **Colaboradores** com papel Write, não Admin (Admin passa por cima dos rulesets). Os segredos do `diario.yml` (banco, Supabase, Azure) ficam visíveis para workflows de quem tem Write: só convide quem pode ter esse acesso.
