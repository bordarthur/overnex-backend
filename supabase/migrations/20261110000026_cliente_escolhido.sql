-- Migration 26 — cliente escolhido na requisição e o administrador que vê todos os clientes.
--
-- Por quê: até aqui cada usuário tinha um cliente só (a API negava quem tinha mais de um
-- vínculo) e o RLS liberava a união dos clientes vinculados. Com o Admin BORD (vê todos os
-- clientes, sem vínculo) e o Consultor por carteira (vários vínculos), a requisição passa a
-- dizer qual cliente quer (`?cliente=<slug>`), e o banco passa a liberar SÓ esse cliente:
--
--   - app.cliente_id: o cliente escolhido da transação, definido por seg.contexto_acesso
--     junto com app.usuario_id. O slug pedido é só uma escolha entre os clientes que o banco
--     libera à pessoa (os vínculos; para o admin, os ativos); fora deles, fica vazio.
--   - seg.clientes_do_usuario(): só o cliente escolhido. Sem escolha: o único vínculo de
--     quem tem um só (é o caso da API antiga e dos testes, que não definem app.cliente_id);
--     nada para o admin e para quem tem mais de um. Assim nenhuma consulta mistura clientes,
--     nem nas linhas sem empresa (_empresa_id is null) das tabelas de dados.
--   - seg.empresas_do_usuario(): para o admin, todas as empresas do cliente escolhido.
--   - seg.clientes_vinculados(): todos os clientes que a pessoa pode abrir. Só a política de
--     cfg.cliente usa (nome, slug e logo, para o seletor de empresa do front).
--
-- seg.contexto_acesso ganha o parâmetro p_cliente e as colunas perfil e cliente_id. As
-- decisões continuam na API (app/seguranca/acesso.py). perfis_tela passa a contar só as
-- telas ligadas no cliente (cfg.cliente_tela, migration 25); para o admin, a tela ligada no
-- cliente escolhido vem com dado nominal.
--
-- A API antiga continua funcionando: chama a função com 2 argumentos (o terceiro tem
-- padrão), lê as colunas pelo nome e só tem usuários de um vínculo.

create function seg.cliente_atual()
returns uuid
language sql
stable
set search_path = ''
as $$
    select nullif(current_setting('app.cliente_id', true), '')::uuid
$$;

comment on function seg.cliente_atual() is
    'cfg.cliente.id escolhido na requisição corrente (app.cliente_id), ou null.';

create function seg.eh_admin()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select exists (
        select 1
          from seg.usuario u
         where u.id = seg.usuario_atual()
           and u.ativo
           and u.perfil = 'admin'
    )
$$;

comment on function seg.eh_admin() is
    'O usuário de app.usuario_id está ativo e tem o perfil admin (Admin BORD).';

create function seg.clientes_vinculados()
returns setof uuid
language sql
stable
security definer
set search_path = ''
as $$
    select c.id
      from cfg.cliente c
     where c.ativo
       and seg.eh_admin()
    union
    select uc.cliente_id
      from seg.usuario_cliente uc
      join seg.usuario u on u.id = uc.usuario_id
      join cfg.cliente c on c.id = uc.cliente_id
     where u.id = seg.usuario_atual()
       and u.ativo
       and u.perfil <> 'admin'
       and c.ativo
$$;

comment on function seg.clientes_vinculados() is
    'Clientes ativos que o usuário de app.usuario_id pode abrir: os dos vínculos; para o '
    'admin, todos.';

-- Mesma assinatura da migration 3: create or replace mantém os grants e as políticas.
create or replace function seg.clientes_do_usuario()
returns setof uuid
language sql
stable
security definer
set search_path = ''
as $$
    select v.id
      from seg.clientes_vinculados() as v (id)
     where case
               when seg.cliente_atual() is not null then v.id = seg.cliente_atual()
               else not seg.eh_admin()
                    and (select count(*) from seg.clientes_vinculados()) = 1
           end
$$;

comment on function seg.clientes_do_usuario() is
    'O cliente escolhido (app.cliente_id), se o usuário pode abri-lo. Sem escolha: o único '
    'vínculo de quem tem um só; nada para o admin e para quem tem mais de um.';

create or replace function seg.empresas_do_usuario()
returns setof uuid
language sql
stable
security definer
set search_path = ''
as $$
    select e.id
      from cfg.empresa e
     where seg.eh_admin()
       and e.cliente_id in (select seg.clientes_do_usuario())
    union
    select ue.empresa_id
      from seg.usuario_empresa ue
     where ue.usuario_id = seg.usuario_atual()
       and ue.cliente_id in (select seg.clientes_do_usuario())
$$;

comment on function seg.empresas_do_usuario() is
    'Empresas liberadas ao usuário de app.usuario_id no cliente escolhido; para o admin, '
    'todas as do cliente.';

revoke all on function seg.cliente_atual() from public;
revoke all on function seg.eh_admin() from public;
revoke all on function seg.clientes_vinculados() from public;

grant execute on function seg.cliente_atual() to api_site;
grant execute on function seg.eh_admin() to api_site;
grant execute on function seg.clientes_vinculados() to api_site;

-- cfg.cliente: a pessoa lê o nome de todos os clientes que pode abrir, mesmo com um já
-- escolhido (seletor de empresa).
drop policy api_site_select on cfg.cliente;

create policy api_site_select on cfg.cliente
    for select to api_site
    using (id in (select seg.clientes_vinculados()));

-- ---------------------------------------------------------------------------
-- seg.contexto_acesso: muda o retorno, então drop + create
-- ---------------------------------------------------------------------------

drop function seg.contexto_acesso(uuid, text);

create function seg.contexto_acesso(
    p_id_autenticacao uuid,
    p_codigo_tela     text default null,
    p_cliente         text default null
)
returns table (
    usuario_id  uuid,
    ativo       boolean,
    -- [{"cliente_id", "papel"}] dos vínculos com cliente ativo (admin: vazio)
    vinculos    jsonb,
    -- {"<cliente_id>": ve_dado_nominal} do cliente escolhido, se p_codigo_tela está ligada
    -- nele e algum perfil do usuário a libera (admin: basta estar ligada, e vem true)
    perfis_tela jsonb,
    -- [{"cliente_id", "empresa_id"}] liberadas no cliente escolhido, na ordem nome, id
    empresas    jsonb,
    -- seg.usuario.perfil
    perfil      text,
    -- cliente escolhido: o de p_cliente (slug), se o usuário pode abri-lo; sem p_cliente, o
    -- único vínculo de quem tem um só; senão null
    cliente_id  uuid
)
language plpgsql
volatile
security invoker
set search_path = ''
as $$
#variable_conflict use_column
declare
    v_id      uuid;
    v_ativo   boolean;
    v_perfil  text;
    v_cliente uuid;
begin
    select r.id, r.ativo into v_id, v_ativo
      from seg.resolver_usuario(p_id_autenticacao) r;

    perform set_config('app.usuario_id', coalesce(v_id::text, ''), true);
    perform set_config('app.cliente_id', '', true);

    if v_id is null or not v_ativo then
        return query
        select v_id, coalesce(v_ativo, false), '[]'::jsonb, '{}'::jsonb, '[]'::jsonb,
               null::text, null::uuid;
        return;
    end if;

    select u.perfil into v_perfil from seg.usuario u where u.id = v_id;

    -- cfg.cliente só mostra os clientes que o usuário pode abrir (seg.clientes_vinculados)
    if p_cliente is not null then
        select c.id into v_cliente
          from cfg.cliente c
         where c.slug = p_cliente
           and c.ativo;
    elsif v_perfil <> 'admin' then
        select v.id into v_cliente
          from seg.clientes_vinculados() as v (id)
         where (select count(*) from seg.clientes_vinculados()) = 1;
    end if;

    -- daqui em diante o RLS só mostra o cliente escolhido
    perform set_config('app.cliente_id', coalesce(v_cliente::text, ''), true);

    return query
    select
        v_id,
        true,
        coalesce((
            select jsonb_agg(
                       jsonb_build_object('cliente_id', uc.cliente_id, 'papel', uc.papel)
                       order by uc.cliente_id)
              from seg.usuario_cliente uc
              join cfg.cliente c on c.id = uc.cliente_id and c.ativo
             where uc.usuario_id = v_id
               and v_perfil <> 'admin'
        ), '[]'::jsonb),
        case
            when v_perfil = 'admin' then coalesce((
                select jsonb_build_object(ct.cliente_id::text, true)
                  from cfg.cliente_tela ct
                 where ct.cliente_id = v_cliente
                   and ct.codigo_tela = p_codigo_tela
                   and ct.ligada
            ), '{}'::jsonb)
            else coalesce((
                select jsonb_object_agg(x.cliente_id::text, x.ve_dado_nominal)
                  from (
                        select up.cliente_id, bool_or(pt.ve_dado_nominal) as ve_dado_nominal
                          from seg.usuario_perfil up
                          join cfg.perfil_tela pt on pt.perfil_id = up.perfil_id
                          join cfg.cliente_tela ct
                            on ct.cliente_id = up.cliente_id
                           and ct.codigo_tela = pt.codigo_tela
                           and ct.ligada
                         where up.usuario_id = v_id
                           and up.cliente_id = v_cliente
                           and pt.codigo_tela = p_codigo_tela
                         group by up.cliente_id
                       ) x
            ), '{}'::jsonb)
        end,
        case
            when v_perfil = 'admin' then coalesce((
                select jsonb_agg(
                           jsonb_build_object('cliente_id', e.cliente_id, 'empresa_id', e.id)
                           order by e.nome, e.id)
                  from cfg.empresa e
                 where e.cliente_id = v_cliente
            ), '[]'::jsonb)
            else coalesce((
                select jsonb_agg(
                           jsonb_build_object('cliente_id', ue.cliente_id, 'empresa_id', ue.empresa_id)
                           order by e.nome, e.id)
                  from seg.usuario_empresa ue
                  join cfg.empresa e on e.id = ue.empresa_id and e.cliente_id = ue.cliente_id
                 where ue.usuario_id = v_id
                   and ue.cliente_id = v_cliente
            ), '[]'::jsonb)
        end,
        v_perfil,
        v_cliente;
end
$$;

comment on function seg.contexto_acesso(uuid, text, text) is
    'Define app.usuario_id e app.cliente_id (o cliente escolhido pelo slug, entre os que o '
    'usuário pode abrir) e devolve o perfil, os vínculos, se a tela está liberada no cliente '
    'escolhido e as empresas dele, numa chamada (checagem de acesso da API).';

revoke all on function seg.contexto_acesso(uuid, text, text) from public;
grant execute on function seg.contexto_acesso(uuid, text, text) to api_site;
