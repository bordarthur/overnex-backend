-- Migration 28: o tempo de sessão parada sai do cadastro da pessoa (sessao_minutos ou o
-- padrão do perfil). Ana: Cliente principal, 4 h. Dora: admin, 24 h. Bruno: 30 min próprios.
begin;

create extension if not exists pgtap with schema extensions;
grant usage on schema extensions to api_site;

select plan(15);

update seg.usuario set sessao_minutos = 30 where id = 'd1000000-0000-4000-8000-00000000000b';

insert into seg.sessao_uso (sessao_id, id_autenticacao, ultimo_uso) values
    -- Ana: parada há 3 h (dentro das 4 h), há 5 h (fora) e outra há 5 h (para o usar_sessao)
    ('5e000000-0000-4000-8000-0000000000a1', '0d000000-0000-4000-8000-00000000000a', now() - interval '3 hours'),
    ('5e000000-0000-4000-8000-0000000000a2', '0d000000-0000-4000-8000-00000000000a', now() - interval '5 hours'),
    ('5e000000-0000-4000-8000-0000000000a3', '0d000000-0000-4000-8000-00000000000a', now() - interval '5 hours'),
    -- Dora: parada há 20 h (dentro das 24 h)
    ('5e000000-0000-4000-8000-0000000000d1', '0d000000-0000-4000-8000-0000000000ad', now() - interval '20 hours'),
    -- Bruno: parada há 45 min (fora dos 30 min dele)
    ('5e000000-0000-4000-8000-0000000000b1', '0d000000-0000-4000-8000-00000000000b', now() - interval '45 minutes'),
    -- de alguém, parada há 30 h: o próximo login apaga
    ('5e000000-0000-4000-8000-0000000000f1', '0d000000-0000-4000-8000-0000000000c0', now() - interval '30 hours');

set local role api_site;

select is(seg.limite_sessao('0d000000-0000-4000-8000-00000000000a'), interval '4 hours',
          'Cliente principal: padrão de 4 h');
select is(seg.limite_sessao('0d000000-0000-4000-8000-0000000000ad'), interval '24 hours',
          'Admin: padrão de 24 h');
select is(seg.limite_sessao('0d000000-0000-4000-8000-0000000000c0'), interval '8 hours',
          'Consultor: padrão de 8 h');
select is(seg.limite_sessao('0d000000-0000-4000-8000-00000000000b'), interval '30 minutes',
          'tempo próprio da pessoa vale mais que o padrão');
select is(seg.limite_sessao('0d000000-0000-4000-8000-0000000000ff'), null::interval,
          'id não cadastrado: sem limite');

select ok(seg.renovar_sessao('5e000000-0000-4000-8000-0000000000a1',
                             '0d000000-0000-4000-8000-00000000000a'),
          'Ana, parada há 3 h: renova');
select ok(not seg.renovar_sessao('5e000000-0000-4000-8000-0000000000a2',
                                 '0d000000-0000-4000-8000-00000000000a'),
          'Ana, parada há 5 h: não renova');
select ok(seg.renovar_sessao('5e000000-0000-4000-8000-0000000000d1',
                             '0d000000-0000-4000-8000-0000000000ad'),
          'Dora, parada há 20 h: renova');
select ok(not seg.renovar_sessao('5e000000-0000-4000-8000-0000000000b1',
                                 '0d000000-0000-4000-8000-00000000000b'),
          'Bruno, parado há 45 min com 30 min de sessão: não renova');
select ok(not seg.renovar_sessao('5e000000-0000-4000-8000-0000000000ff',
                                 '0d000000-0000-4000-8000-0000000000ff'),
          'id não cadastrado: não renova');

-- um pedido não revive a sessão parada; o login registra a sessão e o primeiro acesso
do $$
begin
    perform seg.usar_sessao('5e000000-0000-4000-8000-0000000000a3',
                            '0d000000-0000-4000-8000-00000000000a');
    perform seg.iniciar_sessao('5e000000-0000-4000-8000-0000000000b2',
                               '0d000000-0000-4000-8000-00000000000b');
end
$$;

reset role;

select ok((select ultimo_uso < now() - interval '4 hours' from seg.sessao_uso
            where sessao_id = '5e000000-0000-4000-8000-0000000000a3'),
          'o pedido não revive a sessão parada além do limite');
select is((select id_autenticacao from seg.sessao_uso
            where sessao_id = '5e000000-0000-4000-8000-0000000000b2'),
          '0d000000-0000-4000-8000-00000000000b'::uuid, 'o login grava a sessão nova');
select ok((select primeiro_acesso_em is not null from seg.usuario
            where id = 'd1000000-0000-4000-8000-00000000000b'),
          'o login marca o primeiro acesso');
select is_empty(
    $$select 1 from seg.sessao_uso where sessao_id = '5e000000-0000-4000-8000-0000000000f1'$$,
    'o login apaga as sessões paradas há mais de 24 h');
select has_function('seg', 'usar_sessao', array['uuid', 'uuid', 'interval'],
                    'as funções de 3 argumentos continuam (API antiga)');

select * from finish();
rollback;
