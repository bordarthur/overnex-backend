-- PJ Cortes — painéis financeiros (Resultado e Receber e pagar) (produção).
--
-- NÃO é migration: rode no SQL Editor do Supabase. Pode rodar mais de uma vez.
--
-- ORDEM: a API com o código das telas FIN-02 e FIN-03 publicada ANTES (sem ela, a tela
-- aparece em /api/sessao, mas a rota não existe). Não depende de migration nova.
--
-- Telas FIN-02 (Resultado) e FIN-03 (Receber e pagar) no perfil "Desenvolvimento" (o do
-- usuário de desenvolvimento). A FIN-03 mostra nomes de clientes, fornecedores e
-- vendedores: só abre para perfil com ve_dado_nominal.

begin;

insert into cfg.perfil_tela (perfil_id, codigo_tela, ve_dado_nominal)
select p.id, t.codigo, true
  from cfg.perfil_acesso p
  join cfg.cliente c on c.id = p.cliente_id and c.slug = 'pj-cortes'
 cross join (values ('FIN-02'), ('FIN-03')) t (codigo)
 where p.nome = 'Desenvolvimento'
on conflict do nothing;

-- conferência
do $$
begin
    if (select count(*)
          from cfg.perfil_tela pt
          join cfg.perfil_acesso p on p.id = pt.perfil_id
          join cfg.cliente c on c.id = p.cliente_id and c.slug = 'pj-cortes'
         where p.nome = 'Desenvolvimento'
           and pt.codigo_tela in ('FIN-02', 'FIN-03')
           and pt.ve_dado_nominal) < 2 then
        raise exception 'esperava FIN-02 e FIN-03 (com dado nominal) no perfil Desenvolvimento da PJ Cortes';
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
