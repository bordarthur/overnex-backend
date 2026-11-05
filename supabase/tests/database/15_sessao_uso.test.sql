-- Migration 21: último uso da sessão de login (seg.sessao_uso). O api_site só passa pelas
-- funções; sessão parada há mais que o limite não renova nem volta com um pedido.
begin;

create extension if not exists pgtap with schema extensions;
grant usage on schema extensions to api_site;

select plan(14);

-- sessões da Ana: 'a1' usada há 5 min, 'a2' parada há 40 min, 'a3' parada há 2 h
insert into seg.sessao_uso (sessao_id, id_autenticacao, ultimo_uso) values
    ('5e000000-0000-4000-8000-0000000000a1', '0d000000-0000-4000-8000-00000000000a',
     now() - interval '5 minutes'),
    ('5e000000-0000-4000-8000-0000000000a2', '0d000000-0000-4000-8000-00000000000a',
     now() - interval '40 minutes'),
    ('5e000000-0000-4000-8000-0000000000a3', '0d000000-0000-4000-8000-00000000000a',
     now() - interval '2 hours');

set local role api_site;

select throws_ok(
    $$select * from seg.sessao_uso$$,
    '42501',
    null,
    'api_site não lê seg.sessao_uso direto'
);
select throws_ok(
    $$insert into seg.sessao_uso (sessao_id, id_autenticacao)
      values ('5e000000-0000-4000-8000-0000000000ff', '0d000000-0000-4000-8000-00000000000a')$$,
    '42501',
    null,
    'api_site não insere em seg.sessao_uso direto'
);

-- renovar dentro do limite: true e conta como uso
select ok(
    seg.renovar_sessao('5e000000-0000-4000-8000-0000000000a1',
                       '0d000000-0000-4000-8000-00000000000a', interval '30 minutes'),
    'renovar a sessão usada há 5 min: true'
);
-- de outro usuário: false (e a linha da Ana fica)
select ok(
    not seg.renovar_sessao('5e000000-0000-4000-8000-0000000000a1',
                           '0d000000-0000-4000-8000-00000000000b', interval '30 minutes'),
    'renovar com o id de outro usuário: false'
);
-- parada há 40 min: um pedido não a revive, e o renovar recusa
select lives_ok(
    $$select seg.usar_sessao('5e000000-0000-4000-8000-0000000000a2',
                             '0d000000-0000-4000-8000-00000000000a', interval '30 minutes')$$,
    'usar a sessão parada não dá erro'
);
select ok(
    not seg.renovar_sessao('5e000000-0000-4000-8000-0000000000a2',
                           '0d000000-0000-4000-8000-00000000000a', interval '30 minutes'),
    'renovar a sessão parada há 40 min: false'
);
-- sem registro (aberta antes da migration ou já apagada): false
select ok(
    not seg.renovar_sessao('5e000000-0000-4000-8000-0000000000ff',
                           '0d000000-0000-4000-8000-00000000000a', interval '30 minutes'),
    'renovar sessão sem registro: false'
);
-- o limite é o que a API passa
select ok(
    seg.renovar_sessao('5e000000-0000-4000-8000-0000000000a3',
                       '0d000000-0000-4000-8000-00000000000a', interval '3 hours'),
    'com limite de 3 h, a sessão parada há 2 h renova'
);

-- login novo
select lives_ok(
    $$select seg.iniciar_sessao('5e000000-0000-4000-8000-0000000000b1',
                                '0d000000-0000-4000-8000-00000000000b', interval '30 minutes')$$,
    'iniciar sessão'
);

reset role;

select ok(
    (select ultimo_uso = now() from seg.sessao_uso
      where sessao_id = '5e000000-0000-4000-8000-0000000000a1'),
    'o renovar aceito grava o último uso'
);
select is_empty(
    $$select 1 from seg.sessao_uso where sessao_id = '5e000000-0000-4000-8000-0000000000a2'$$,
    'o renovar recusado apaga a linha'
);
select is(
    (select id_autenticacao from seg.sessao_uso
      where sessao_id = '5e000000-0000-4000-8000-0000000000b1'),
    '0d000000-0000-4000-8000-00000000000b'::uuid,
    'o login grava a sessão nova'
);

-- o login apaga as vencidas pelo limite dele; um pedido atualiza a que está no limite
update seg.sessao_uso set ultimo_uso = now() - interval '1 hour'
 where sessao_id = '5e000000-0000-4000-8000-0000000000a3';
update seg.sessao_uso set ultimo_uso = now() - interval '10 minutes'
 where sessao_id = '5e000000-0000-4000-8000-0000000000b1';

set local role api_site;
do $$
begin
    perform seg.iniciar_sessao('5e000000-0000-4000-8000-0000000000b2',
                               '0d000000-0000-4000-8000-00000000000b', interval '30 minutes');
    perform seg.usar_sessao('5e000000-0000-4000-8000-0000000000b1',
                            '0d000000-0000-4000-8000-00000000000b', interval '30 minutes');
end
$$;
reset role;

select is_empty(
    $$select 1 from seg.sessao_uso where sessao_id = '5e000000-0000-4000-8000-0000000000a3'$$,
    'o login apaga a sessão vencida de outro usuário'
);
select ok(
    (select ultimo_uso = now() from seg.sessao_uso
      where sessao_id = '5e000000-0000-4000-8000-0000000000b1'),
    'o pedido dentro do limite atualiza o último uso'
);

select * from finish();
rollback;
