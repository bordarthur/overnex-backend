-- Admin BORD (arthur.duarte@bordgestao.com.br) — cadastra a pessoa no Overnex com o perfil
-- admin e liga o cadastro ao usuário do Supabase Auth.
--
-- NÃO é migration: rode no SQL Editor do Supabase (como postgres, que lê auth.users).
-- Pode rodar de novo.
--
-- Antes:
--   1. migrations 25 a 28 aplicadas e a API com o código de 10/11/2026 publicada;
--   2. o usuário criado no Supabase Auth: Authentication → Users → Add user → "Send
--      invitation" (chega o e-mail do convite, que leva a /definir-senha) ou "Create new
--      user" (com senha e "Auto Confirm User").
--
-- Por que não o CLI (python -m app.cli.convidar_usuario --perfil admin): ele serve, mas o
-- SMTP padrão do Supabase só entrega para e-mails do time do projeto; enquanto não houver
-- SMTP próprio (ver TODO.md), o convite do CLI pode não chegar.
--
-- O admin vê todos os clientes sem vínculo: nenhuma linha em seg.usuario_cliente. Para outro
-- admin, troque o e-mail e o nome abaixo (o e-mail precisa ser de um domínio de
-- ADMIN_DOMINIOS da API).

begin;

do $$
declare
    v_email constant text := 'arthur.duarte@bordgestao.com.br';
    v_nome  constant text := 'Arthur Duarte';
    v_id    uuid;
    v_usuario uuid;
begin
    select a.id into v_id
      from auth.users a
     where lower(a.email) = v_email;
    if v_id is null then
        raise exception 'crie primeiro o usuário % no Supabase Auth (Authentication → Users)', v_email;
    end if;

    select u.id into v_usuario from seg.usuario u where lower(u.email) = v_email;
    if v_usuario is null then
        insert into seg.usuario (id_autenticacao, email, nome, tipo, perfil, convidado_em)
        values (v_id, v_email, v_nome, 'lann', 'admin', now())
        returning id into v_usuario;
    else
        update seg.usuario
           set id_autenticacao = v_id, perfil = 'admin', ativo = true, desativado_em = null
         where id = v_usuario;
    end if;

    -- admin não tem vínculos (se a pessoa já existia com outro perfil, eles saem)
    perform seg.definir_vinculos(v_usuario, '{}'::uuid[]);
end
$$;

commit;

-- conferência: o admin, e o usuário de desenvolvimento só na PJ (Cliente principal)
select u.email, u.nome, u.perfil, u.ativo,
       coalesce(u.sessao_minutos, seg.sessao_padrao(u.perfil)) as sessao_minutos,
       (select string_agg(c.slug, ', ' order by c.slug)
          from seg.usuario_cliente uc
          join cfg.cliente c on c.id = uc.cliente_id
         where uc.usuario_id = u.id) as clientes
  from seg.usuario u
 where lower(u.email) in ('arthur.duarte@bordgestao.com.br', 'diretoria@lannconsult.com.br')
 order by u.email;
