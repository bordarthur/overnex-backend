-- PJ Cortes — regras da DRE próprias da PJ (produção), em cfg.parametro_cliente.
--
-- NÃO é migration: rode no SQL Editor depois de publicar a API que lê estes parâmetros
-- (antes, eles ficam parados). Pode rodar mais de uma vez: troca os valores.
--
-- 1. dre.inadimplencia: a DRE do Power BI da PJ deduz todo mês 0,40% da receita bruta como
--    "Inadimplência", uma provisão calculada no relatório, não um lançamento (conferido em
--    03/11/2026 contra o export da DRE realizada de 2026: 0,40% exatos de jan a nov). A API
--    faz o mesmo na Visão geral e no painel Resultado: taxa × receita bruta do mês na conta
--    110.01.02 (Provisão para Devedores Duvidosos, nas deduções), para onde o de-para da PJ
--    manda a conta "Inadimplência" (320.02.07).
--
-- 2. dre.contas_fora: contas que a DRE da PJ não mostra (nem no realizado nem no orçado).
--    120.03.22 Premiações (C) — PJ 330.02.45 "Premiação (Custo)", no custo de pessoal: fora
--    a pedido do usuário em 03/11/2026 (o Power BI também não a mostra; R$ 784 mil em 2026).
--    Para tirar outra conta, acrescente o Id_Descritivo do plano padrão na lista (um código
--    de nível 2, como "180.01", tira tudo dentro dele).
--
-- Para mudar um valor a partir de um mês, insira outra linha com vigente_de nesse mês: vale
-- a de vigente_de mais recente até o mês. As telas guardam a resposta por até 15 min.

begin;

insert into cfg.parametro_cliente (cliente_id, chave, valor, vigente_de)
select c.id, p.chave, p.valor, date '2020-01-01'
  from cfg.cliente c
 cross join (values
        ('dre.inadimplencia', '{"taxa": 0.4}'::jsonb),
        ('dre.contas_fora',   '{"contas": ["120.03.22"]}'::jsonb)
       ) p (chave, valor)
 where c.slug = 'pj-cortes'
on conflict (cliente_id, chave, vigente_de) do update set valor = excluded.valor;

commit;
