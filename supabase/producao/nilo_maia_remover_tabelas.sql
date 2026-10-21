-- Nilo Maia — remove as tabelas de dados do mart para liberar espaço (produção).
--
-- NÃO é migration: rode no SQL Editor do Supabase. Não tem volta: os dados voltam só
-- recriando as tabelas (migration 5 / inspecionar) e rodando a carga de novo.
--
-- Remove, só da Nilo Maia:
--   - as tabelas mart.*_nilo_maia (o espaço é liberado no commit);
--   - as fontes do tipo 'tabela' em cfg.fonte_carga e o histórico delas em mart.carga_fonte;
--   - os totais em mart.resumo_mensal e as permissões em cfg.perfil_tabela.
-- Mantém o cliente, as empresas, o mapa de empresas, os usuários e os perfis.

begin;

-- tabelas a remover: destinos das fontes do cliente
create temp table remover on commit drop as
select f.id as fonte_id, f.tabela_destino as tabela
  from cfg.fonte_carga f
  join cfg.cliente c on c.id = f.cliente_id
 where c.slug = 'nilo-maia'
   and f.tipo = 'tabela';

-- trava: só nomes que terminam em _nilo_maia
do $$
begin
    if not exists (select 1 from remover) then
        raise exception 'nenhuma fonte do tipo tabela para a Nilo Maia';
    end if;
    if exists (select 1 from remover where tabela !~ '_nilo_maia$') then
        raise exception 'fonte com tabela fora do padrão *_nilo_maia: %',
            (select string_agg(tabela, ', ') from remover where tabela !~ '_nilo_maia$');
    end if;
end
$$;

delete from mart.carga_fonte cf
 using remover r
 where cf.fonte_id = r.fonte_id;

delete from mart.resumo_mensal rm
 using cfg.cliente c
 where c.id = rm.cliente_id
   and c.slug = 'nilo-maia';

delete from cfg.perfil_tabela pt
 using remover r
 where pt.tabela = r.tabela;

do $$
declare
    t text;
begin
    for t in select tabela from remover order by tabela loop
        execute format('drop table if exists mart.%I', t);
        raise notice 'removida: mart.%', t;
    end loop;
end
$$;

delete from cfg.fonte_carga f
 using remover r
 where f.id = r.fonte_id;

-- conferência: nenhuma tabela da Nilo Maia sobrou no mart
do $$
begin
    if exists (select 1 from pg_tables
                where schemaname = 'mart' and tablename like '%\_nilo\_maia') then
        raise exception 'ainda há tabelas *_nilo_maia no mart';
    end if;
end
$$;

commit;

-- Para ver o tamanho do banco depois:
-- select pg_size_pretty(pg_database_size(current_database())) as banco;
