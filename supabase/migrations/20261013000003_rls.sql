-- Migration 3 — Row Level Security.
--
-- Regra central: um cliente nunca vê o dado de outro.
-- A API abre uma transação por requisição e executa
--     set_config('app.usuario_id', <seg.usuario.id>, true)
-- As políticas de api_site partem desse valor; sem ele, nenhuma linha.
--
-- As funções SECURITY DEFINER rodam como o dono das tabelas (postgres), que no
-- Supabase tem BYPASSRLS. Isso evita recursão entre as políticas de seg.

do $$
begin
    if not (select rolbypassrls from pg_roles where rolname = current_user) then
        raise exception 'o dono das funções de seg (%) precisa de BYPASSRLS', current_user;
    end if;
end
$$;

-- ---------------------------------------------------------------------------
-- Funções de apoio
-- ---------------------------------------------------------------------------

create function seg.usuario_atual()
returns uuid
language sql
stable
set search_path = ''
as $$
    select nullif(current_setting('app.usuario_id', true), '')::uuid
$$;

comment on function seg.usuario_atual() is
    'seg.usuario.id da requisição corrente (app.usuario_id), ou null.';

create function seg.clientes_do_usuario()
returns setof uuid
language sql
stable
security definer
set search_path = ''
as $$
    select uc.cliente_id
      from seg.usuario_cliente uc
      join seg.usuario u on u.id = uc.usuario_id
      join cfg.cliente c on c.id = uc.cliente_id
     where u.id = seg.usuario_atual()
       and u.ativo
       and c.ativo
$$;

comment on function seg.clientes_do_usuario() is
    'Clientes ativos vinculados ao usuário ativo de app.usuario_id.';

create function seg.empresas_do_usuario()
returns setof uuid
language sql
stable
security definer
set search_path = ''
as $$
    select ue.empresa_id
      from seg.usuario_empresa ue
     where ue.usuario_id = seg.usuario_atual()
       and ue.cliente_id in (select seg.clientes_do_usuario())
$$;

comment on function seg.empresas_do_usuario() is
    'Empresas liberadas ao usuário de app.usuario_id, dentro dos clientes ativos dele.';

-- Usada pela API antes de existir app.usuario_id: traduz o oid do token do
-- Entra no id interno. Devolve só o próprio registro.
create function seg.resolver_usuario(p_object_id_entra uuid)
returns table (id uuid, ativo boolean)
language sql
stable
security definer
set search_path = ''
as $$
    select u.id, u.ativo
      from seg.usuario u
     where u.object_id_entra = p_object_id_entra
$$;

revoke all on function seg.usuario_atual() from public;
revoke all on function seg.clientes_do_usuario() from public;
revoke all on function seg.empresas_do_usuario() from public;
revoke all on function seg.resolver_usuario(uuid) from public;

grant execute on function seg.usuario_atual() to api_site;
grant execute on function seg.clientes_do_usuario() to api_site;
grant execute on function seg.empresas_do_usuario() to api_site;
grant execute on function seg.resolver_usuario(uuid) to api_site;

-- ---------------------------------------------------------------------------
-- ENABLE + FORCE em todas as tabelas de mart, cfg e seg
-- ---------------------------------------------------------------------------

do $$
declare
    t record;
begin
    for t in
        select n.nspname, c.relname
          from pg_class c
          join pg_namespace n on n.oid = c.relnamespace
         where n.nspname in ('mart', 'cfg', 'seg')
           and c.relkind in ('r', 'p')
    loop
        execute format('alter table %I.%I enable row level security', t.nspname, t.relname);
        execute format('alter table %I.%I force row level security', t.nspname, t.relname);
    end loop;
end
$$;

-- ---------------------------------------------------------------------------
-- Políticas de api_site (leitura)
-- ---------------------------------------------------------------------------

-- cfg: escopo do cliente
create policy api_site_select on cfg.cliente
    for select to api_site
    using (id in (select seg.clientes_do_usuario()));

create policy api_site_select on cfg.painel
    for select to api_site
    using (cliente_id in (select seg.clientes_do_usuario()));

create policy api_site_select on cfg.perfil_acesso
    for select to api_site
    using (cliente_id in (select seg.clientes_do_usuario()));

create policy api_site_select on cfg.perfil_tela
    for select to api_site
    using (exists (
        select 1
          from cfg.perfil_acesso p
         where p.id = perfil_tela.perfil_id
           and p.cliente_id in (select seg.clientes_do_usuario())
    ));

create policy api_site_select on cfg.fonte_carga
    for select to api_site
    using (cliente_id in (select seg.clientes_do_usuario()));

create policy api_site_select on cfg.mapa_empresa
    for select to api_site
    using (cliente_id in (select seg.clientes_do_usuario()));

-- cfg: escopo da empresa
create policy api_site_select on cfg.empresa
    for select to api_site
    using (id in (select seg.empresas_do_usuario()));

create policy api_site_select on cfg.parametro
    for select to api_site
    using (empresa_id in (select seg.empresas_do_usuario()));

create policy api_site_select on cfg.regra_lancamento
    for select to api_site
    using (empresa_id in (select seg.empresas_do_usuario()));

-- seg: só os próprios registros
create policy api_site_select on seg.usuario
    for select to api_site
    using (id = seg.usuario_atual());

create policy api_site_select on seg.usuario_cliente
    for select to api_site
    using (usuario_id = seg.usuario_atual());

create policy api_site_select on seg.usuario_perfil
    for select to api_site
    using (usuario_id = seg.usuario_atual());

create policy api_site_select on seg.usuario_empresa
    for select to api_site
    using (usuario_id = seg.usuario_atual());

-- seg: logs (só inserção; usuario_id null = usuário não identificado)
create policy api_site_insert on seg.log_acesso
    for insert to api_site
    with check (usuario_id is null or usuario_id = seg.usuario_atual());

create policy api_site_insert on seg.log_alteracao
    for insert to api_site
    with check (usuario_id = seg.usuario_atual());

-- mart
create policy api_site_select on mart.lancamento
    for select to api_site
    using (empresa_id in (select seg.empresas_do_usuario()));

create policy api_site_select on mart.carga_fonte
    for select to api_site
    using (cliente_id in (select seg.clientes_do_usuario()));

-- mart.carga: sem política para api_site (e sem grant).
-- Escrita em cadastros (cfg, seg.usuario*): sem política por enquanto → negada.
-- TODO(admin): políticas de escrita restritas a papel 'admin' do próprio cliente.

-- ---------------------------------------------------------------------------
-- Políticas de carga
-- ---------------------------------------------------------------------------

create policy carga_all on mart.lancamento
    for all to carga using (true) with check (true);

create policy carga_all on mart.carga
    for all to carga using (true) with check (true);

create policy carga_all on mart.carga_fonte
    for all to carga using (true) with check (true);

create policy carga_select on cfg.fonte_carga
    for select to carga using (true);

create policy carga_select on cfg.mapa_empresa
    for select to carga using (true);

create policy carga_select on cfg.empresa
    for select to carga using (true);

-- Nenhuma política de carga em seg.
