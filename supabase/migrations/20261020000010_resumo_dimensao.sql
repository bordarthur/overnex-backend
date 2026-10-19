-- Migration 10 — dimensão no resumo mensal.
--
-- mart.resumo_mensal passa a guardar totais abertos por um valor do arquivo (ex.: receita
-- e custo por en_ramo, para o CMV por ramo da seção Custos). dimensao = '' é o total sem
-- abertura, como antes.
--
-- Só adição: as linhas existentes ficam com dimensao = ''.

alter table mart.resumo_mensal
    add column dimensao text not null default '';

comment on column mart.resumo_mensal.dimensao is
    'Valor da coluna de abertura da medida (ex.: o ramo do cliente). Vazio = total sem abertura.';

-- a unicidade passa a incluir a dimensão
do $$
declare
    r record;
begin
    for r in
        select conname
          from pg_constraint
         where conrelid = 'mart.resumo_mensal'::regclass and contype = 'u'
    loop
        execute format('alter table mart.resumo_mensal drop constraint %I', r.conname);
    end loop;
end
$$;

alter table mart.resumo_mensal
    add constraint resumo_mensal_unico
        unique nulls not distinct (cliente_id, empresa_id, tabela, medida, dimensao, mes);
