-- Migration 29 — fila de convites da Gestão.
--
-- Por quê: o Admin BORD passa a convidar e reenviar convites pela tela (decisão de
-- 13/11/2026, opção E de docs/convite-pela-gestao-opcoes.md). Criar o login no Supabase
-- Auth exige a chave secreta, que continua fora da API. A tela só grava o pedido aqui; um
-- job do GitHub Actions (onde a chave já está) lê a fila, manda o convite e grava o
-- resultado. A API segue com o api_site, sem a conexão administrativa.
--
--   - seg.usuario.id_autenticacao passa a aceitar null: a pessoa convidada pela tela existe
--     no cadastro antes de existir no Supabase Auth. Sem o id, ela não entra (nenhum token
--     aponta para ela); o job grava o id quando o convite sai.
--   - seg.convite_fila: um pedido por linha, com quem pediu, quando, e o resultado
--     ('fila' → 'enviado' ou 'falhou', com o motivo pronto para a tela).
--   - seg.pedir_convite: cria a pessoa, os vínculos e o pedido numa chamada. Só para o
--     admin (o api_site continua sem insert em seg.usuario). O reenvio é um insert direto
--     na fila, pelo RLS do admin.
--   - seg.convites_na_fila e seg.concluir_convite: o que o job usa, pelo role carga. É o
--     único acesso do carga a seg: lê os pedidos pendentes (e-mail e nome) e grava o
--     resultado. Não lê nem grava mais nada do schema.
--
-- A API nova depende desta migration: aplique antes de subir a API com o código de
-- 13/11/2026. A API antiga continua funcionando depois dela.

alter table seg.usuario alter column id_autenticacao drop not null;

comment on column seg.usuario.id_autenticacao is
    'Id do usuário no provedor de login (Supabase Auth). Null = convidado pela Gestão e '
    'ainda sem login: o job de convites grava o id quando o convite sai.';
comment on column seg.usuario.convidado_em is
    'Quando o convite foi enviado pela última vez (CLI de cadastro ou job de convites).';

-- ---------------------------------------------------------------------------
-- a fila
-- ---------------------------------------------------------------------------

create table seg.convite_fila (
    id            bigint generated always as identity primary key,
    usuario_id    uuid not null references seg.usuario (id) on delete cascade,
    -- o Admin BORD que pediu
    pedido_por    uuid not null references seg.usuario (id),
    pedido_em     timestamptz not null default now(),
    estado        text not null default 'fila' check (estado in ('fila', 'enviado', 'falhou')),
    -- por que não saiu, em texto pronto para a tela
    motivo        text,
    processado_em timestamptz,
    constraint convite_fila_motivo_so_na_falha check ((estado = 'falhou') = (motivo is not null)),
    constraint convite_fila_processado check ((estado = 'fila') = (processado_em is null))
);

comment on table seg.convite_fila is
    'Pedidos de convite feitos pela Gestão. O job de convites manda os que estão na fila e '
    'grava o resultado.';

-- um pedido pendente por pessoa: o segundo clique não manda dois e-mails
create unique index convite_fila_um_pendente on seg.convite_fila (usuario_id)
 where estado = 'fila';
-- o último pedido de cada pessoa (lista da Gestão)
create index convite_fila_ultimo on seg.convite_fila (usuario_id, id desc);

alter table seg.convite_fila enable row level security;
alter table seg.convite_fila force row level security;

revoke all on seg.convite_fila from public;
grant select, insert (usuario_id, pedido_por) on seg.convite_fila to api_site;

create policy api_site_admin_select on seg.convite_fila
    for select to api_site using ((select seg.eh_admin()));
-- o pedido leva quem pediu: o admin da requisição, e não outro
create policy api_site_admin_insert on seg.convite_fila
    for insert to api_site
    with check ((select seg.eh_admin()) and pedido_por = (select seg.usuario_atual()));

-- ---------------------------------------------------------------------------
-- API: convite de uma pessoa nova
-- ---------------------------------------------------------------------------

-- Cria a pessoa (sem login ainda), os vínculos e o pedido de convite. As regras com texto
-- para a tela (e-mail repetido, domínio do admin, sessão) são conferidas antes, na API; as
-- constraints da tabela valem de qualquer jeito.
create function seg.pedir_convite(
    p_email text, p_nome text, p_perfil text, p_sessao integer, p_clientes uuid[]
)
returns uuid
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
    v_id uuid;
begin
    if not seg.eh_admin() then
        raise exception 'só o admin pede convite' using errcode = '42501';
    end if;

    insert into seg.usuario (email, nome, tipo, perfil, sessao_minutos)
    values (
        lower(btrim(p_email)),
        btrim(p_nome),
        case when p_perfil in ('admin', 'consultor') then 'lann' else 'cliente' end,
        p_perfil,
        p_sessao
    )
    returning id into v_id;

    perform seg.definir_vinculos(v_id, p_clientes);

    insert into seg.convite_fila (usuario_id, pedido_por)
    values (v_id, seg.usuario_atual());

    return v_id;
end
$$;

comment on function seg.pedir_convite(text, text, text, integer, uuid[]) is
    'Cria a pessoa sem login, os vínculos e o pedido de convite na fila (só o admin chama).';

-- ---------------------------------------------------------------------------
-- job de convites (role carga)
-- ---------------------------------------------------------------------------

-- Pedidos pendentes, do mais antigo para o mais novo.
create function seg.convites_na_fila()
returns table (
    convite_id bigint, usuario_id uuid, email text, nome text, ativo boolean, entrou boolean
)
language sql
stable
security definer
set search_path = ''
as $$
    select f.id, u.id, u.email, u.nome, u.ativo, u.primeiro_acesso_em is not null
      from seg.convite_fila f
      join seg.usuario u on u.id = f.usuario_id
     where f.estado = 'fila'
     order by f.pedido_em, f.id
$$;

comment on function seg.convites_na_fila() is
    'Pedidos de convite pendentes, com o e-mail e o nome da pessoa (só o job de convites).';

-- Resultado de um pedido. Sem motivo: saiu; grava o id do Supabase Auth e a data do
-- convite na pessoa. Com motivo: não saiu, e a pessoa fica como está.
create function seg.concluir_convite(p_convite_id bigint, p_id_autenticacao uuid, p_motivo text)
returns void
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
    v_usuario uuid;
    v_motivo  text := nullif(btrim(p_motivo), '');
begin
    if v_motivo is null and p_id_autenticacao is null then
        raise exception 'convite enviado precisa do id do Supabase Auth';
    end if;

    update seg.convite_fila f
       set estado = case when v_motivo is null then 'enviado' else 'falhou' end,
           motivo = v_motivo,
           processado_em = now()
     where f.id = p_convite_id
       and f.estado = 'fila'
    returning f.usuario_id into v_usuario;
    if v_usuario is null then
        raise exception 'o convite % não está na fila', p_convite_id;
    end if;

    if v_motivo is null then
        -- quem já entrou não troca de login
        update seg.usuario u
           set id_autenticacao = p_id_autenticacao,
               convidado_em = now()
         where u.id = v_usuario
           and u.primeiro_acesso_em is null;
    end if;
end
$$;

comment on function seg.concluir_convite(bigint, uuid, text) is
    'Grava o resultado de um pedido de convite: enviado (com o id do Supabase Auth) ou '
    'falhou (com o motivo). Só o job de convites.';

revoke all on function seg.pedir_convite(text, text, text, integer, uuid[]) from public;
revoke all on function seg.convites_na_fila() from public;
revoke all on function seg.concluir_convite(bigint, uuid, text) from public;

grant execute on function seg.pedir_convite(text, text, text, integer, uuid[]) to api_site;

grant usage on schema seg to carga;
grant execute on function seg.convites_na_fila() to carga;
grant execute on function seg.concluir_convite(bigint, uuid, text) to carga;
