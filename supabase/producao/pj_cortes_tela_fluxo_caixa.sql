-- PJ Cortes — painel Fluxo de caixa (FIN-04) (produção).
--
-- NÃO é migration: rode no SQL Editor do Supabase. Pode rodar mais de uma vez.
--
-- ORDEM: a API com o código da tela FIN-04 publicada ANTES (sem ela, a tela aparece em
-- /api/sessao, mas a rota não existe). Para a tela ter números: migrations 22 e 23 e
-- pj_cortes_fluxo_caixa.sql (saldo inicial e fonte f_fluxo_pj) e uma carga. Sem eles, a
-- tela abre vazia.
--
-- FIN-04 no perfil "Desenvolvimento" (o do usuário de desenvolvimento). Sem dado nominal: a
-- tela mostra contas do plano e códigos de conta do ERP, não nomes de pessoas.

begin;

insert into cfg.perfil_tela (perfil_id, codigo_tela, ve_dado_nominal)
select p.id, 'FIN-04', false
  from cfg.perfil_acesso p
  join cfg.cliente c on c.id = p.cliente_id and c.slug = 'pj-cortes'
 where p.nome = 'Desenvolvimento'
on conflict do nothing;

-- conferência
do $$
begin
    if not exists (select 1
                     from cfg.perfil_tela pt
                     join cfg.perfil_acesso p on p.id = pt.perfil_id
                     join cfg.cliente c on c.id = p.cliente_id and c.slug = 'pj-cortes'
                    where p.nome = 'Desenvolvimento'
                      and pt.codigo_tela = 'FIN-04') then
        raise exception 'esperava FIN-04 no perfil Desenvolvimento da PJ Cortes';
    end if;
end
$$;

commit;

-- Para ver:
--   select p.nome, pt.codigo_tela, pt.ve_dado_nominal
--     from cfg.perfil_tela pt
--     join cfg.perfil_acesso p on p.id = pt.perfil_id
--     join cfg.cliente c on c.id = p.cliente_id
--    where c.slug = 'pj-cortes'
--    order by 1, 2;
