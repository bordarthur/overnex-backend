-- PJ Cortes — painel Pessoas (PES-01) (produção).
--
-- NÃO é migration: rode no SQL Editor do Supabase. Não depende de migration nova. Pode rodar
-- mais de uma vez.
--
-- Parte 1 (a qualquer hora): tira a janela de 3 meses de f_folha_pj_cortes. O painel precisa
-- do histórico inteiro: admissão é o primeiro mês do nome na folha, desligamento o mês
-- depois do último, e o tempo de casa conta desde o primeiro mês. Com 3 meses, o turnover
-- sai de 2 meses e o tempo de casa não sai. O arquivo inteiro tem ~26 mil linhas (jan/2024
-- em diante). Depois, rode a carga (workflow diario.yml).
--
-- Parte 2 (ORDEM: a API com o código da tela PES-01 publicada ANTES; sem ela, a tela aparece
-- em /api/sessao, mas a rota não existe): PES-01 no perfil "Desenvolvimento" (o do usuário de
-- desenvolvimento). O painel é só da diretoria e do RH: quando esses perfis existirem, libere
-- a tela neles e em nenhum outro. ve_dado_nominal fica false: o painel não mostra nomes (o
-- salário por nome, que será só do RH, fica para outra versão).

begin;

-- parte 1
update cfg.fonte_carga f
   set coluna_data = null,
       meses_retidos = null
  from cfg.cliente c
 where c.id = f.cliente_id
   and c.slug = 'pj-cortes'
   and f.tabela_destino = 'f_folha_pj_cortes';

-- parte 2
insert into cfg.perfil_tela (perfil_id, codigo_tela, ve_dado_nominal)
select p.id, 'PES-01', false
  from cfg.perfil_acesso p
  join cfg.cliente c on c.id = p.cliente_id and c.slug = 'pj-cortes'
 where p.nome = 'Desenvolvimento'
on conflict do nothing;

-- conferência
do $$
begin
    if exists (select 1
                 from cfg.fonte_carga f
                 join cfg.cliente c on c.id = f.cliente_id and c.slug = 'pj-cortes'
                where f.tabela_destino = 'f_folha_pj_cortes'
                  and (f.coluna_data is not null or f.meses_retidos is not null)) then
        raise exception 'f_folha_pj_cortes da PJ Cortes ainda tem janela de datas';
    end if;
    if not exists (select 1
                     from cfg.perfil_tela pt
                     join cfg.perfil_acesso p on p.id = pt.perfil_id
                     join cfg.cliente c on c.id = p.cliente_id and c.slug = 'pj-cortes'
                    where p.nome = 'Desenvolvimento'
                      and pt.codigo_tela = 'PES-01') then
        raise exception 'esperava PES-01 no perfil Desenvolvimento da PJ Cortes';
    end if;
end
$$;

commit;

-- Para ver:
--   select f.tabela_destino, f.coluna_data, f.meses_retidos
--     from cfg.fonte_carga f join cfg.cliente c on c.id = f.cliente_id
--    where c.slug = 'pj-cortes' and f.tabela_destino = 'f_folha_pj_cortes';
--   select p.nome, pt.codigo_tela, pt.ve_dado_nominal
--     from cfg.perfil_tela pt
--     join cfg.perfil_acesso p on p.id = pt.perfil_id
--     join cfg.cliente c on c.id = p.cliente_id
--    where c.slug = 'pj-cortes' and pt.codigo_tela = 'PES-01';
