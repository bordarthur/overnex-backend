-- Migration 2 — roles e grants.
--
-- api_site : usado pela API. Lê mart e cfg; escreve só em cadastros e logs.
-- carga    : usado pela carga diária. Escreve no mart, lê só o que precisa de cfg,
--            cria tabela temporária e não enxerga seg.
--
-- Os roles nascem sem LOGIN e sem senha. A senha é definida fora das migrations
-- (localmente pelo seed.sql; em produção, manualmente — ver supabase/README.md).
-- Roles são globais no cluster, por isso a criação é idempotente.

do $$
begin
    if not exists (select from pg_roles where rolname = 'api_site') then
        create role api_site;
    end if;
    if not exists (select from pg_roles where rolname = 'carga') then
        create role carga;
    end if;
end
$$;

-- SUPERUSER/REPLICATION não podem ser alterados pelo postgres do Supabase (não é
-- superuser); roles novos já nascem sem eles, e a asserção abaixo confere.
alter role api_site with nocreatedb nocreaterole noinherit nobypassrls;
alter role carga    with nocreatedb nocreaterole noinherit nobypassrls;

do $$
begin
    if exists (
        select from pg_roles
         where rolname in ('api_site', 'carga')
           and (rolsuper or rolbypassrls or rolreplication)
    ) then
        raise exception 'api_site e carga não podem ter SUPERUSER, BYPASSRLS nem REPLICATION';
    end if;
end
$$;

-- O dono (postgres) precisa de SET ROLE nos dois para os testes (supabase test db).
-- A partir do PG16 o criador do role não ganha isso automaticamente; sem INHERIT,
-- postgres não herda nada deles.
do $$
begin
    if current_setting('server_version_num')::int >= 160000 then
        execute format('grant api_site, carga to %I with inherit false, set true', current_user);
    else
        execute format('grant api_site, carga to %I', current_user);
    end if;
end
$$;

-- Ninguém além dos donos recebe nada por padrão nestes schemas.
revoke all on schema stg, cfg, seg, mart from public;
revoke all on all tables in schema cfg, seg, mart from public;
revoke all on all sequences in schema cfg, seg, mart from public;

do $$
declare
    r text;
begin
    -- roles padrão do Supabase (PostgREST) não acessam nada daqui
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select from pg_roles where rolname = r) then
            execute format('revoke all on schema stg, cfg, seg, mart from %I', r);
            execute format('revoke all on all tables in schema cfg, seg, mart from %I', r);
            execute format('revoke all on all sequences in schema cfg, seg, mart from %I', r);
        end if;
    end loop;
end
$$;

-- ---------------------------------------------------------------------------
-- api_site
-- ---------------------------------------------------------------------------

grant usage on schema cfg, seg, mart to api_site;

-- leitura
grant select on all tables in schema cfg to api_site;
grant select on mart.lancamento, mart.carga_fonte to api_site;
-- mart.carga: sem acesso.
grant select on seg.usuario, seg.usuario_cliente, seg.usuario_perfil, seg.usuario_empresa to api_site;

-- escrita em cadastros (as políticas de escrita chegam com o admin; até lá o RLS nega)
grant insert, update, delete on all tables in schema cfg to api_site;
grant insert, update, delete
    on seg.usuario, seg.usuario_cliente, seg.usuario_perfil, seg.usuario_empresa
    to api_site;
grant usage on all sequences in schema cfg to api_site;

-- logs: só inserção (append-only, sem leitura)
grant insert on seg.log_acesso, seg.log_alteracao to api_site;

-- ---------------------------------------------------------------------------
-- carga
-- ---------------------------------------------------------------------------

grant usage on schema cfg, mart to carga;
-- seg: nenhum acesso.

grant select on cfg.fonte_carga, cfg.mapa_empresa, cfg.empresa to carga;

grant select, insert, update, delete on mart.lancamento, mart.carga, mart.carga_fonte to carga;
grant usage on all sequences in schema mart to carga;

do $$
begin
    execute format('grant temporary on database %I to carga', current_database());
end
$$;
