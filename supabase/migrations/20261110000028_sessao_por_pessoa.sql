-- Migration 28 — tempo de sessão parada por pessoa.
--
-- Por quê: a sessão caía depois de 30 min sem uso para todo mundo (o limite vinha da API,
-- em AUTH_INATIVIDADE_SEGUNDOS). O mockup Gestão M2.1.0 pede o tempo por pessoa, de 30 min
-- a 24 h, com padrão pelo perfil (Admin 24 h, Consultor 8 h, clientes 4 h) e mais de 12 h
-- só para o admin: a TV da recepção, por exemplo, fica com 12 h.
--
-- O limite passa a sair do cadastro: seg.usuario.sessao_minutos ou, sem ele, o padrão do
-- perfil (seg.sessao_padrao, migration 25). As funções novas têm 2 argumentos (sem o
-- limite). As de 3 argumentos, da migration 21, ficam como estão para a API antiga
-- continuar funcionando até a nova subir; uma migration futura as remove.
--
-- Efeito em produção: quem tinha 30 min passa a ter o padrão do perfil (4 h para os
-- clientes) assim que a API nova subir.
--
-- seg.iniciar_sessao também grava o primeiro login da pessoa (seg.usuario.primeiro_acesso_em),
-- que a Gestão usa para distinguir "convite enviado" de "ativo".
--
-- A API nova depende desta migration: aplique antes de subir a API com o código de
-- 10/11/2026.

-- Limite da pessoa; null se o id não está cadastrado.
create function seg.limite_sessao(p_id_autenticacao uuid)
returns interval
language sql
stable
security definer
set search_path = ''
as $$
    select make_interval(mins => coalesce(u.sessao_minutos, seg.sessao_padrao(u.perfil)))
      from seg.usuario u
     where u.id_autenticacao = p_id_autenticacao
$$;

comment on function seg.limite_sessao(uuid) is
    'Tempo de sessão parada da pessoa: seg.usuario.sessao_minutos ou o padrão do perfil.';

-- Login: registra a sessão nova, marca o primeiro acesso e apaga as sessões paradas há
-- mais de 24 h (o maior limite possível), de todos os usuários.
create function seg.iniciar_sessao(p_sessao_id uuid, p_id_autenticacao uuid)
returns void
language sql
volatile
security definer
set search_path = ''
as $$
    delete from seg.sessao_uso s
     where s.ultimo_uso <= now() - interval '24 hours';

    insert into seg.sessao_uso (sessao_id, id_autenticacao)
    values (p_sessao_id, p_id_autenticacao)
    on conflict (sessao_id) do update set ultimo_uso = now();

    update seg.usuario u
       set primeiro_acesso_em = now()
     where u.id_autenticacao = p_id_autenticacao
       and u.primeiro_acesso_em is null;
$$;

-- Pedido autenticado: atualiza o último uso, se a sessão ainda está dentro do limite da
-- pessoa.
create function seg.usar_sessao(p_sessao_id uuid, p_id_autenticacao uuid)
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
       and s.ultimo_uso > now() - seg.limite_sessao(p_id_autenticacao);
$$;

-- Renovação: true = dentro do limite da pessoa (conta como uso); false = vencida, sem
-- registro ou pessoa não cadastrada (a linha, se houver, é apagada).
create function seg.renovar_sessao(p_sessao_id uuid, p_id_autenticacao uuid)
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
       and s.ultimo_uso > now() - seg.limite_sessao(p_id_autenticacao);
    if found then
        return true;
    end if;
    delete from seg.sessao_uso s
     where s.sessao_id = p_sessao_id
       and s.id_autenticacao = p_id_autenticacao;
    return false;
end
$$;

revoke all on function seg.limite_sessao(uuid) from public;
revoke all on function seg.iniciar_sessao(uuid, uuid) from public;
revoke all on function seg.usar_sessao(uuid, uuid) from public;
revoke all on function seg.renovar_sessao(uuid, uuid) from public;

grant execute on function seg.limite_sessao(uuid) to api_site;
grant execute on function seg.iniciar_sessao(uuid, uuid) to api_site;
grant execute on function seg.usar_sessao(uuid, uuid) to api_site;
grant execute on function seg.renovar_sessao(uuid, uuid) to api_site;
