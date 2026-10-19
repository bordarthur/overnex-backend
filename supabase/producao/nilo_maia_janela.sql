-- Nilo Maia — janela de 3 meses nas tabelas de movimento (produção).
--
-- NÃO é migration: rode no SQL Editor DEPOIS de aplicar a migration 6
-- (supabase db push). Não ativa nenhuma fonte; só configura a janela.
--
-- Coluna escolhida: a data de emissão quando a tabela tem uma; senão, a data que houver.
-- Tabelas de posição (inadimplência, contas a pagar/receber, estoque, saldos bancários)
-- ficam sem janela: cortar por data esconderia títulos ainda em aberto. Balanço, metas,
-- orçamento e pedidos são pequenas e entram inteiras.

begin;

update cfg.fonte_carga f
   set coluna_data = v.coluna_data,
       meses_retidos = 3
  from cfg.cliente c,
       (values
        -- tabela                               coluna de data
        ('f_vendas_nilo_maia',                  'en_datemicao'),       -- emissão (en_datemi não tem o mês atual)
        ('f_receitas_nilo_maia',                'en_datemi'),          -- emissão
        ('f_compras_nilo_maia',                 'en_datemi'),          -- emissão
        ('f_recebidas_nilo_maia',               'en_dataemissao'),     -- emissão
        ('f_lancamentos_agrupados_nilo_maia',   'DATA_COMPETENCIA'),   -- sem emissão
        ('f_pagas_nilo_maia',                   'en_datapagamento'),   -- única data
        ('f_movimentacao_nilo_maia',            'Data'),
        ('f_645_nilo_maia',                     'Data'),
        ('f_transf_65apli_nilo_maia',           'Data'),
        ('f_transf_65dni_nilo_maia',            'Data'),
        ('f_transf_65sent_nilo_maia',           'Data'),
        ('f_transf_65vale_nilo_maia',           'Data')
       ) v (tabela, coluna_data)
 where c.id = f.cliente_id
   and c.slug = 'nilo-maia'
   and f.tabela_destino = v.tabela;

-- conferência: 12 tabelas com janela
do $$
begin
    if (select count(*) from cfg.fonte_carga f
          join cfg.cliente c on c.id = f.cliente_id
         where c.slug = 'nilo-maia' and f.meses_retidos is not null) <> 12 then
        raise exception 'esperava 12 tabelas com janela de datas para a Nilo Maia';
    end if;
end
$$;

commit;

-- Para ver:
--   select tabela_destino, coluna_data, meses_retidos, ativo
--     from cfg.fonte_carga order by tabela_destino;
