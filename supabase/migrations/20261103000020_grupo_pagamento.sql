-- Migration 20 — grupo de pagamento da conta do ERP, no de-para (cfg.conta_dre).
--
-- Por quê: o cartão "Contas a pagar lançadas" da Visão geral (Financeiro) separa o a pagar
-- em grupos (Fornecedores; Custos fixos, despesas e impostos; Dívida; Capex), cada um com o
-- pago por mês e o lançado para os próximos 30 dias. O grupo é da conta do ERP, como o
-- grupo da DRE: a classe de caixa do plano (FCI, FCF) não basta, porque no plano padrão o
-- FCF também tem multas, perdas e receitas.
--
-- grupo_pagamento: 'fornecedores', 'custos', 'divida' ou 'capex'; vazio = a conta fica fora
-- dos grupos e do total do cartão (ex.: retirada de sócios, receita lançada no a pagar).
-- Cliente sem nenhuma conta com grupo: o cartão sai com um grupo só, com tudo.
--
-- Só adição: o api_site já lê a tabela inteira (migration 14). A API funciona sem esta
-- migration (lê o a pagar sem grupos). Conteúdo por cliente em
-- supabase/producao/<cliente>_grupos_pagamento.sql.

alter table cfg.conta_dre
    add column grupo_pagamento text
        check (grupo_pagamento in ('fornecedores', 'custos', 'divida', 'capex'));

comment on column cfg.conta_dre.grupo_pagamento is
    'Grupo de pagamento da conta no cartão "Contas a pagar lançadas": fornecedores, custos, '
    'divida ou capex; vazio = fora dos grupos.';
