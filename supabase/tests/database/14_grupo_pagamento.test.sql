-- cfg.conta_dre.grupo_pagamento: só os quatro grupos (ou vazio), e o api_site lê o do
-- próprio cliente. Conta sem de-para da DRE pode ter uma linha só para o grupo
-- (id_descritivo vazio e fora_da_dre preenchido), como em pj_cortes_grupos_pagamento.sql.
begin;

create extension if not exists pgtap with schema extensions;
grant usage on schema extensions to api_site;

select plan(5);

insert into cfg.conta_dre (cliente_id, conta, id_descritivo, descricao, grupo_pagamento) values
    ('c1000000-0000-4000-8000-00000000000a', '100001', '120.01.01', 'CMV', 'fornecedores'),
    ('c1000000-0000-4000-8000-00000000000a', '900004', '210.01.01', 'Retirada', null),
    ('c1000000-0000-4000-8000-00000000000b', '4043', '420.02.07', 'Imobilizado', 'capex');

insert into cfg.conta_dre (cliente_id, conta, id_descritivo, descricao, fora_da_dre,
                           grupo_pagamento) values
    ('c1000000-0000-4000-8000-00000000000a', '301019', null, 'Outras despesas',
     'sem de-para da DRE: linha só para o grupo de pagamento', 'custos');

select throws_ok($$insert into cfg.conta_dre (cliente_id, conta, id_descritivo, grupo_pagamento)
                   values ('c1000000-0000-4000-8000-00000000000a', '1', '140.01.01', 'outros')$$,
                 '23514', null, 'grupo fora dos quatro: recusado');

set local role api_site;
set local app.usuario_id = 'd1000000-0000-4000-8000-00000000000a';
select is((select count(*)::int from cfg.conta_dre where grupo_pagamento is not null), 2,
          'A lê só os grupos do próprio de-para');
select is((select grupo_pagamento from cfg.conta_dre where conta = '100001'), 'fornecedores',
          'grupo lido pela API');
select is((select count(*)::int from cfg.conta_dre where conta = '900004' and grupo_pagamento is null),
          1, 'conta sem grupo continua no de-para');
select is((select grupo_pagamento from cfg.conta_dre
            where conta = '301019' and id_descritivo is null), 'custos',
          'linha só para o grupo, sem de-para da DRE');
reset role;

select * from finish();
rollback;
