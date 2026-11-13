-- PJ Cortes — painéis comerciais (Vendas, Equipe comercial, Clientes e atendimentos) (produção).
--
-- NÃO é migration: rode no SQL Editor do Supabase. Pode rodar mais de uma vez.
--
-- ORDEM: a carga (jobs/carga) e a API com o código dos painéis comerciais em `main` ANTES
-- deste script. A carga antiga recusa os papéis novos em colunas_modelo ("papéis
-- desconhecidos"): a tabela de vendas carrega, mas o resumo mensal das vendas para de ser
-- atualizado (aviso em mart.carga_fonte.mensagem). Depois do script, rode o diario.yml: a
-- carga passa a gravar o faturado por vendedor no resumo mensal (faturado_vendedor), de que a
-- rampa dos vendedores novos precisa (o histórico inteiro, não só a janela de 13 meses).
--
-- 1. telas COM-01, COM-02 e COM-03 no perfil "Desenvolvimento" (o que existe hoje). A
--    COM-02 mostra nomes de vendedores: só abre para perfil com ve_dado_nominal.
-- 2. papéis novos nas duas tabelas de vendas: vendedor (en_codigovendedor), linha de
--    produto (it_grupo) e quantidade (en_quantidade). A API já acha essas colunas sozinha
--    (padrão dos painéis, desde 26/10/2026); o papel em colunas_modelo só faz a CARGA gravar
--    o faturado por vendedor no resumo mensal, de que a rampa dos vendedores novos precisa.

begin;

-- 1. telas
insert into cfg.perfil_tela (perfil_id, codigo_tela, ve_dado_nominal)
select p.id, t.codigo, true
  from cfg.perfil_acesso p
  join cfg.cliente c on c.id = p.cliente_id and c.slug = 'pj-cortes'
 cross join (values ('COM-01'), ('COM-02'), ('COM-03')) t (codigo)
 where p.nome = 'Desenvolvimento'
on conflict do nothing;

-- 2. papéis das vendas
update cfg.fonte_carga f
   set colunas_modelo = f.colunas_modelo
       || '{"vendedor": "en_codigovendedor", "linha": "it_grupo", "quantidade": "en_quantidade"}'::jsonb
  from cfg.cliente c
 where c.id = f.cliente_id
   and c.slug = 'pj-cortes'
   and f.tabela_destino in ('f_vendas_pj', 'f_vendas_agile_pj_cortes');

-- conferência
do $$
begin
    if (select count(*)
          from cfg.perfil_tela pt
          join cfg.perfil_acesso p on p.id = pt.perfil_id
          join cfg.cliente c on c.id = p.cliente_id and c.slug = 'pj-cortes'
         where pt.codigo_tela in ('COM-01', 'COM-02', 'COM-03')) < 3 then
        raise exception 'esperava as 3 telas comerciais no perfil Desenvolvimento da PJ Cortes';
    end if;
    if (select count(*)
          from cfg.fonte_carga f
          join cfg.cliente c on c.id = f.cliente_id and c.slug = 'pj-cortes'
         where f.colunas_modelo ? 'vendedor') <> 2 then
        raise exception 'esperava o papel vendedor nas 2 tabelas de vendas da PJ Cortes';
    end if;
end
$$;

commit;

-- Para ver:
--   select f.tabela_destino, f.colunas_modelo
--     from cfg.fonte_carga f join cfg.cliente c on c.id = f.cliente_id
--    where c.slug = 'pj-cortes' and f.tabela_destino like 'f_vendas_%';
--
-- Depois da carga, o faturado por vendedor:
--   select tabela, min(mes), max(mes), count(distinct dimensao) as vendedores
--     from mart.resumo_mensal r join cfg.cliente c on c.id = r.cliente_id
--    where c.slug = 'pj-cortes' and medida = 'faturado_vendedor'
--    group by tabela;
