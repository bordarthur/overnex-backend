-- Migration 15 — login com e-mail e senha no Supabase Auth.
--
-- Por quê: o login sai do Entra ID (desligado por configuração) e passa para o Supabase
-- Auth do próprio projeto. O id do usuário no provedor deixa de ser o `oid` do Entra e
-- passa a ser o `sub` do token do Supabase (auth.users.id); a coluna troca de nome para
-- não amarrar o cadastro a um provedor. Sem FK para auth.users de propósito: o cadastro
-- do Overnex não depende do schema do Auth (e o Entra pode voltar).
--
-- nome: cadastrado pela LANN; /api/sessao passa a usar este nome em vez do token.
--
-- Usuários já existentes mantêm o id antigo (provisório ou do Entra) até alguém trocar
-- pelo id do Supabase Auth (ver supabase/producao/usuario_dev_supabase_auth.sql).

alter table seg.usuario rename column object_id_entra to id_autenticacao;
alter table seg.usuario rename constraint usuario_object_id_entra_key to usuario_id_autenticacao_key;
alter table seg.usuario add column nome text;

comment on column seg.usuario.id_autenticacao is
    'Id do usuário no provedor de login: auth.users.id (sub do token) no Supabase Auth.';
comment on column seg.usuario.nome is
    'Nome mostrado no Overnex, cadastrado pela LANN.';

alter table seg.log_acesso rename column object_id_entra to id_autenticacao;

-- O parâmetro muda de nome: create or replace não aceita, então drop + create.
drop function seg.resolver_usuario(uuid);

-- Usada pela API antes de existir app.usuario_id: traduz o id do provedor (sub do token)
-- no id interno. Devolve só o próprio registro.
create function seg.resolver_usuario(p_id_autenticacao uuid)
returns table (id uuid, ativo boolean)
language sql
stable
security definer
set search_path = ''
as $$
    select u.id, u.ativo
      from seg.usuario u
     where u.id_autenticacao = p_id_autenticacao
$$;

revoke all on function seg.resolver_usuario(uuid) from public;
grant execute on function seg.resolver_usuario(uuid) to api_site;
