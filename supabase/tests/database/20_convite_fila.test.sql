-- Migration 29: fila de convites da Gestão. Só o admin (Dora) pede convite e lê a fila; o
-- pedido leva quem pediu; a API continua sem criar usuário direto nem mexer no resultado; o
-- role carga só alcança seg pelas duas funções do job; e quem não é admin (Ana) não ganha nada.
begin;

create extension if not exists pgtap with schema extensions;
grant usage on schema extensions to api_site, carga;

select plan(27);

select col_is_null('seg', 'usuario', 'id_autenticacao',
                   'id_autenticacao aceita null (convidado ainda sem login)');

-- ---------------------------------------------------------------------------
-- Dora (admin) pede o convite
-- ---------------------------------------------------------------------------
set local role api_site;
set local app.usuario_id = 'd1000000-0000-4000-8000-0000000000ad';

select lives_ok(
    $$select seg.pedir_convite(
          ' Carla@Alfa.example ', ' Carla Alfa ', 'cliente_usuario', null,
          array['c1000000-0000-4000-8000-00000000000a']::uuid[])$$,
    'admin pede o convite de uma pessoa nova');
select results_eq(
    $$select email, nome, tipo, perfil, id_autenticacao is null, convidado_em is null
        from seg.usuario where email = 'carla@alfa.example'$$,
    $$values ('carla@alfa.example', 'Carla Alfa', 'cliente', 'cliente_usuario', true, true)$$,
    'a pessoa nasce sem login e sem data de convite, com o e-mail em minúsculas');
select is((select count(*)::int
             from seg.usuario_cliente uc
             join seg.usuario u on u.id = uc.usuario_id
            where u.email = 'carla@alfa.example' and uc.papel = 'leitor'),
          1, 'com o vínculo do perfil');
select results_eq(
    $$select f.estado, f.pedido_por, f.motivo, f.processado_em
        from seg.convite_fila f
        join seg.usuario u on u.id = f.usuario_id
       where u.email = 'carla@alfa.example'$$,
    $$values ('fila', 'd1000000-0000-4000-8000-0000000000ad'::uuid, null::text,
              null::timestamptz)$$,
    'o pedido entra na fila com quem pediu');
select throws_ok(
    $$select seg.pedir_convite('carla@alfa.example', 'Outra', 'cliente_usuario', null, '{}')$$,
    '23505', null, 'um cadastro por e-mail');
select throws_ok(
    $$select seg.pedir_convite('longa@alfa.example', 'Longa', 'cliente_usuario', 1440, '{}')$$,
    '23514', null, 'mais de 12 h de sessão continua só para o admin');
select throws_ok(
    $$insert into seg.usuario (email, tipo) values ('direto@alfa.example', 'cliente')$$,
    '42501', null, 'a API continua sem inserir em seg.usuario direto');

-- reenvio: insert direto na fila, pelo RLS
select throws_ok(
    $$insert into seg.convite_fila (usuario_id, pedido_por)
      select id, 'd1000000-0000-4000-8000-0000000000ad' from seg.usuario
       where email = 'carla@alfa.example'$$,
    '23505', null, 'um pedido pendente por pessoa');
select lives_ok(
    $$insert into seg.convite_fila (usuario_id, pedido_por)
      values ('d1000000-0000-4000-8000-00000000000b', 'd1000000-0000-4000-8000-0000000000ad')$$,
    'admin põe na fila o convite de quem já existe');
select throws_ok(
    $$insert into seg.convite_fila (usuario_id, pedido_por)
      values ('d1000000-0000-4000-8000-0000000000c0', 'd1000000-0000-4000-8000-00000000000a')$$,
    '42501', null, 'o pedido não sai em nome de outra pessoa');
select throws_ok(
    $$insert into seg.convite_fila (usuario_id, pedido_por, estado)
      values ('d1000000-0000-4000-8000-0000000000c0',
              'd1000000-0000-4000-8000-0000000000ad', 'enviado')$$,
    '42501', null, 'a API não escolhe o estado do pedido');
select throws_ok(
    $$update seg.convite_fila set estado = 'enviado', processado_em = now()$$,
    '42501', null, 'a API não grava o resultado do envio');
select throws_ok(
    $$select * from seg.convites_na_fila()$$,
    '42501', null, 'a API não lê a fila do job');
select throws_ok(
    $$select seg.concluir_convite(1, null, 'x')$$,
    '42501', null, 'a API não conclui convite');

-- ---------------------------------------------------------------------------
-- Ana (Cliente principal de A): nada disso
-- ---------------------------------------------------------------------------
set local app.usuario_id = 'd1000000-0000-4000-8000-00000000000a';

select throws_ok(
    $$select seg.pedir_convite('eva@alfa.example', 'Eva', 'cliente_usuario', null, '{}')$$,
    '42501', null, 'Ana não pede convite');
select is((select count(*)::int from seg.convite_fila), 0, 'Ana não lê a fila');
select throws_ok(
    $$insert into seg.convite_fila (usuario_id, pedido_por)
      values ('d1000000-0000-4000-8000-0000000000c0', 'd1000000-0000-4000-8000-00000000000a')$$,
    '42501', null, 'Ana não põe convite na fila');

-- ---------------------------------------------------------------------------
-- job de convites (role carga)
-- ---------------------------------------------------------------------------
reset role;
set local role carga;

select is((select count(*)::int from seg.convites_na_fila()), 2, 'o job lê os dois pedidos pendentes');
select throws_ok('select count(*) from seg.convite_fila', '42501', null,
                 'carga não lê a fila direto');
select throws_ok('select count(*) from seg.usuario', '42501', null,
                 'carga continua sem ler seg.usuario');
select throws_ok(
    $$select seg.pedir_convite('eva@alfa.example', 'Eva', 'cliente_usuario', null, '{}')$$,
    '42501', null, 'carga não pede convite');

-- a Carla recebeu o convite; o do Bruno falhou
select lives_ok(
    $$select seg.concluir_convite(
          (select convite_id from seg.convites_na_fila() where email = 'carla@alfa.example'),
          '0d000000-0000-4000-8000-0000000000c1', null)$$,
    'o job grava o convite enviado');
select lives_ok(
    $$select seg.concluir_convite(
          (select convite_id from seg.convites_na_fila() where email = 'bruno@beta.example'),
          null, 'O limite de e-mails do Supabase foi atingido.')$$,
    'o job grava o convite que não saiu');
select is((select count(*)::int from seg.convites_na_fila()), 0, 'a fila ficou vazia');

reset role;

select results_eq(
    $$select u.id_autenticacao, u.convidado_em is not null, f.estado, f.motivo
        from seg.usuario u
        join seg.convite_fila f on f.usuario_id = u.id
       where u.email in ('carla@alfa.example', 'bruno@beta.example')
       order by u.email$$,
    $$values ('0d000000-0000-4000-8000-00000000000b'::uuid, false, 'falhou',
              'O limite de e-mails do Supabase foi atingido.'),
             ('0d000000-0000-4000-8000-0000000000c1'::uuid, true, 'enviado', null)$$,
    'enviado: a pessoa ganha o login e a data do convite; falhou: fica como estava, com o motivo');
select throws_ok(
    $$update seg.convite_fila set estado = 'falhou' where estado = 'enviado'$$,
    '23514', null, 'falha sem motivo não existe');

select * from finish();
rollback;
