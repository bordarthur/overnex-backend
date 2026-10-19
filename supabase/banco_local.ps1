# Recria o banco local SEM Docker: aplica as migrations e o seed num Postgres 17 instalado no
# Windows, no mesmo endereço do Supabase local (127.0.0.1:54322, usuário postgres, senha postgres).
# Assim o api/.env, os testes da API e o front local funcionam sem mudar nada.
#
# Não substitui o `supabase db reset`: sem Docker não há pgTAP nem Storage (ver api/README.md).
# Apaga e recria os schemas stg, cfg, seg e mart e os roles api_site e carga. Só para dev.
#
# Uso, na raiz do repositório:
#   powershell -ExecutionPolicy Bypass -File supabase\banco_local.ps1

$ErrorActionPreference = 'Stop'

$psql = Join-Path $env:ProgramFiles 'PostgreSQL\17\bin\psql.exe'
if (-not (Test-Path $psql)) {
    throw "psql não encontrado em $psql. Instale o Postgres 17 (ver api/README.md)."
}

$env:PGPASSWORD = 'postgres'
# Sem isso, o psql do Windows lê os .sql na página de código do console e grava acento errado.
$env:PGCLIENTENCODING = 'UTF8'
$env:PGOPTIONS = '-c client_min_messages=warning'
$conexao = @('-h', '127.0.0.1', '-p', '54322', '-U', 'postgres', '-d', 'postgres', '-v', 'ON_ERROR_STOP=1', '-q')

function Rodar([string[]] $argumentos) {
    & $psql @conexao @argumentos
    if ($LASTEXITCODE -ne 0) { throw "psql falhou: $argumentos" }
}

Write-Host 'Apagando o banco local anterior...'
Rodar @('-c', 'drop schema if exists stg, cfg, seg, mart cascade;')
Rodar @('-c', 'do $$ begin if exists (select from pg_roles where rolname = ''api_site'') then drop owned by api_site, carga; drop role api_site, carga; end if; end $$;')

Get-ChildItem (Join-Path $PSScriptRoot 'migrations\*.sql') | Sort-Object Name | ForEach-Object {
    Write-Host "Migration $($_.Name)"
    Rodar @('-f', $_.FullName)
}

Write-Host 'Seed'
Rodar @('-f', (Join-Path $PSScriptRoot 'seed.sql'))

Write-Host 'Banco local pronto (migrations e seed "Padrão LANN" aplicados).'
