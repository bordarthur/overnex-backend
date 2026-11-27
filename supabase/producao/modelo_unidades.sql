-- Modelo de carga das unidades de um cliente a partir da planilha (produção).
--
-- NÃO é migration: copie, troque os valores e rode no SQL Editor do Supabase. Pode rodar
-- mais de uma vez: unidade que já existe (mesmo cliente e nome) tem setor, segmento, grupo e
-- endereço trocados pelos daqui. Depende da migration 35 (`supabase db push` antes).
--
-- Uma linha da planilha = uma linha do `values`:
--
--   Unidade   | Setor  | Segmento  | UF | Cidade | Bairro | Endereco | Cliente      | Grupo
--   Leao 1    | Varejo | Calçados  |    |        |        |          | Leao de Ouro | Leao
--
--   - cliente: o slug do cliente (cfg.cliente.slug, o da URL do front), não o nome;
--   - setor: o id de cfg.setor, não o rótulo: distribuidora, industria, varejo ou servicos
--     (null = não definido; o painel de um setor só aparece com unidade desse setor);
--   - grupo: o negócio dentro do setor (Leao, Lupo); null = "Sem grupo". Unidades com o
--     mesmo setor e grupo ficam juntas na bandeja "Negócios" do front, que só aparece com
--     mais de um par setor + grupo;
--   - uf com 2 letras maiúsculas; cidade, bairro e endereço opcionais (null).
--
-- A unidade nova entra sozinha nos vínculos que o cliente já tem (trigger da migration 30).
-- Para os dados dela chegarem às telas, o valor da coluna de empresa no DW precisa estar no
-- mapa (cfg.mapa_empresa): passo 2, no fim.

begin;

-- 1. unidades
insert into cfg.empresa (cliente_id, nome, setor, segmento, grupo, uf, cidade, bairro, endereco)
select c.id, v.nome, v.setor, v.segmento, v.grupo, v.uf, v.cidade, v.bairro, v.endereco
  from (values
    -- (cliente,         nome,        setor,    segmento,    grupo,     uf,   cidade, bairro, endereco)
    ('leao-de-ouro', 'Leao 1',    'varejo', 'Calçados',  'Leao',    null, null,   null,   null),
    ('leao-de-ouro', 'Leao 2',    'varejo', 'Calçados',  'Leao',    null, null,   null,   null),
    ('leao-de-ouro', 'Lupo 1',    'varejo', 'Vestuário', 'Lupo',    null, null,   null,   null),
    ('leao-de-ouro', 'Lupo 2',    'varejo', 'Vestuário', 'Lupo',    null, null,   null,   null),
    ('leao-de-ouro', 'Usaflex 1', 'varejo', 'Calçados',  'Usaflex', null, null,   null,   null),
    ('leao-de-ouro', 'Usaflex 2', 'varejo', 'Calçados',  'Usaflex', null, null,   null,   null)
  ) as v (cliente, nome, setor, segmento, grupo, uf, cidade, bairro, endereco)
  join cfg.cliente c on c.slug = v.cliente
on conflict (cliente_id, nome) do update
   set setor    = excluded.setor,
       segmento = excluded.segmento,
       grupo    = excluded.grupo,
       uf       = excluded.uf,
       cidade   = excluded.cidade,
       bairro   = excluded.bairro,
       endereco = excluded.endereco;

-- 2. mapa: valor da coluna de empresa no DW → unidade (um por valor; troque pelos do DW)
-- insert into cfg.mapa_empresa (cliente_id, empresa_origem, empresa_id)
-- select e.cliente_id, v.origem, e.id
--   from (values ('LEAO 1', 'Leao 1'), ('LEAO 2', 'Leao 2')) v (origem, nome)
--   join cfg.empresa e on e.nome = v.nome
--   join cfg.cliente c on c.id = e.cliente_id and c.slug = 'leao-de-ouro'
-- on conflict (cliente_id, empresa_origem) do update set empresa_id = excluded.empresa_id;

-- 3. conferência: as unidades do cliente na ordem da bandeja (setor, grupo, nome)
select e.nome, s.rotulo as setor, e.segmento, e.grupo, e.uf, e.cidade
  from cfg.empresa e
  join cfg.cliente c on c.id = e.cliente_id and c.slug = 'leao-de-ouro'
  left join cfg.setor s on s.id = e.setor
 order by s.ordem nulls last, e.grupo nulls last, e.nome;

commit;
