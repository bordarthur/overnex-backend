-- Migration 17 — plano de contas gerencial padrão da LANN.
--
-- Por quê: a DRE (Visão geral e painel Resultado) e o fluxo de caixa precisam saber, para
-- cada Id_Descritivo, em que demonstrativo a conta entra e com que sinal. O plano é o
-- "Plano de Contas Padrão" da LANN (aba Plano_Contas), o mesmo para todos os clientes:
-- - competencia: 'CO' só na DRE (ex.: PDD, devoluções, depreciação), 'CA' só no caixa
--   (ex.: empréstimos, investimentos, recebimento indevido), 'CO/CA' nos dois. A DRE usa
--   CO e CO/CA; o caixa, CA e CO/CA. Conta sem valor não entra em nenhum;
-- - fator: +1 soma no resultado, −1 subtrai (as redutoras de custo têm +1);
-- - caixa: classe do fluxo de caixa (FCO operação, FCI investimento, FCF financiamento).
--
-- Global (sem cliente): qualquer usuário da API lê. O conteúdo vem de
-- supabase/producao/plano_contas_padrao.sql (também no seed local).

create table cfg.plano_conta (
    -- normalizado: números separados por ponto ("140.01.01")
    id_descritivo text primary key check (id_descritivo ~ '^\d+(\.\d+)*$'),
    descritivo    text not null,
    id_nivel_1    text not null,
    nivel_1       text not null,
    id_nivel_2    text not null,
    nivel_2       text not null,
    fator         smallint not null check (fator in (-1, 1)),
    caixa         text check (caixa in ('FCO', 'FCI', 'FCF')),
    competencia   text check (competencia in ('CO', 'CA', 'CO/CA'))
);

comment on table cfg.plano_conta is
    'Plano de contas gerencial padrão da LANN. competencia: CO = só DRE, CA = só caixa, '
    'CO/CA = os dois; fator: +1 soma no resultado, -1 subtrai; caixa: FCO, FCI ou FCF.';

alter table cfg.plano_conta enable row level security;
alter table cfg.plano_conta force row level security;

revoke all on cfg.plano_conta from public;
grant select on cfg.plano_conta to api_site;

create policy api_site_select on cfg.plano_conta
    for select to api_site
    using (true);
