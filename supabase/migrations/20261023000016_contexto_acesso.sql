-- Migration 16 — checagem de acesso numa ida ao banco só.
--
-- Por quê: a API fazia uma consulta por etapa da checagem (traduzir o usuário, definir
-- app.usuario_id, vínculos, perfis da tela, empresas), e cada uma custa a latência da rede
-- até o banco (~120 ms do Brasil até us-east-1). seg.contexto_acesso faz tudo isso numa
-- chamada e devolve os fatos; as decisões das 6 etapas continuam na API
-- (app/seguranca/acesso.py), como antes.
--
-- SECURITY INVOKER de propósito: roda como api_site, sob as mesmas políticas de RLS das
-- consultas que substitui. Só seg.resolver_usuario (SECURITY DEFINER, migration 15) enxerga
-- seg.usuario antes de existir app.usuario_id.
--
-- Efeito colateral: define app.usuario_id na transação (set_config local), como a API
-- fazia antes de checar o acesso. Usuário inexistente ou inativo: app.usuario_id vazio e
-- listas vazias.

create function seg.contexto_acesso(p_id_autenticacao uuid, p_codigo_tela text default null)
returns table (
    usuario_id  uuid,
    ativo       boolean,
    -- [{"cliente_id", "papel"}] dos vínculos com cliente ativo
    vinculos    jsonb,
    -- {"<cliente_id>": ve_dado_nominal} dos clientes em que algum perfil libera p_codigo_tela
    perfis_tela jsonb,
    -- [{"cliente_id", "empresa_id"}] liberadas ao usuário, na ordem nome, id da empresa
    empresas    jsonb
)
language plpgsql
volatile
security invoker
set search_path = ''
as $$
#variable_conflict use_column
declare
    v_id    uuid;
    v_ativo boolean;
begin
    select r.id, r.ativo into v_id, v_ativo
      from seg.resolver_usuario(p_id_autenticacao) r;

    perform set_config('app.usuario_id', coalesce(v_id::text, ''), true);

    if v_id is null or not v_ativo then
        return query select v_id, coalesce(v_ativo, false), '[]'::jsonb, '{}'::jsonb, '[]'::jsonb;
        return;
    end if;

    return query
    select
        v_id,
        true,
        coalesce((
            select jsonb_agg(
                       jsonb_build_object('cliente_id', uc.cliente_id, 'papel', uc.papel)
                       order by uc.cliente_id)
              from seg.usuario_cliente uc
              join cfg.cliente c on c.id = uc.cliente_id and c.ativo
             where uc.usuario_id = v_id
        ), '[]'::jsonb),
        coalesce((
            select jsonb_object_agg(x.cliente_id::text, x.ve_dado_nominal)
              from (
                    select up.cliente_id, bool_or(pt.ve_dado_nominal) as ve_dado_nominal
                      from seg.usuario_perfil up
                      join cfg.perfil_tela pt on pt.perfil_id = up.perfil_id
                     where up.usuario_id = v_id
                       and pt.codigo_tela = p_codigo_tela
                     group by up.cliente_id
                   ) x
        ), '{}'::jsonb),
        coalesce((
            select jsonb_agg(
                       jsonb_build_object('cliente_id', ue.cliente_id, 'empresa_id', ue.empresa_id)
                       order by e.nome, e.id)
              from seg.usuario_empresa ue
              join cfg.empresa e on e.id = ue.empresa_id and e.cliente_id = ue.cliente_id
             where ue.usuario_id = v_id
        ), '[]'::jsonb);
end
$$;

comment on function seg.contexto_acesso(uuid, text) is
    'Define app.usuario_id a partir do id do provedor de login e devolve vínculos, perfis '
    'que liberam a tela e empresas do usuário, numa chamada (checagem de acesso da API).';

revoke all on function seg.contexto_acesso(uuid, text) from public;
grant execute on function seg.contexto_acesso(uuid, text) to api_site;
