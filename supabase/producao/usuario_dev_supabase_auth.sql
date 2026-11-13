-- Usuário de desenvolvimento (diretoria@lannconsult.com.br) — liga o cadastro do Overnex ao
-- usuário do Supabase Auth, para entrar com e-mail e senha.
--
-- NÃO é migration: rode no SQL Editor do Supabase (como postgres, que lê auth.users).
-- Antes:
--   1. migration 15 aplicada (seg.usuario.id_autenticacao) e pj_cortes_usuario_dev.sql rodado;
--   2. o usuário criado no Supabase Auth: Authentication → Users → Add user → "Send
--      invitation" (chega o e-mail do convite, que leva a /definir-senha) ou "Create new
--      user" (com senha e "Auto Confirm User").
--
-- Troca o id_autenticacao provisório (0d000000-0000-4000-8000-0000000001a1, o do token
-- fake) pelo id de auth.users. Depois disso, o token fake antigo para de funcionar: quem
-- ainda usar AUTH_FAKE no front precisa pôr o id novo em VITE_AUTH_FAKE_OBJECT_ID (a
-- consulta no fim mostra o id).

begin;

do $$
declare
    v_email constant text := 'diretoria@lannconsult.com.br';
    v_id uuid;
begin
    select a.id into v_id
      from auth.users a
     where lower(a.email) = v_email;
    if v_id is null then
        raise exception 'crie primeiro o usuário % no Supabase Auth (Authentication → Users)', v_email;
    end if;

    update seg.usuario u
       set id_autenticacao = v_id
     where lower(u.email) = v_email;
    if not found then
        raise exception 'usuário % não existe em seg.usuario (rode pj_cortes_usuario_dev.sql)', v_email;
    end if;
end
$$;

-- Nome mostrado no Overnex (opcional):
-- update seg.usuario set nome = '<nome>' where lower(email) = 'diretoria@lannconsult.com.br';

commit;

-- id para o VITE_AUTH_FAKE_OBJECT_ID do front (se continuar com o token fake):
select u.id_autenticacao, u.email, u.nome, u.ativo
  from seg.usuario u
 where lower(u.email) = 'diretoria@lannconsult.com.br';
