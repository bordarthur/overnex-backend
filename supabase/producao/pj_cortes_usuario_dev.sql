-- PJ Cortes — usuário de desenvolvimento para ler os dados reais pela API local (produção).
--
-- NÃO é migration: rode no SQL Editor do Supabase, DEPOIS de pj_cortes_cadastro.sql.
--
-- O usuário diretoria@lannconsult.com.br sai da Nilo Maia e passa para a PJ Cortes: a API
-- ainda nega quem tem vínculo com mais de um cliente (ver TODO.md, "Usuário com mais de um
-- cliente"). O perfil "Desenvolvimento" da Nilo Maia fica, sem usuários.
--
-- Mesmo token fake da API local: `Bearer fake:0d000000-0000-4000-8000-0000000001a1`
-- (AMBIENTE=dev e AUTH_FAKE=1). Se o usuário ainda não existe, ele é criado com esse id.
--
-- Depois da migration 15 (id_autenticacao). Para o login com e-mail e senha, troque o id
-- pelo do Supabase Auth com usuario_dev_supabase_auth.sql.

begin;

-- 1. perfil "Desenvolvimento": Visão geral, FIN-01 e todas as tabelas do cliente
insert into cfg.perfil_acesso (cliente_id, nome)
select c.id, 'Desenvolvimento'
  from cfg.cliente c
 where c.slug = 'pj-cortes'
on conflict do nothing;

insert into cfg.perfil_tela (perfil_id, codigo_tela, ve_dado_nominal)
select p.id, t.codigo, true
  from cfg.perfil_acesso p
  join cfg.cliente c on c.id = p.cliente_id and c.slug = 'pj-cortes'
 cross join (values ('VG-01'), ('FIN-01')) t (codigo)
 where p.nome = 'Desenvolvimento'
on conflict do nothing;

-- ve_dado_nominal = true: o perfil lê também as tabelas com dado nominal (clientes, históricos)
insert into cfg.perfil_tabela (perfil_id, tabela, ve_dado_nominal)
select p.id, f.tabela_destino, true
  from cfg.perfil_acesso p
  join cfg.cliente c on c.id = p.cliente_id and c.slug = 'pj-cortes'
  join cfg.fonte_carga f on f.cliente_id = c.id and f.tipo = 'tabela'
 where p.nome = 'Desenvolvimento'
on conflict do nothing;

-- 2. usuário: sai da Nilo Maia (perfis e empresas saem em cascata) e entra na PJ Cortes
insert into seg.usuario (id_autenticacao, email, tipo)
values ('0d000000-0000-4000-8000-0000000001a1', 'diretoria@lannconsult.com.br', 'lann')
on conflict do nothing;

delete from seg.usuario_cliente uc
 using seg.usuario u, cfg.cliente c
 where uc.usuario_id = u.id
   and uc.cliente_id = c.id
   and lower(u.email) = 'diretoria@lannconsult.com.br'
   and c.slug = 'nilo-maia';

insert into seg.usuario_cliente (usuario_id, cliente_id, papel)
select u.id, c.id, 'gestor'
  from seg.usuario u, cfg.cliente c
 where lower(u.email) = 'diretoria@lannconsult.com.br'
   and c.slug = 'pj-cortes'
on conflict do nothing;

insert into seg.usuario_perfil (usuario_id, cliente_id, perfil_id)
select u.id, p.cliente_id, p.id
  from seg.usuario u
  join cfg.perfil_acesso p on p.nome = 'Desenvolvimento'
  join cfg.cliente c on c.id = p.cliente_id and c.slug = 'pj-cortes'
 where lower(u.email) = 'diretoria@lannconsult.com.br'
on conflict do nothing;

insert into seg.usuario_empresa (usuario_id, cliente_id, empresa_id)
select u.id, e.cliente_id, e.id
  from seg.usuario u
  join cfg.empresa e on true
  join cfg.cliente c on c.id = e.cliente_id and c.slug = 'pj-cortes'
 where lower(u.email) = 'diretoria@lannconsult.com.br'
on conflict do nothing;

-- conferência: um cliente só (a PJ Cortes), com a VG-01 e a empresa
do $$
begin
    if (select count(*) from seg.usuario_cliente uc
          join seg.usuario u on u.id = uc.usuario_id
         where lower(u.email) = 'diretoria@lannconsult.com.br') <> 1
       or not exists (
        select 1
          from seg.usuario u
          join seg.usuario_cliente uc on uc.usuario_id = u.id
          join cfg.cliente c on c.id = uc.cliente_id and c.slug = 'pj-cortes'
          join seg.usuario_perfil up on up.usuario_id = u.id and up.cliente_id = c.id
          join cfg.perfil_tela pt on pt.perfil_id = up.perfil_id and pt.codigo_tela = 'VG-01'
          join seg.usuario_empresa ue on ue.usuario_id = u.id and ue.cliente_id = c.id
         where lower(u.email) = 'diretoria@lannconsult.com.br'
    ) then
        raise exception 'usuário de desenvolvimento da PJ Cortes incompleto';
    end if;
end
$$;

commit;
