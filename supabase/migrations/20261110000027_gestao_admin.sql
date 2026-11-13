-- Migration 27 — o que a Gestão (rotas /api/admin/*, só do Admin BORD) lê e grava.
--
-- Por quê: a Gestão lista todos os clientes e todas as pessoas, liga painéis, monta a
-- matriz "o que cada perfil vê", edita perfil, empresas e sessão de cada pessoa, desativa
-- acessos e cadastra feriados. Até aqui o api_site só lia os próprios registros e não
-- gravava cadastro nenhum (havia o grant, da migration 2, mas nenhuma política).
--
-- Tudo passa por políticas `(select seg.eh_admin())` (migration 26; dentro de um select
-- para o Postgres avaliar uma vez por consulta, não por linha): valem só quando o usuário de
-- app.usuario_id está ativo e tem o perfil admin. Elas abrem cadastro e controle da carga
-- de todos os clientes. NÃO abrem dado de negócio: as tabelas de dados do mart continuam só
-- com o cliente escolhido (seg.clientes_do_usuario).
--
--   - seg.usuario: o api_site perde insert e delete (quem cria login é o CLI, pela conexão
--     administrativa) e só troca nome, perfil, ativo, sessao_minutos e desativado_em. O
--     e-mail é o login e não muda.
--   - logs e sessões continuam fechados: o admin lê só o resumo (seg.resumo_acessos) e
--     encerra as sessões de quem foi desativado (seg.encerrar_sessoes), por funções.
--   - seg.definir_vinculos: troca os clientes da pessoa numa chamada (vínculo com o papel
--     do perfil, perfil fixo do cliente e todas as empresas dele). A API e o CLI usam.

-- ---------------------------------------------------------------------------
-- grants
-- ---------------------------------------------------------------------------

revoke insert, update, delete on seg.usuario from api_site;
grant update (nome, perfil, ativo, sessao_minutos, desativado_em) on seg.usuario to api_site;

grant insert, update (ligada) on cfg.cliente_tela to api_site;
grant insert, update (nome), delete on cfg.feriado to api_site;

-- ---------------------------------------------------------------------------
-- leitura do admin (todos os clientes)
-- ---------------------------------------------------------------------------

create policy api_site_admin_select on seg.usuario
    for select to api_site using ((select seg.eh_admin()));
create policy api_site_admin_select on seg.usuario_cliente
    for select to api_site using ((select seg.eh_admin()));
create policy api_site_admin_select on seg.usuario_perfil
    for select to api_site using ((select seg.eh_admin()));
create policy api_site_admin_select on seg.usuario_empresa
    for select to api_site using ((select seg.eh_admin()));

create policy api_site_admin_select on cfg.empresa
    for select to api_site using ((select seg.eh_admin()));
create policy api_site_admin_select on cfg.perfil_acesso
    for select to api_site using ((select seg.eh_admin()));
create policy api_site_admin_select on cfg.perfil_tela
    for select to api_site using ((select seg.eh_admin()));
create policy api_site_admin_select on cfg.cliente_tela
    for select to api_site using ((select seg.eh_admin()));
create policy api_site_admin_select on cfg.fonte_carga
    for select to api_site using ((select seg.eh_admin()));
create policy api_site_admin_select on cfg.feriado
    for select to api_site using ((select seg.eh_admin()));
create policy api_site_admin_select on mart.carga_fonte
    for select to api_site using ((select seg.eh_admin()));

-- ---------------------------------------------------------------------------
-- escrita do admin
-- ---------------------------------------------------------------------------

create policy api_site_admin_update on seg.usuario
    for update to api_site using ((select seg.eh_admin())) with check ((select seg.eh_admin()));

create policy api_site_admin_insert on seg.usuario_cliente
    for insert to api_site with check ((select seg.eh_admin()));
create policy api_site_admin_update on seg.usuario_cliente
    for update to api_site using ((select seg.eh_admin())) with check ((select seg.eh_admin()));
create policy api_site_admin_delete on seg.usuario_cliente
    for delete to api_site using ((select seg.eh_admin()));

create policy api_site_admin_insert on seg.usuario_perfil
    for insert to api_site with check ((select seg.eh_admin()));
create policy api_site_admin_delete on seg.usuario_perfil
    for delete to api_site using ((select seg.eh_admin()));

create policy api_site_admin_insert on seg.usuario_empresa
    for insert to api_site with check ((select seg.eh_admin()));
create policy api_site_admin_delete on seg.usuario_empresa
    for delete to api_site using ((select seg.eh_admin()));

-- matriz "o que cada perfil vê": só nos perfis fixos
create policy api_site_admin_insert on cfg.perfil_tela
    for insert to api_site
    with check (
        (select seg.eh_admin())
        and exists (select 1 from cfg.perfil_acesso p
                     where p.id = perfil_tela.perfil_id and p.perfil is not null)
    );
create policy api_site_admin_delete on cfg.perfil_tela
    for delete to api_site
    using (
        (select seg.eh_admin())
        and exists (select 1 from cfg.perfil_acesso p
                     where p.id = perfil_tela.perfil_id and p.perfil is not null)
    );

create policy api_site_admin_insert on cfg.cliente_tela
    for insert to api_site with check ((select seg.eh_admin()));
create policy api_site_admin_update on cfg.cliente_tela
    for update to api_site using ((select seg.eh_admin())) with check ((select seg.eh_admin()));

create policy api_site_admin_insert on cfg.feriado
    for insert to api_site with check ((select seg.eh_admin()));
create policy api_site_admin_update on cfg.feriado
    for update to api_site using ((select seg.eh_admin())) with check ((select seg.eh_admin()));
create policy api_site_admin_delete on cfg.feriado
    for delete to api_site using ((select seg.eh_admin()));

-- ---------------------------------------------------------------------------
-- funções
-- ---------------------------------------------------------------------------

-- Troca os clientes da pessoa pelos de p_clientes: vínculo com o papel do perfil dela,
-- perfil fixo de cada cliente e todas as empresas dele. Admin fica sem vínculos (vê todos).
-- SECURITY INVOKER: pela API, vale o RLS (só o admin grava); pelo CLI, a conexão
-- administrativa.
create function seg.definir_vinculos(p_usuario_id uuid, p_clientes uuid[])
returns void
language plpgsql
volatile
security invoker
set search_path = ''
as $$
declare
    v_perfil   text;
    v_clientes uuid[];
begin
    select u.perfil into v_perfil from seg.usuario u where u.id = p_usuario_id;
    if v_perfil is null then
        raise exception 'usuário % não encontrado', p_usuario_id;
    end if;
    v_clientes := case when v_perfil = 'admin' then '{}'::uuid[]
                       else coalesce(p_clientes, '{}'::uuid[]) end;

    -- perfis e empresas dos vínculos que saem vão em cascata
    delete from seg.usuario_cliente uc
     where uc.usuario_id = p_usuario_id
       and uc.cliente_id <> all (v_clientes);

    insert into seg.usuario_cliente (usuario_id, cliente_id, papel)
    select p_usuario_id, c.id, seg.papel_do_perfil(v_perfil)
      from unnest(v_clientes) as c (id)
    on conflict (usuario_id, cliente_id) do update set papel = excluded.papel;

    delete from seg.usuario_perfil up where up.usuario_id = p_usuario_id;

    insert into seg.usuario_perfil (usuario_id, cliente_id, perfil_id)
    select p_usuario_id, f.cliente_id, f.id
      from cfg.perfil_acesso f
     where f.cliente_id = any (v_clientes)
       and f.perfil = v_perfil;

    insert into seg.usuario_empresa (usuario_id, cliente_id, empresa_id)
    select p_usuario_id, e.cliente_id, e.id
      from cfg.empresa e
     where e.cliente_id = any (v_clientes)
    on conflict do nothing;
end
$$;

comment on function seg.definir_vinculos(uuid, uuid[]) is
    'Troca os clientes da pessoa: vínculo com o papel do perfil, perfil fixo de cada cliente '
    'e todas as empresas dele. Admin fica sem vínculos.';

-- Encerra as sessões de login abertas da pessoa (ao desativar o acesso): o próximo
-- renovar dela responde 401.
create function seg.encerrar_sessoes(p_usuario_id uuid)
returns void
language plpgsql
volatile
security definer
set search_path = ''
as $$
begin
    if not seg.eh_admin() then
        raise exception 'só o admin encerra sessões' using errcode = '42501';
    end if;
    delete from seg.sessao_uso s
     using seg.usuario u
     where u.id = p_usuario_id
       and s.id_autenticacao = u.id_autenticacao;
end
$$;

comment on function seg.encerrar_sessoes(uuid) is
    'Apaga as sessões de login abertas da pessoa (só o admin chama).';

-- Entradas (logins) nos últimos 30 dias e último acesso de cada pessoa. O log continua
-- sem leitura direta pelo api_site; quem não é admin recebe vazio.
create function seg.resumo_acessos()
returns table (usuario_id uuid, entradas_30d integer, ultimo_acesso timestamptz)
language sql
stable
security definer
set search_path = ''
as $$
    select u.id,
           (select count(*)::integer
              from seg.log_acesso l
             where l.usuario_id = u.id
               and l.resultado = 'permitido'
               and l.codigo_tela in ('auth:entrar', 'auth:definir-senha')
               and l.em > now() - interval '30 days'),
           (select max(l.em)
              from seg.log_acesso l
             where l.usuario_id = u.id
               and l.resultado = 'permitido')
      from seg.usuario u
     where seg.eh_admin()
$$;

comment on function seg.resumo_acessos() is
    'Por pessoa: logins nos últimos 30 dias e o último acesso permitido (só para o admin).';

-- os logins de cada pessoa, para a contagem acima não varrer o log inteiro
create index log_acesso_entradas
    on seg.log_acesso (usuario_id, em)
 where codigo_tela in ('auth:entrar', 'auth:definir-senha');

revoke all on function seg.definir_vinculos(uuid, uuid[]) from public;
revoke all on function seg.encerrar_sessoes(uuid) from public;
revoke all on function seg.resumo_acessos() from public;

grant execute on function seg.definir_vinculos(uuid, uuid[]) to api_site;
grant execute on function seg.encerrar_sessoes(uuid) to api_site;
grant execute on function seg.resumo_acessos() to api_site;
