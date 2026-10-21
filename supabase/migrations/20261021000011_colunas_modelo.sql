-- Migration 11 — colunas do modelo por fonte.
--
-- Por quê: o resumo mensal (carga) e a Visão geral (API) leem as tabelas de vendas e de
-- metas pelos nomes de coluna do DW da Nilo Maia (en_VALTOT, en_datemicao, Competencia…).
-- Clientes com outro DW usam outros nomes (ex.: PJ Cortes: en_valortotal, en_dataemissao).
--
-- colunas_modelo: papel → coluna do arquivo, só para os papéis que mudam; o que faltar
-- usa o nome padrão. Papéis (ver jobs/carga/carga/resumo.py e api/app/telas/visao_geral):
--   f_vendas_*: data, valor, custo, pedido, cliente, produto, abertura (CMV por …)
--   f_metas_*:  data, valor
-- Ex.: {"data": "en_dataemissao", "valor": "en_valortotal", "abertura": "it_grupo"}
--
-- Só adição.

alter table cfg.fonte_carga
    add column colunas_modelo jsonb not null default '{}'::jsonb
        check (jsonb_typeof(colunas_modelo) = 'object');

comment on column cfg.fonte_carga.colunas_modelo is
    'Tipo tabela: papel → coluna do arquivo para o resumo mensal e a Visão geral '
    '(ex.: {"valor": "en_valortotal"}); vazio = nomes do DW padrão.';
