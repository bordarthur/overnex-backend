-- Migration 19 — a API grava as escolhas do cliente em cfg.parametro_cliente.
--
-- Por quê: a Visão geral passou a ter escolhas que ficam salvas para o cliente inteiro (a
-- primeira: quantas linhas de custo viram velocímetro na seção Custos, gravada por
-- PUT /api/telas/visao-geral/preferencias/{id}). Elas vão em cfg.parametro_cliente com
-- chave 'preferencia.<id>' e vigente_de = dia da escolha: a mais recente vale e as antigas
-- ficam como histórico. Cada troca vai também para seg.log_alteracao (o api_site já insere
-- lá, migration 3).
--
-- O api_site passa a inserir e a trocar o valor só dessas chaves e só do próprio cliente;
-- farol e os outros parâmetros continuam só da LANN. Quem pode mudar (o papel) é decidido
-- na API. A API funciona sem esta migration, mas a gravação responde 500 até ela entrar.

grant insert, update (valor) on cfg.parametro_cliente to api_site;

create policy api_site_insert_preferencia on cfg.parametro_cliente
    for insert to api_site
    with check (
        chave like 'preferencia.%'
        and cliente_id in (select seg.clientes_do_usuario())
    );

create policy api_site_update_preferencia on cfg.parametro_cliente
    for update to api_site
    using (
        chave like 'preferencia.%'
        and cliente_id in (select seg.clientes_do_usuario())
    )
    with check (
        chave like 'preferencia.%'
        and cliente_id in (select seg.clientes_do_usuario())
    );
