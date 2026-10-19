-- Nilo Maia — usuário de desenvolvimento para ler os dados reais pela API local (produção).
--
-- NÃO é migration: rode uma vez no SQL Editor do Supabase. Não muda dado de cliente: só
-- cria um perfil, um usuário e os vínculos dele com a Nilo Maia.
--
-- O object_id_entra abaixo é provisório: serve para o token fake da API LOCAL
-- (`Bearer fake:0d000000-0000-4000-8000-0000000001a1`, com AMBIENTE=dev e AUTH_FAKE=1).
-- Quando o login com o Entra entrar, troque pelo object id real do usuário:
--   update seg.usuario set object_id_entra = '<oid do Entra>' where lower(email) = 'diretoria@lannconsult.com.br';

begin;

-- 1. perfil "Desenvolvimento": Visão geral, FIN-01 e todas as tabelas do cliente
insert into cfg.perfil_acesso (cliente_id, nome)
select c.id, 'Desenvolvimento'
  from cfg.cliente c
 where c.slug = 'nilo-maia'
on conflict do nothing;

insert into cfg.perfil_tela (perfil_id, codigo_tela, ve_dado_nominal)
select p.id, t.codigo, true
  from cfg.perfil_acesso p
  join cfg.cliente c on c.id = p.cliente_id and c.slug = 'nilo-maia'
 cross join (values ('VG-01'), ('FIN-01')) t (codigo)
 where p.nome = 'Desenvolvimento'
on conflict do nothing;

-- ve_dado_nominal = true: o perfil lê também as tabelas com dado nominal (clientes, históricos)
insert into cfg.perfil_tabela (perfil_id, tabela, ve_dado_nominal)
select p.id, f.tabela_destino, true
  from cfg.perfil_acesso p
  join cfg.cliente c on c.id = p.cliente_id and c.slug = 'nilo-maia'
  join cfg.fonte_carga f on f.cliente_id = c.id and f.tipo = 'tabela'
 where p.nome = 'Desenvolvimento'
on conflict do nothing;

-- 2. usuário e vínculos (todas as empresas da Nilo Maia)
insert into seg.usuario (object_id_entra, email, tipo)
values ('0d000000-0000-4000-8000-0000000001a1', 'diretoria@lannconsult.com.br', 'lann')
on conflict do nothing;

insert into seg.usuario_cliente (usuario_id, cliente_id, papel)
select u.id, c.id, 'gestor'
  from seg.usuario u, cfg.cliente c
 where lower(u.email) = 'diretoria@lannconsult.com.br'
   and c.slug = 'nilo-maia'
on conflict do nothing;

insert into seg.usuario_perfil (usuario_id, cliente_id, perfil_id)
select u.id, p.cliente_id, p.id
  from seg.usuario u
  join cfg.perfil_acesso p on p.nome = 'Desenvolvimento'
  join cfg.cliente c on c.id = p.cliente_id and c.slug = 'nilo-maia'
 where lower(u.email) = 'diretoria@lannconsult.com.br'
on conflict do nothing;

insert into seg.usuario_empresa (usuario_id, cliente_id, empresa_id)
select u.id, e.cliente_id, e.id
  from seg.usuario u
  join cfg.empresa e on true
  join cfg.cliente c on c.id = e.cliente_id and c.slug = 'nilo-maia'
 where lower(u.email) = 'diretoria@lannconsult.com.br'
on conflict do nothing;

-- conferência: o usuário vê a Nilo Maia, a VG-01 e ao menos uma empresa
do $$
begin
    if not exists (
        select 1
          from seg.usuario u
          join seg.usuario_cliente uc on uc.usuario_id = u.id
          join cfg.cliente c on c.id = uc.cliente_id and c.slug = 'nilo-maia'
          join seg.usuario_perfil up on up.usuario_id = u.id and up.cliente_id = c.id
          join cfg.perfil_tela pt on pt.perfil_id = up.perfil_id and pt.codigo_tela = 'VG-01'
          join seg.usuario_empresa ue on ue.usuario_id = u.id and ue.cliente_id = c.id
         where lower(u.email) = 'diretoria@lannconsult.com.br'
    ) then
        raise exception 'usuário de desenvolvimento da Nilo Maia incompleto';
    end if;
end
$$;

commit;

-- Para ver:
--   select u.email, uc.papel, e.nome as empresa
--     from seg.usuario u
--     join seg.usuario_cliente uc on uc.usuario_id = u.id
--     join seg.usuario_empresa ue on ue.usuario_id = u.id
--     join cfg.empresa e on e.id = ue.empresa_id
--    where lower(u.email) = 'diretoria@lannconsult.com.br';
