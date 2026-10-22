-- Migration 14 — de-para da conta do ERP para o plano gerencial da DRE.
--
-- Por quê: as despesas do contas a pagar (f_despesas_*) vêm com a conta do ERP do cliente
-- (ex.: PJ Cortes, Winthor: en_codigoplanocliente = '100001' COMPRA MERCADORIA), sem o
-- Id_Descritivo do plano gerencial. A seção Resultado da Visão geral junta as despesas a
-- esta tabela para saber o grupo da DRE de cada conta (3 primeiros dígitos do
-- Id_Descritivo: 100 receita bruta, 110 deduções, 120 custos…).
--
-- fora_da_dre: motivo de a conta não entrar na DRE mesmo tendo Id_Descritivo (ex.: compra
-- de mercadoria, que entra pelo CMV das vendas; impostos que vão para rateio). Vazio =
-- entra. Conta sem linha aqui também fica fora.
--
-- Só adição. O conteúdo é por cliente, em supabase/producao/<cliente>_contas_dre.sql.

create table cfg.conta_dre (
    cliente_id    uuid not null references cfg.cliente (id),
    conta         text not null,
    id_descritivo text,
    descricao     text,
    fora_da_dre   text,
    primary key (cliente_id, conta),
    check (id_descritivo is not null or fora_da_dre is not null)
);

comment on table cfg.conta_dre is
    'De-para da conta do ERP do cliente (ex.: en_codigoplanocliente) para o Id_Descritivo do '
    'plano gerencial da DRE; fora_da_dre preenchido = a conta não entra na DRE (motivo).';

alter table cfg.conta_dre enable row level security;
alter table cfg.conta_dre force row level security;

revoke all on cfg.conta_dre from public;
grant select on cfg.conta_dre to api_site;

create policy api_site_select on cfg.conta_dre
    for select to api_site
    using (cliente_id in (select seg.clientes_do_usuario()));
