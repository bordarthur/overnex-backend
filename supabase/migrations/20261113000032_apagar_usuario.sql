-- Migration 32 — apagar pessoa pela Gestão.
--
-- Por quê: a Gestão só desativava. Cadastro errado (e-mail digitado errado, pessoa que
-- saiu do cliente) ficava para sempre na lista, e o e-mail continuava ocupado. Agora o
-- Admin BORD apaga quem já está desativado.
--
--   - seg.apagar_usuario: só o admin, só pessoa desativada e nunca a si mesmo. Apaga
--     seg.usuario (vínculos e pedidos de convite vão em cascata) e as sessões abertas. Os
--     logs ficam: não têm FK, de propósito (migration 1).
--   - o login no Supabase Auth só se apaga com a chave secreta, que fica fora da API. O id
--     vai para seg.login_a_apagar, e o job de convites (role carga) apaga no Supabase Auth
--     e tira da fila. Sem isso, o e-mail não poderia ser convidado de novo.
--   - seg.convite_fila.pedido_por passa a aceitar null (on delete set null): apagar um
--     Admin BORD não esbarra nos convites que ele pediu.
--
-- Quem tem registro que depende dele (cfg.regra_lancamento.alterado_por) não se apaga: a FK
-- recusa, e a pessoa continua desativada.
--
-- A rota DELETE /api/admin/usuarios/{id} depende desta migration: aplique antes. O resto da
-- API funciona sem ela.

alter table seg.convite_fila alter column pedido_por drop not null;
alter table seg.convite_fila drop constraint convite_fila_pedido_por_fkey;
alter table seg.convite_fila
    add constraint convite_fila_pedido_por_fkey
        foreign key (pedido_por) references seg.usuario (id) on delete set null;

-- Logins do Supabase Auth que o job ainda precisa apagar.
create table seg.login_a_apagar (
    id_autenticacao uuid primary key,
    pedido_em       timestamptz not null default now()
);

comment on table seg.login_a_apagar is
    'Ids do Supabase Auth de pessoas apagadas pela Gestão; o job de convites apaga o login '
    'e tira daqui.';

alter table seg.login_a_apagar enable row level security;
alter table seg.login_a_apagar force row level security;
revoke all on seg.login_a_apagar from public;

-- ---------------------------------------------------------------------------
-- API
-- ---------------------------------------------------------------------------

create function seg.apagar_usuario(p_usuario_id uuid)
returns void
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
    v_ativo boolean;
    v_login uuid;
begin
    if not seg.eh_admin() then
        raise exception 'só o admin apaga usuário' using errcode = '42501';
    end if;
    if p_usuario_id = seg.usuario_atual() then
        raise exception 'ninguém apaga a si mesmo';
    end if;

    select u.ativo, u.id_autenticacao into v_ativo, v_login
      from seg.usuario u
     where u.id = p_usuario_id
       for update;
    if not found then
        raise exception 'usuário % não encontrado', p_usuario_id;
    end if;
    if v_ativo then
        raise exception 'só se apaga usuário desativado';
    end if;

    delete from seg.usuario u where u.id = p_usuario_id;

    if v_login is not null then
        delete from seg.sessao_uso s where s.id_autenticacao = v_login;
        insert into seg.login_a_apagar (id_autenticacao)
        values (v_login)
        on conflict do nothing;
    end if;
end
$$;

comment on function seg.apagar_usuario(uuid) is
    'Apaga uma pessoa desativada e põe o login dela na fila para o job apagar no Supabase '
    'Auth (só o admin chama).';

-- ---------------------------------------------------------------------------
-- job de convites (role carga)
-- ---------------------------------------------------------------------------

-- Logins a apagar no Supabase Auth. O id que voltou a ter cadastro (a pessoa foi convidada
-- de novo antes de o job rodar) sai da fila sem ser apagado.
create function seg.logins_a_apagar()
returns setof uuid
language plpgsql
volatile
security definer
set search_path = ''
as $$
begin
    delete from seg.login_a_apagar l
     where exists (select 1 from seg.usuario u where u.id_autenticacao = l.id_autenticacao);
    return query
        select l.id_autenticacao from seg.login_a_apagar l order by l.pedido_em;
end
$$;

comment on function seg.logins_a_apagar() is
    'Ids do Supabase Auth que o job de convites precisa apagar (os que não têm mais cadastro).';

create function seg.login_apagado(p_id_autenticacao uuid)
returns void
language sql
volatile
security definer
set search_path = ''
as $$
    delete from seg.login_a_apagar l where l.id_autenticacao = p_id_autenticacao;
$$;

comment on function seg.login_apagado(uuid) is
    'Tira da fila um login que o job já apagou no Supabase Auth.';

revoke all on function seg.apagar_usuario(uuid) from public;
revoke all on function seg.logins_a_apagar() from public;
revoke all on function seg.login_apagado(uuid) from public;

grant execute on function seg.apagar_usuario(uuid) to api_site;
grant execute on function seg.logins_a_apagar() to carga;
grant execute on function seg.login_apagado(uuid) to carga;
