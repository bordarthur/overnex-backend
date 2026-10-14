-- Nilo Maia — fontes das tabelas de dados (produção).
--
-- NÃO é migration: rode uma vez no SQL Editor do Supabase, DEPOIS de aplicar a
-- migration 5 (supabase db push) e do cadastro do cliente/empresas/mapa.
--
-- 1. A fonte de lançamentos (hoje tipo 'lancamento', que espera o formato
--    Empresa/Data/Id_Descritivo/Valor) passa a ser tipo 'tabela': o arquivo entra como
--    está em mart.f_lancamentos_agrupados_nilo_maia.
-- 2. As outras 20 tabelas do bucket ganham uma fonte cada, INATIVAS. Ative depois de
--    confirmar o espaço do banco (ver o fim do arquivo).
--
-- coluna_empresa: coluna do arquivo com a empresa (vira _empresa_id e o RLS separa por
-- empresa). null = a tabela não tem empresa: todo usuário do cliente vê todas as linhas.
-- contem_dado_nominal: true = só perfis com ve_dado_nominal em cfg.perfil_tabela leem.

begin;

-- 1. lançamentos agrupados: de 'lancamento' para 'tabela'
update cfg.fonte_carga f
   set tipo = 'tabela',
       tabela_destino = 'f_lancamentos_agrupados_nilo_maia',
       coluna_empresa = 'EMPRESA',
       contem_dado_nominal = true
  from cfg.cliente c
 where c.id = f.cliente_id
   and c.slug = 'nilo-maia'
   and f.caminho_bucket =
       'dataflow/dbo_f_lancamentos_agrupados_nilo_maia/dbo_f_lancamentos_agrupados_nilo_maia.parquet';

-- 2. demais tabelas (inativas)
insert into cfg.fonte_carga
    (cliente_id, tipo, caminho_bucket, tabela_destino, coluna_empresa, contem_dado_nominal, ativo)
select c.id,
       'tabela',
       'dataflow/dbo_' || v.tabela || '/dbo_' || v.tabela || '.parquet',
       v.tabela,
       v.coluna_empresa,
       v.nominal,
       false
  from cfg.cliente c
 cross join (values
        -- tabela                          coluna_empresa  nominal
        ('f_645_nilo_maia',                 null::text,     true),   -- Historico
        ('f_balanco_nilo_maia',             null,           false),
        ('f_compras_nilo_maia',             null,           true),   -- NOMEFUNC, en_historico
        ('f_contas_a_pagar_nilo_maia',      null,           false),
        ('f_contas_a_receber_nilo_maia',    null,           false),
        ('f_estoque_nilo_maia',             'EMPRESA',      false),
        ('f_inadimplencia_nilo_maia',       'EMPRESA',      true),   -- en_codigocliente
        ('f_metas_nilo_maia',               null,           false),  -- Empresa = 'Nilo Maia'
        ('f_movimentacao_nilo_maia',        'Empresa',      true),   -- Descricao
        ('f_orcamento_nilo_maia',           null,           false),
        ('f_pagas_nilo_maia',               'EMPRESA',      true),   -- en_observacao
        ('f_pedidos_nilo_maia',             null,           false),
        ('f_recebidas_nilo_maia',           'EMPRESA',      true),   -- en_observacao
        ('f_receitas_nilo_maia',            null,           true),   -- en_codcli
        ('f_saldos_bancarios_nilo_maia',    null,           false),
        ('f_transf_65apli_nilo_maia',       null,           true),   -- Historico
        ('f_transf_65dni_nilo_maia',        null,           true),
        ('f_transf_65sent_nilo_maia',       null,           true),
        ('f_transf_65vale_nilo_maia',       null,           true),
        ('f_vendas_nilo_maia',              null,           true)    -- en_nome, en_codcli
       ) v (tabela, coluna_empresa, nominal)
 where c.slug = 'nilo-maia'
on conflict (cliente_id, tipo, caminho_bucket) do nothing;

-- conferência: 21 fontes do tipo tabela para a Nilo Maia
do $$
begin
    if (select count(*) from cfg.fonte_carga f
          join cfg.cliente c on c.id = f.cliente_id
         where c.slug = 'nilo-maia' and f.tipo = 'tabela') <> 21 then
        raise exception 'esperava 21 fontes do tipo tabela para a Nilo Maia';
    end if;
end
$$;

commit;

-- Para ver:
--   select id, tabela_destino, coluna_empresa, contem_dado_nominal, ativo
--     from cfg.fonte_carga order by tabela_destino;
--
-- Para ativar (depois de confirmar o espaço do banco; ~7 milhões de linhas no total):
--   update cfg.fonte_carga set ativo = true
--    where tipo = 'tabela' and tabela_destino in ('f_estoque_nilo_maia', ...);
