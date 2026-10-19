-- Migration 8 — histórico mensal agregado e parâmetros do cliente.
--
-- mart.resumo_mensal: totais por mês das tabelas de movimento (ex.: faturado, pedidos e
-- clientes de f_vendas_*). A carga calcula esses totais a partir do arquivo INTEIRO,
-- antes da janela de datas: as tabelas com janela guardam 1 mês, mas a Visão geral pede
-- tendência de 6 meses e acumulado do ano. São poucas linhas por mês.
--
-- cfg.parametro_cliente: parâmetros do cliente inteiro, como as faixas do farol de um
-- indicador calculado sobre tabela sem empresa. cfg.parametro continua para o que é
-- por empresa.
--
-- Só adição.

-- ---------------------------------------------------------------------------
-- mart.resumo_mensal
-- ---------------------------------------------------------------------------

create table mart.resumo_mensal (
    cliente_id    uuid    not null references cfg.cliente (id),
    -- null = tabela sem coluna de empresa (dado do cliente inteiro)
    empresa_id    uuid,
    -- tabela de dados de origem (cfg.fonte_carga.tabela_destino)
    tabela        text    not null check (tabela ~ '^[a-z][a-z0-9_]{0,62}$'),
    medida        text    not null check (medida ~ '^[a-z][a-z0-9_]{0,62}$'),
    mes           date    not null check (mes = date_trunc('month', mes)::date),
    valor         numeric not null,
    -- snapshot do arquivo que gerou o total
    data_snapshot date    not null,
    unique nulls not distinct (cliente_id, empresa_id, tabela, medida, mes),
    foreign key (empresa_id, cliente_id) references cfg.empresa (id, cliente_id)
);

comment on table mart.resumo_mensal is
    'Totais mensais das tabelas de movimento, calculados pela carga sobre o arquivo inteiro '
    '(antes da janela de datas). A carga substitui todas as linhas do cliente e da tabela.';

alter table mart.resumo_mensal enable row level security;
alter table mart.resumo_mensal force row level security;

revoke all on mart.resumo_mensal from public;
grant select on mart.resumo_mensal to api_site;
grant select, insert, update, delete on mart.resumo_mensal to carga;

create policy api_site_select on mart.resumo_mensal
    for select to api_site
    using (cliente_id in (select seg.clientes_do_usuario())
           and (empresa_id is null or empresa_id in (select seg.empresas_do_usuario())));

create policy carga_all on mart.resumo_mensal
    for all to carga using (true) with check (true);

-- ---------------------------------------------------------------------------
-- cfg.parametro_cliente
-- ---------------------------------------------------------------------------

create table cfg.parametro_cliente (
    cliente_id  uuid  not null references cfg.cliente (id),
    chave       text  not null,
    valor       jsonb not null,
    vigente_de  date  not null,
    vigente_ate date,
    primary key (cliente_id, chave, vigente_de),
    check (vigente_ate is null or vigente_ate >= vigente_de)
);

comment on table cfg.parametro_cliente is
    'Parâmetros do cliente inteiro (ex.: farol.vendas_meta com as faixas do velocímetro). '
    'Parâmetro por empresa fica em cfg.parametro.';

alter table cfg.parametro_cliente enable row level security;
alter table cfg.parametro_cliente force row level security;

revoke all on cfg.parametro_cliente from public;
grant select on cfg.parametro_cliente to api_site;

create policy api_site_select on cfg.parametro_cliente
    for select to api_site
    using (cliente_id in (select seg.clientes_do_usuario()));
