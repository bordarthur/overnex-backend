-- Migration 22 — saldo inicial de caixa do cliente.
--
-- Por quê: o fluxo de caixa (DFC) parte de um saldo conhecido e soma o caixa realizado
-- depois dele. No Power BI da PJ, esse saldo é uma tabela à parte (Saldo_inicial: Data
-- 01/04/2025, Saldo −2.103.419,90). Aqui ele fica num cadastro, para a LANN trocar no SQL
-- Editor sem mexer em código nem em migration (conteúdo por cliente em
-- supabase/producao/<cliente>_fluxo_caixa.sql).
--
-- Regra para quem ler (a API do fluxo de caixa):
-- - saldo = saldo de todas as contas do cliente no INÍCIO do dia `data`, antes de qualquer
--   movimento desse dia;
-- - saldo de abertura de um dia D = saldo da linha mais recente com data <= D + o caixa
--   realizado de `data` até a véspera de D. Antes da primeira linha, não há saldo;
-- - mais de uma linha serve para "reancorar" o saldo numa data conferida com o banco: a
--   linha nova vale dali para frente, e as antigas continuam valendo para trás.
--
-- O api_site só lê o do próprio cliente; quem cadastra é a LANN. Só adição.

create table cfg.saldo_inicial (
    cliente_id uuid    not null references cfg.cliente (id),
    data       date    not null,
    saldo      numeric not null,
    -- de onde veio o número (ex.: "Saldo_inicial do Power BI da PJ")
    origem     text,
    primary key (cliente_id, data)
);

comment on table cfg.saldo_inicial is
    'Saldo de caixa do cliente (todas as contas) no início do dia `data`. O fluxo de caixa '
    'usa a linha mais recente com data <= o dia pedido e soma o caixa realizado desde ela.';

alter table cfg.saldo_inicial enable row level security;
alter table cfg.saldo_inicial force row level security;

revoke all on cfg.saldo_inicial from public;
grant select on cfg.saldo_inicial to api_site;

create policy api_site_select on cfg.saldo_inicial
    for select to api_site
    using (cliente_id in (select seg.clientes_do_usuario()));
