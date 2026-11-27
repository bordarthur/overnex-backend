-- Migrations 30 e 31: empresa nova pela Gestão e a edição do nome. Só o admin (Dora) cria o
-- cfg.cliente, que nasce com os 3 perfis fixos, e troca o nome; ninguém troca o slug,
-- desativa nem apaga cliente pela API; e empresa (CNPJ) nova entra sozinha nos vínculos que
-- o cliente já tem. O segmento (o "Ramo") saiu na migration 37 (teste 27).
begin;

create extension if not exists pgtap with schema extensions;
grant usage on schema extensions to api_site;

select plan(17);

-- ---------------------------------------------------------------------------
-- Dora (admin) cria a empresa
-- ---------------------------------------------------------------------------
set local role api_site;
set local app.usuario_id = 'd1000000-0000-4000-8000-0000000000ad';

select lives_ok(
    $$insert into cfg.cliente (nome, slug) values ('Nova Gama', 'nova-gama')$$,
    'admin cria a empresa');
select results_eq(
    $$select nome, ativo from cfg.cliente where slug = 'nova-gama'$$,
    $$values ('Nova Gama', true)$$,
    'a empresa nova aparece para o admin');
select is((select count(*)::int
             from cfg.perfil_acesso p
             join cfg.cliente c on c.id = p.cliente_id
            where c.slug = 'nova-gama' and p.perfil is not null),
          3, 'e nasce com os 3 perfis fixos');
select throws_ok(
    $$insert into cfg.cliente (nome, slug) values ('Outra', 'nova-gama')$$,
    '23505', null, 'um slug por empresa');
select throws_ok(
    $$insert into cfg.cliente (nome, slug) values ('Outra', 'Com Espaço')$$,
    '23514', null, 'slug fora do formato');

-- o nome muda; slug e ativo não, e a empresa não se apaga
select lives_ok(
    $$update cfg.cliente set nome = 'Nova Gama Ltda' where slug = 'nova-gama'$$,
    'admin troca o nome');
select results_eq(
    $$select nome from cfg.cliente where slug = 'nova-gama'$$,
    $$values ('Nova Gama Ltda')$$,
    'nome trocado');
select throws_ok(
    $$update cfg.cliente set slug = 'outro-slug' where slug = 'nova-gama'$$,
    '42501', null, 'o slug não muda pela API');
select throws_ok(
    $$update cfg.cliente set ativo = false where slug = 'nova-gama'$$,
    '42501', null, 'a empresa não se desativa pela API');
select throws_ok(
    $$delete from cfg.cliente where slug = 'nova-gama'$$,
    '42501', null, 'a empresa não se apaga pela API');

-- já dá para vincular alguém à empresa nova, antes de existir CNPJ
select lives_ok(
    $$select seg.definir_vinculos(
          'd1000000-0000-4000-8000-00000000000b',
          array['c1000000-0000-4000-8000-00000000000b',
                (select id from cfg.cliente where slug = 'nova-gama')]::uuid[])$$,
    'admin vincula o Bruno à empresa nova');
select is((select count(*)::int
             from seg.usuario_empresa ue
             join cfg.cliente c on c.id = ue.cliente_id
            where c.slug = 'nova-gama'),
          0, 'sem CNPJ cadastrado, o vínculo ainda não tem empresa');

-- ---------------------------------------------------------------------------
-- Ana (Cliente principal de A) não cria empresa
-- ---------------------------------------------------------------------------
set local app.usuario_id = 'd1000000-0000-4000-8000-00000000000a';

select throws_ok(
    $$insert into cfg.cliente (nome, slug) values ('Da Ana', 'da-ana')$$,
    '42501', null, 'Ana não cria empresa');
-- o update da Ana no próprio cliente não acha a linha (RLS)
update cfg.cliente set nome = 'Invadido'
 where id = 'c1000000-0000-4000-8000-00000000000a';

-- ---------------------------------------------------------------------------
-- o CNPJ chega depois (script, como postgres) e entra nos vínculos que já existem
-- ---------------------------------------------------------------------------
reset role;

select is((select nome from cfg.cliente
            where id = 'c1000000-0000-4000-8000-00000000000a'),
          'Padrão LANN Alfa', 'o update da Ana não mexeu no nome do cliente dela');

insert into cfg.empresa (cliente_id, nome)
select id, 'Nova Gama Matriz' from cfg.cliente where slug = 'nova-gama';

select results_eq(
    $$select ue.usuario_id, e.nome
        from seg.usuario_empresa ue
        join cfg.empresa e on e.id = ue.empresa_id
        join cfg.cliente c on c.id = ue.cliente_id
       where c.slug = 'nova-gama'$$,
    $$values ('d1000000-0000-4000-8000-00000000000b'::uuid, 'Nova Gama Matriz')$$,
    'a empresa nova entrou no vínculo do Bruno');

-- e só nos vínculos do cliente dela
insert into cfg.empresa (cliente_id, nome)
values ('c1000000-0000-4000-8000-00000000000a', 'Alfa Filial Nova');

select is((select count(*)::int
             from seg.usuario_empresa ue
             join cfg.empresa e on e.id = ue.empresa_id
            where e.nome = 'Alfa Filial Nova'
              and ue.usuario_id = 'd1000000-0000-4000-8000-00000000000b'),
          0, 'empresa de outro cliente não entra no vínculo do Bruno');
select is((select count(*)::int
             from seg.usuario_empresa ue
             join cfg.empresa e on e.id = ue.empresa_id
            where e.nome = 'Alfa Filial Nova'),
          (select count(*)::int from seg.usuario_cliente
            where cliente_id = 'c1000000-0000-4000-8000-00000000000a'),
          'mas entra em todos os vínculos do cliente A');

select * from finish();
rollback;
