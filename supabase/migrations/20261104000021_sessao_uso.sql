-- Migration 21 — último uso de cada sessão de login, para encerrar a sessão parada.
--
-- Por quê: a sessão termina depois de 30 min sem uso (o limite é da API, em
-- AUTH_INATIVIDADE_SEGUNDOS). Com a aba aberta, o front derruba a sessão sozinho; com a aba
-- fechada, o cookie do refresh token continuava valendo e POST /api/auth/renovar devolvia
-- uma sessão nova sem senha. O "Inactivity timeout" do Supabase Auth não serve: é do plano
-- Pro, conta só as renovações (pedia access token curto, de 10 min) e o limite ficaria no
-- painel do Supabase, fora da configuração da API.
--
-- Uso = pedido autenticado à API ou renovação. A sessão é a do Supabase Auth (claim
-- session_id do access token). A API:
--   - no login (entrar, definir-senha) chama seg.iniciar_sessao;
--   - em cada pedido autenticado chama seg.usar_sessao, junto com o commit (sem ida a mais
--     ao banco);
--   - no renovar chama seg.renovar_sessao; false = parada há mais que o limite, ou sem
--     registro (sessão aberta antes desta migration): a API encerra a sessão e responde 401.
--
-- Sessão vencida não volta: seg.usar_sessao só atualiza quem está dentro do limite, e só o
-- login cria a linha. Linhas vencidas são apagadas no renovar recusado e a cada login.
--
-- Sem grant na tabela: o api_site só passa pelas três funções (SECURITY DEFINER). A API
-- depende desta migration: aplique antes de subir a API com o código de 04/11/2026.

create table seg.sessao_uso (
    -- session_id do access token do Supabase Auth (auth.sessions.id); sem FK, como
    -- seg.usuario.id_autenticacao (migration 15)
    sessao_id       uuid primary key,
    id_autenticacao uuid not null,
    ultimo_uso      timestamptz not null default now()
);

create index sessao_uso_ultimo_uso on seg.sessao_uso (ultimo_uso);

comment on table seg.sessao_uso is
    'Último uso (pedido autenticado ou renovação) de cada sessão de login, para encerrar '
    'a sessão parada (AUTH_INATIVIDADE_SEGUNDOS, na API).';

alter table seg.sessao_uso enable row level security;
alter table seg.sessao_uso force row level security;

-- Login: registra a sessão nova e apaga as vencidas (de todos os usuários).
create function seg.iniciar_sessao(
    p_sessao_id uuid, p_id_autenticacao uuid, p_limite interval
)
returns void
language sql
volatile
security definer
set search_path = ''
as $$
    delete from seg.sessao_uso s
     where s.ultimo_uso <= now() - p_limite;

    insert into seg.sessao_uso (sessao_id, id_autenticacao)
    values (p_sessao_id, p_id_autenticacao)
    on conflict (sessao_id) do update set ultimo_uso = now();
$$;

-- Pedido autenticado: atualiza o último uso da sessão, se ela ainda estiver dentro do limite.
create function seg.usar_sessao(
    p_sessao_id uuid, p_id_autenticacao uuid, p_limite interval
)
returns void
language sql
volatile
security definer
set search_path = ''
as $$
    update seg.sessao_uso s
       set ultimo_uso = now()
     where s.sessao_id = p_sessao_id
       and s.id_autenticacao = p_id_autenticacao
       and s.ultimo_uso > now() - p_limite;
$$;

-- Renovação: true = dentro do limite (conta como uso); false = vencida ou sem registro
-- (a linha, se houver, é apagada).
create function seg.renovar_sessao(
    p_sessao_id uuid, p_id_autenticacao uuid, p_limite interval
)
returns boolean
language plpgsql
volatile
security definer
set search_path = ''
as $$
begin
    update seg.sessao_uso s
       set ultimo_uso = now()
     where s.sessao_id = p_sessao_id
       and s.id_autenticacao = p_id_autenticacao
       and s.ultimo_uso > now() - p_limite;
    if found then
        return true;
    end if;
    delete from seg.sessao_uso s
     where s.sessao_id = p_sessao_id
       and s.id_autenticacao = p_id_autenticacao;
    return false;
end
$$;

revoke all on function seg.iniciar_sessao(uuid, uuid, interval) from public;
revoke all on function seg.usar_sessao(uuid, uuid, interval) from public;
revoke all on function seg.renovar_sessao(uuid, uuid, interval) from public;

grant execute on function seg.iniciar_sessao(uuid, uuid, interval) to api_site;
grant execute on function seg.usar_sessao(uuid, uuid, interval) to api_site;
grant execute on function seg.renovar_sessao(uuid, uuid, interval) to api_site;
