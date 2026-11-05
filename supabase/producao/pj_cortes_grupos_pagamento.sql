-- PJ Cortes — grupo de pagamento de cada conta do Winthor (cartão "Contas a pagar lançadas"
-- da Visão geral).
--
-- NÃO é migration: rode no SQL Editor DEPOIS da migration 20 e do pj_cortes_contas_dre.sql
-- (que apaga e regrava o de-para: rodou de novo, rode este também). Pode rodar mais de uma
-- vez. Usa cfg.plano_conta (plano_contas_padrao.sql).
--
-- Regra (revista com o usuário em 04/11/2026, com os dados do a pagar), na ordem:
-- 1. fora dos grupos, conta a conta: diretoria e sócios (900002 a 900007 e 500010
--    treinamento da diretoria), premiações (200012, 402010, 401012 e 300004
--    gratificações), 200003 descontos concedidos (desconto na baixa, sem valor pago),
--    301013 lançamentos de empréstimos (quase tudo entrada de empréstimo, em negativo),
--    893015 mútuo saída e 893010 capitalização (nem gasto da operação nem imobilizado);
-- 2. Fornecedores: verbas de fornecedor (2500xx, menos a 250006, que é comissão), 120.01
--    (compra de mercadoria, descontos obtidos, armazenagem, embalagens) e 120.04 (frete de
--    compra), menos o 120.01.52;
-- 3. Custos: 120.01.52 (ICMS e ST pagos na compra: imposto vai com os impostos) e 100020
--    devolução de cliente (dinheiro devolvido, embora o de-para seja 100.01.17);
-- 4. Capex: classe FCI no plano (420: imobilizado, sistemas, consórcio);
-- 5. Dívida: 400 (amortização de empréstimos e financiamentos) e 170.01.05 (juros de
--    financiamentos);
-- 6. fora dos grupos: retirada de sócios (210), receitas lançadas no a pagar (100, 160 e
--    190: fretes retorno, descarga, juros recebidos, restituição de imposto) e conta fora
--    do caixa no plano (900, transferência entre filiais);
-- 7. Custos: o resto (impostos sobre a venda, pessoal, despesas variáveis e fixas, IRPJ e
--    CSLL, IOF, tarifas, multas, despesas não operacionais).
-- O fora_da_dre não conta: salário e compra de mercadoria fora da DRE são pagamento igual.
--
-- Contas do a pagar sem linha no de-para: as de sem_de_para abaixo ganham uma linha só
-- para o grupo (id_descritivo vazio e fora_da_dre preenchido: a DRE não muda); o grupo
-- delas vale antes da regra. As que ficam fora não ganham linha: 402999 e 402997 acerto
-- de estoque, 201001 fundos de caixa, 37 temporária, 301300 migração (sem movimento de
-- caixa); 101 venda faturada, 107 adiantamento de cliente, 301012 sobra de caixa, 893014
-- mútuo entrada (entradas); 200012 premiação e 900007 despesas da diretoria (regra 1).
--
-- Contagem esperada (de-para de 238 contas + 22 linhas novas): 22 em Fornecedores, 195 em
-- Custos, 5 em Dívida, 15 em Capex e 23 fora dos grupos. Pago de mai a out/2026: R$ 113,1
-- mi, 28,9 mi, 7,1 mi e 3,2 mi.

begin;

create temp table sem_de_para (conta text primary key, descricao text, grupo text)
    on commit drop;

insert into sem_de_para (conta, descricao, grupo) values
    ('100027', 'ADIANTAMENTO A FORNECEDOR',               'fornecedores'),
    ('250015', 'VB PARA REBAIXA DE CUSTO DE MERC. (CMV)', 'fornecedores'),
    ('105',    'DEVOLUCAO DE CLIENTES',                   'custos'),
    ('201012', 'AJUDA COMBUSTIVEL',                       'custos'),
    ('201015', 'CREDITO PAGAMENTO DE VALE',               'custos'),
    ('201016', 'ALUGUEL VEICULAR',                        'custos'),
    ('300036', 'SEGURO PREDIAL',                          'custos'),
    ('301004', 'MULTA E JUROS DE MORA',                   'custos'),
    ('301019', 'OUTRAS DESPESAS NAO OPERACIONAIS',        'custos'),  -- consultoria tributária
    ('401014', 'MANUTENCAO/ ACESSORIOS - VEICULOS',       'custos'),
    ('401044', 'DESPESAS DE VIAGEM - CARRETAS',           'custos'),
    ('401045', 'ESTACIONAMENTO / UBERS',                  'custos'),
    ('401046', 'MULTA / TAXAS',                           'custos'),
    ('401048', 'DIARIA MOTORISTA CAPITAL',                'custos'),
    ('401049', 'DIARIA MOTORISTA INTERIOR',               'custos'),
    ('401050', 'DIARIA LOMBADOR CAPITAL',                 'custos'),
    ('401051', 'DIARIA LOMBADOR INTERIOR',                'custos'),
    ('50004',  null,                                      'custos'),  -- sem nome no Winthor
    ('301018', 'JUROS CONTA GARANTIDA',                   'divida'),
    ('402015', 'AQUISICAO/ EQUIPAMENTOS CD',              'capex'),
    ('500007', 'AQUISICAO EQUIP. INFORMATICA',            'capex'),
    ('893012', 'IMOBILIZADO',                             'capex');

-- linha só para o grupo; se a conta já tem de-para (da DRE), fica a linha que existe
insert into cfg.conta_dre (cliente_id, conta, id_descritivo, descricao, fora_da_dre)
select c.id, s.conta, null, s.descricao,
       'sem de-para da DRE: linha só para o grupo de pagamento'
  from cfg.cliente c
 cross join sem_de_para s
 where c.slug = 'pj-cortes'
    on conflict (cliente_id, conta) do nothing;

with regra as (
    select d.cliente_id,
           d.conta,
           case
               when s.grupo is not null then s.grupo
               when d.conta in ('900002', '900003', '900004', '900005', '900006', '900007',
                                '500010', '200012', '402010', '401012', '300004', '200003',
                                '301013', '893015', '893010')
                   then null
               when d.conta like '2500%' and d.conta <> '250006' then 'fornecedores'
               when d.id_descritivo = '120.01.52' or d.conta = '100020' then 'custos'
               when d.id_descritivo like '120.01.%' or d.id_descritivo like '120.04.%'
                   then 'fornecedores'
               when p.caixa = 'FCI' then 'capex'
               when d.id_descritivo like '400.%' or d.id_descritivo = '170.01.05' then 'divida'
               when d.id_descritivo like '210.%' then null
               when left(d.id_descritivo, 3) in ('100', '160', '190') then null
               when p.caixa is null then null
               else 'custos'
           end as grupo
      from cfg.conta_dre d
      join cfg.cliente c on c.id = d.cliente_id and c.slug = 'pj-cortes'
      left join cfg.plano_conta p on p.id_descritivo = d.id_descritivo
      left join sem_de_para s on s.conta = d.conta
)
update cfg.conta_dre d
   set grupo_pagamento = r.grupo
  from regra r
 where r.cliente_id = d.cliente_id
   and r.conta = d.conta;

commit;

-- conferência: contas por grupo (esperado: 22, 195, 5, 15 e 23 fora)
select coalesce(d.grupo_pagamento, '(fora dos grupos)') as grupo, count(*) as contas
  from cfg.conta_dre d
  join cfg.cliente c on c.id = d.cliente_id and c.slug = 'pj-cortes'
 group by 1
 order by 1;
