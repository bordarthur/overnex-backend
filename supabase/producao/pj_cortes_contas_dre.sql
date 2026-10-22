-- PJ Cortes — de-para da conta do Winthor para o plano gerencial da DRE (produção).
--
-- NÃO é migration: rode no SQL Editor DEPOIS da migration 14 (cfg.conta_dre). Pode rodar
-- mais de uma vez: troca todas as contas da PJ Cortes.
--
-- Gerado de dois arquivos do Power BI da PJ: Codigo_Sistema_x_Id_Descritivo (conta do
-- Winthor → Id_Descritivo antigo, 310.xx…) e Plano_de_Conta (antigo → novo, coluna
-- ID_Mãe: 100.xx…). O comentário de cada linha é o Id_Descritivo antigo. Só as contas do
-- Winthor numéricas (9.99.xx e 2.01.xx não aparecem em f_despesas_pj).
--
-- Ajustes:
-- - 401047: o id antigo 340.04.56 não está no plano de contas; vai para o mesmo id novo
--   de 340.04.02 (mesmo descritivo, "Combustível e Lubrificante");
-- - 402017 aparece em dois ids antigos (330.02.44 e 330.02.45): fica o primeiro;
-- - fora_da_dre: as exclusões conhecidas da DRE do Power BI (compra de mercadoria, ICMS,
--   IRPJ/CSLL, comissões 201014 e o pessoal 350.01, que lá entra pela folha gerencial).
--   A lista de "diversos IDs 320/330/340/350/400" excluídos no Power BI ainda não veio:
--   quando vier, preencha fora_da_dre nessas contas.
-- Avisos da geração: conta 402017 em dois ids: 330.02.44→120.03.02 e 330.02.45→120.03.22 (fica o primeiro); conta 401047: id antigo 340.04.56 fora do plano de contas.

begin;

delete from cfg.conta_dre d
 using cfg.cliente c
 where c.id = d.cliente_id and c.slug = 'pj-cortes';

insert into cfg.conta_dre (cliente_id, conta, id_descritivo, descricao, fora_da_dre)
select c.id, v.conta, v.id_descritivo, v.descricao, v.fora_da_dre
  from cfg.cliente c
 cross join (values
        -- conta     id_descritivo  descrição  fora_da_dre
        ('102', '120.01.01', 'Custo da Mercadoria Vendida (Caixa)', 'CMV (caixa): o CMV entra pelo custo das vendas'),  -- 330.05.05
        ('4043', '420.02.07', 'Imobilizados', null),  -- 810.02.04
        ('100001', '120.01.01', 'Custo da Mercadoria Vendida', 'compra de mercadoria: o CMV entra pelo custo das vendas'),  -- 330.01.01
        ('100002', '100.02.03', 'Fretes Retorno', null),  -- 310.03.01
        ('100003', '120.01.52', 'Outros Custos de Revenda', null),  -- 330.05.12
        ('100010', '130.01.01', 'Comissões de Vendedores', null),  -- 340.03.10
        ('100011', '110.03.01', 'PIS', null),  -- 320.01.01
        ('100012', '110.03.02', 'COFINS', null),  -- 320.01.02
        ('100013', '170.01.08', 'CPMF', null),  -- 380.01.32
        ('100014', '200.01.01', 'IRPJ', 'IRPJ: vai para rateio'),  -- 400.01.01
        ('100015', '200.01.01', 'CSSL (SE ESTIVER NO CMV)', 'CSLL: vai para rateio'),  -- 400.02.06
        ('100016', '110.03.04', 'ICMS (Contábil)', null),  -- 320.01.09
        ('100017', '120.01.52', 'Substituição Tributária na Venda', null),  -- 320.01.11
        ('100020', '100.01.17', 'Receita Devolução de Mercadoria', null),  -- 310.02.02
        ('100021', '120.01.01', 'Ressarcimento de Fornecedor', null),  -- 330.05.07
        ('100022', '140.02.08', 'Material de Consumo Interno', null),  -- 350.02.87
        ('100023', '190.01.06', 'Recebimento de Devolução de Fornecedores', null),  -- 391.01.11
        ('100024', '170.01.09', 'Variação Cambial', null),  -- 380.01.30
        ('100025', '100.02.03', 'Fretes Retorno Receita', null),  -- 310.03.03
        ('100026', '120.04.04', 'Pagamento de Fretes', null),  -- 330.03.01
        ('200001', '140.01.01', 'Salário Mensal', 'pessoal (350.01): entra pela folha gerencial'),  -- 350.01.01
        ('200002', '140.01.02', '13° Salário', 'pessoal (350.01): entra pela folha gerencial'),  -- 350.01.03
        ('200003', '110.01.01', 'Desconto Concedido', null),  -- 320.02.01
        ('200004', '140.01.03', 'Férias - Despesa', 'pessoal (350.01): entra pela folha gerencial'),  -- 350.01.13
        ('200005', '140.01.11', 'Rescisão Trabalhista', 'pessoal (350.01): entra pela folha gerencial'),  -- 350.01.24
        ('200006', '130.03.02', 'Credito Adiantamento de Viagem', null),  -- 340.04.31
        ('200007', '140.02.04', 'Telefonia', null),  -- 350.02.28
        ('200008', '140.02.04', 'Telefone Vendas', null),  -- 350.02.102
        ('200009', '140.02.04', 'Telefone Fax', null),  -- 350.02.100
        ('200011', '140.01.07', 'Cesta Básica e de Alimentação (DP)', null),  -- 350.06.05
        ('200014', '140.06.06', 'Serviço Geral', null),  -- 350.02.22
        ('200015', '120.03.13', 'Treinamento e Curso (Custo)', null),  -- 330.02.09
        ('200016', '140.06.01', 'Assessoria Jurídica', null),  -- 350.02.73
        ('200018', '140.02.08', 'Feiras', null),  -- 350.02.108
        ('200019', '140.03.03', 'Viagens', null),  -- 350.04.06
        ('200020', '140.04.01', 'Marketing e Propaganda', null),  -- 350.05.04
        ('200021', '130.03.09', 'Despacho', null),  -- 340.03.15
        ('200022', '140.02.13', 'Manutenção de instalações', null),  -- 350.02.48
        ('200025', '140.01.07', 'Auxilio Alimentação', null),  -- 350.06.09
        ('201002', '120.03.11', 'Rescisão Trabalhista (Custo)', null),  -- 330.02.28
        ('201003', '140.02.08', 'Material de Consumo - Água', null),  -- 350.02.40
        ('201004', '140.02.05', 'Locação de Impressora', null),  -- 350.02.16
        ('201005', '140.02.15', 'Correios e Malote', null),  -- 350.02.10
        ('201006', '140.02.04', 'Telefone Escritório', null),  -- 350.02.101
        ('201007', '120.03.01', 'Salário CLT Fora (Custo)', null),  -- 330.02.35
        ('201008', '120.03.03', 'Férias', null),  -- 330.02.43
        ('201009', '140.01.02', '13° Salário - Contábil', null),  -- 350.06.44
        ('201010', '120.03.08', 'Auxilio Transporte (Custo)', null),  -- 330.02.11
        ('201011', '140.02.06', 'Material de Limpeza', null),  -- 350.02.18
        ('201013', '140.02.02', 'Energia', null),  -- 350.02.95
        ('201014', '130.01.01', 'Comissão de Venda', 'comissões: fora da DRE do Power BI'),  -- 340.01.01
        ('250001', '130.01.04', 'VB Campanhas / Prom / Incentivos', null),  -- 350.05.29
        ('250002', '180.01.06', 'VB Avarias e Devoluções', null),  -- 390.02.14
        ('250003', '180.01.02', 'VB Inauguração', null),  -- 390.02.15
        ('250005', '180.01.02', 'VB Aniversário', null),  -- 390.02.16
        ('250006', '130.01.01', 'Comissão Adicional', null),  -- 340.01.06
        ('250007', '140.03.03', 'Viagens e Representações', null),  -- 350.04.05
        ('250008', '180.01.02', 'VB Formulários e NF', null),  -- 390.02.17
        ('250009', '180.01.02', 'VB PARA REBAIXA DE CUSTO DE MERCADORIA', null),  -- 390.02.18
        ('250011', '180.01.02', 'VB FUNDO DE RESERVA', null),  -- 390.02.19
        ('250012', '180.01.02', 'VB CONTA CORRENTE FORNECEDOR', null),  -- 390.02.20
        ('250013', '180.01.02', 'VB PARA REBAIXAR DE CMV VALOR APURAR', null),  -- 390.02.21
        ('250014', '180.01.02', 'VB ACRESCIMO CMV (330)', null),  -- 390.02.22
        ('250998', '120.01.100', 'Adiantamento a Fornecedor', null),  -- 330.05.10
        ('250999', '120.01.100', 'Adiantamento a Fornecedor Mercadoria Revenda', null),  -- 330.05.11
        ('300001', '140.01.01', 'Salário CLT Dentro', null),  -- 350.06.04
        ('300002', '140.01.03', 'Férias (DP)', null),  -- 350.06.18
        ('300003', '140.01.02', '13° Salário (DP)', null),  -- 350.06.17
        ('300004', '140.01.23', 'Gratificações', 'pessoal (350.01): entra pela folha gerencial'),  -- 350.01.54
        ('300005', '140.01.11', 'Rescisão (DP)', null),  -- 350.06.27
        ('300006', '140.02.03', 'Água e Esgoto', null),  -- 350.02.01
        ('300007', '140.02.01', 'Aluguel Predial', null),  -- 350.02.03
        ('300008', '130.03.03', 'Conduções Urbanas', null),  -- 340.04.32
        ('300009', '180.01.01', 'Doação', null),  -- 390.02.03
        ('300012', '140.02.15', 'Despesas Postais', null),  -- 350.02.93
        ('300013', '140.02.02', 'Energia Elétrica', null),  -- 350.02.11
        ('300014', '140.04.02', 'Serviço Gráfico', null),  -- 350.05.05
        ('300015', '140.02.07', 'Impressões e Formulários', null),  -- 350.02.86
        ('300016', '140.02.17', 'Diversos', null),  -- 350.02.68
        ('300017', '140.02.06', 'Materiais de Limpeza', null),  -- 350.02.104
        ('300018', '140.02.07', 'Material de Expediente', null),  -- 350.02.17
        ('300019', '140.01.17', 'Outro Custo Pessoal (Custo)', null),  -- 350.06.32
        ('300020', '140.01.15', 'Contribuição Sindical', null),  -- 350.02.09
        ('300021', '140.01.08', 'Auxilio Transporte', null),  -- 350.06.10
        ('300022', '140.02.04', 'Telefonia Movel', null),  -- 350.02.83
        ('300023', '420.02.03', 'Móvel e Utensílio', null),  -- 810.01.05
        ('300024', '130.03.02', 'Viagens e Estadias', null),  -- 340.04.50
        ('300025', '140.02.12', 'Reparo e Manutenção', null),  -- 350.02.19
        ('300026', '140.06.01', 'Honorários Advocaticios', null),  -- 350.02.92
        ('300027', '140.06.02', 'Assessoria Contábil', null),  -- 350.02.04
        ('300028', '140.02.06', 'Dedetização', null),  -- 350.02.84
        ('300029', '140.01.17', 'Ajuda de Custo (DP)', null),  -- 350.06.16
        ('300030', '140.02.11', 'Serviço de Segurança', null),  -- 350.02.20
        ('300031', '140.06.04', 'Consultoria', null),  -- 350.02.08
        ('300032', '140.03.05', 'Serviço de Cobrança', null),  -- 350.04.01
        ('300033', '140.01.17', 'Seguro de Vida Itaú (DP)', null),  -- 350.06.12
        ('300034', '140.01.01', 'Aprendiz', null),  -- 350.06.43
        ('300035', '140.02.17', 'Alvará de Funcionamento', null),  -- 350.02.66
        ('301001', '120.01.103', 'Descontos de Mercadoria Comprada', null),  -- 330.01.26
        ('301002', '160.01.01', 'Juro de Cliente', null),  -- 370.01.02
        ('301003', '190.01.03', 'Desconto Referente a Antecipação de Fornecedores', null),  -- 391.01.12
        ('301005', '170.01.01', 'IOF', null),  -- 380.01.02
        ('301006', '190.01.09', 'Recuperação de Crédito', null),  -- 391.01.10
        ('301007', '160.01.03', 'Rendimento de Aplicação', null),  -- 370.01.03
        ('301008', '140.02.17', 'Consulta de Crédito', null),  -- 350.02.91
        ('301009', '170.01.02', 'Tarifas Bancárias Itaú', null),  -- 380.01.10
        ('301010', '180.01.02', 'Perdas e Devedores Duvidosos', null),  -- 390.02.12
        ('301011', '130.02.03', 'Taxas de Cartões Débito', null),  -- 380.01.25
        ('301013', '400.02.01', 'Amortização de Empréstimo Bancário', null),  -- 800.02.01
        ('301015', '140.02.15', 'Cartórios, CDL e Outros', null),  -- 350.02.06
        ('301016', '160.01.04', 'Outras Receitas Financeiras', null),  -- 370.01.06
        ('301017', '110.02.01', 'Devoluções de Vendas', null),  -- 320.02.09
        ('301301', '100.02.02', 'Receita Descarga Mercadoria', null),  -- 310.03.04
        ('302001', '120.01.52', 'ICMS sobre Custo da Mercadoria', 'ICMS: fora da DRE do Power BI'),  -- 320.02.04
        ('302002', '110.03.01', 'PIS (SE NAO ESTIVER NO CMV)', null),  -- 320.01.10
        ('302003', '110.03.02', 'COFINS (Se não estiver no CMV)', null),  -- 320.02.12
        ('302004', '170.01.08', 'Contribuição Provisória sobre Movimentação', null),  -- 380.01.31
        ('302005', '120.03.09', 'IRRF (Custo)', null),  -- 330.02.02
        ('302006', '200.01.01', 'CSLL', 'CSLL: vai para rateio'),  -- 400.02.01
        ('302007', '140.02.16', 'IPTU', null),  -- 350.02.14
        ('302008', '140.02.16', 'Taxa Municipal, Estadual ou Federal', null),  -- 350.02.26
        ('302009', '140.02.16', 'Taxas Federais', null),  -- 350.02.98
        ('302010', '140.02.16', 'Taxas Estaduais', null),  -- 350.02.99
        ('302011', '120.03.09', 'IRRF s/ Serviços de Terceiros', null),  -- 330.02.39
        ('302012', '180.01.03', 'Multa s/ Infrações Fiscais', null),  -- 390.02.09
        ('302013', '180.01.04', 'Multa Atraso nos Tributos', null),  -- 390.02.10
        ('302014', '120.01.52', 'GUIA SUBSTITUICAO TRIBUTARIA (DESP)', null),  -- 320.01.12
        ('302015', '120.01.52', 'GUIA SUBSTITUICAO TRIBUTARIA (REC)', null),  -- 320.01.13
        ('302016', '110.03.07', 'GERACAO DESPESAS GNRE', null),  -- 320.01.14
        ('302017', '400.01.03', 'Parcelamento', null),  -- 390.02.07
        ('302018', '110.03.07', 'CONTA FUNCEP GNRE', null),  -- 320.01.15
        ('302019', '110.03.04', 'ICMS Regime Mensal de Apuração', 'ICMS: fora da DRE do Power BI'),  -- 320.02.05
        ('401001', '130.03.03', 'Oficina e Manutenção (Desp. Variáveis)', null),  -- 340.04.05
        ('401002', '140.01.14', 'Material de Trabalho e EPI´s (DP)', null),  -- 350.06.11
        ('401003', '120.03.07', 'Cesta Básica e de Alimentação (Custo)', null),  -- 330.02.06
        ('401004', '130.03.05', 'Manutenção Pneus', null),  -- 340.04.29
        ('401005', '120.03.11', 'Rescisões Contratuais - Motoristas', null),  -- 330.02.38
        ('401006', '130.03.02', 'Viagens e Estadias - Motorista', null),  -- 340.04.51
        ('401007', '130.03.10', 'Balsas e Pedagios', null),  -- 340.04.48
        ('401008', '130.03.03', 'Alinhamento e Balanceamento', null),  -- 340.04.47
        ('401009', '180.01.02', 'Produtos vencidos, quebra ou roubo', null),  -- 390.02.02
        ('401010', '120.03.17', 'Reestituição de Motorista', null),  -- 330.02.37
        ('401011', '140.01.01', 'Salários', 'pessoal (350.01): entra pela folha gerencial'),  -- 350.01.58
        ('401012', '130.01.03', 'Premiação Motorista (Custo)', null),  -- 330.02.41
        ('401013', '130.01.07', 'Comissão Motoristas', null),  -- 340.03.07
        ('401015', '130.03.01', 'Combustível para Carretas (Desp. Variáveis)', null),  -- 340.04.25
        ('401016', '130.03.01', 'Oleo Arla', null),  -- 340.04.34
        ('401017', '130.03.03', 'Filtro', null),  -- 340.04.33
        ('401018', '130.03.05', 'Pneus', null),  -- 340.04.46
        ('401019', '130.03.03', 'Material P/ Lavador', null),  -- 340.04.55
        ('401020', '130.03.03', 'Conservação e Limpeza - Transporte', null),  -- 340.04.54
        ('401021', '130.03.01', 'Combustível e Lubrificante', null),  -- 340.04.02
        ('401022', '180.01.03', 'Multa de trânsito', null),  -- 390.02.04
        ('401023', '140.05.04', 'IPVA / Licenciamento', null),  -- 350.03.02
        ('401024', '180.01.02', 'Outra Despesa Não Operacional', null),  -- 390.02.06
        ('401025', '120.03.03', 'Férias (Custo)', null),  -- 330.02.19
        ('401026', '120.03.02', '13° Salário (Custo)', null),  -- 330.02.18
        ('401027', '130.03.08', 'Seguro de Carga e Rastreamento (Desp. Variáveis)', null),  -- 340.04.13
        ('401028', '130.03.02', 'Diária Motorista (Desp. Variáveis)', null),  -- 340.04.03
        ('401029', '120.03.18', 'Diária do Lombador', null),  -- 330.02.31
        ('401030', '130.03.03', 'Manutenção Carreta - RIA-9C95', null),  -- 340.04.35
        ('401031', '130.03.03', 'Manutenção Carreta - SAW-8B77', null),  -- 340.04.36
        ('401032', '130.03.03', 'Manutenção Carreta - SBU-1G58', null),  -- 340.04.37
        ('401033', '130.03.03', 'Manutenção Carreta - THV-9I29', null),  -- 340.04.38
        ('401034', '130.03.03', 'Manutenção Carreta - THZ-5H28', null),  -- 340.04.39
        ('401035', '130.03.03', 'Manutenção Carreta - TIK-2A99', null),  -- 340.04.40
        ('401036', '130.03.03', 'Manutenção Carreta - SBH-1F53', null),  -- 340.04.41
        ('401037', '130.03.03', 'Manutenção Carreta - SBH-8C81', null),  -- 340.04.42
        ('401038', '130.03.03', 'Manutenção Carreta - SBJ-0J92', null),  -- 340.04.43
        ('401039', '130.03.03', 'Manutenção Carreta - SBQ-7C51', null),  -- 340.04.44
        ('401040', '130.03.03', 'Manutenção Carreta - SBT-7I02', null),  -- 340.04.45
        ('401041', '130.03.09', 'Taxas de despachos', null),  -- 340.03.16
        ('401042', '140.02.16', 'Taxas GRU', null),  -- 350.02.107
        ('401043', '130.03.11', 'Despesas de Viagem Carretas (Desp. Variáveis)', null),  -- 340.04.11
        ('401047', '130.03.01', 'Combustível e Lubrificante', null),  -- 340.04.56
        ('402001', '140.02.14', 'Seguro - CD', null),  -- 350.02.89
        ('402002', '120.03.14', 'Material de Trabalho e EPI´s (Custo)', null),  -- 330.02.12
        ('402003', '140.01.13', 'Treinamento e Curso (DP)', null),  -- 350.06.08
        ('402004', '120.01.12', 'Embalagem', null),  -- 340.01.02
        ('402005', '120.03.07', 'Auxilio Alimentação (Custo)', null),  -- 330.02.10
        ('402006', '140.01.11', 'Rescisão de Contrato', null),  -- 350.02.51
        ('402007', '190.01.08', 'Receita de Papelão', null),  -- 391.01.09
        ('402008', '130.03.12', 'Descarrego (Desp. Variáveis)', null),  -- 340.04.10
        ('402009', '120.03.01', 'Salário CLT Dentro (Custo)', null),  -- 330.02.04
        ('402010', '130.01.03', 'Premiação CD (Custo)', null),  -- 330.02.40
        ('402011', '140.02.12', 'Manutenção de Equipamento', null),  -- 350.02.96
        ('402012', '140.02.12', 'Serviço de Manutenção Empresa', null),  -- 350.02.58
        ('402013', '140.02.08', 'Material de Consumo CD', null),  -- 350.02.88
        ('402014', '180.01.02', 'Acidente - CD', null),  -- 390.02.13
        ('402016', '120.03.03', 'Férias CD (Custo)', null),  -- 330.02.42
        ('402017', '120.03.02', '13° Salário (Custo) - CD', null),  -- 330.02.44
        ('402018', '140.01.17', 'Beneficiamento', null),  -- 350.06.40
        ('402019', '900.01.01', 'Transferência entre Filiais (Saída)', null),  -- 820.01.05
        ('402020', '420.02.07', 'Imobilizado', null),  -- 810.02.02
        ('403000', '120.01.73', 'Custo de Armazenamento de Mercadoria', null),  -- 330.04.01
        ('403001', '140.02.05', 'Locação de Equipamento', null),  -- 350.02.15
        ('500001', '130.03.02', 'Viagens e Estadias - CPD', null),  -- 340.04.53
        ('500002', '140.06.04', 'Consultoria Financeira', null),  -- 350.02.72
        ('500003', '140.02.04', 'Internet', null),  -- 350.02.13
        ('500004', '140.02.10', 'Manutenção de Sistema', null),  -- 350.02.97
        ('500005', '140.02.12', 'Manutenção Predial', null),  -- 350.02.103
        ('500006', '140.02.17', 'Transmissão de Dados', null),  -- 350.02.90
        ('500008', '420.02.07', 'Outro Investimento', null),  -- 810.01.06
        ('500009', '140.02.09', 'Sistema', null),  -- 350.02.85
        ('600001', '140.01.16', 'Plano de Saúde dos Funcionários (DP)', null),  -- 350.06.13
        ('600002', '140.01.14', 'Fardamentos (DP)', null),  -- 350.06.06
        ('600003', '140.01.11', 'Indenizações e Rescisões', null),  -- 350.06.42
        ('600004', '140.04.04', 'Anuncios e Publicações', null),  -- 350.05.28
        ('600005', '130.03.02', 'Viagens e Estadias - RH', null),  -- 340.04.52
        ('600006', '140.04.04', 'Evento', null),  -- 350.05.03
        ('600007', '140.01.11', 'Deposito Judicial', null),  -- 350.02.106
        ('600008', '140.01.11', 'Custas Processuais', null),  -- 350.02.105
        ('600009', '140.01.09', 'IRRF (DP)', null),  -- 350.06.02
        ('600010', '140.01.13', 'Recrutamento e Seleção', null),  -- 350.06.41
        ('600011', '120.03.17', 'Outros Lançamentos (Custo)', null),  -- 330.02.27
        ('600012', '140.01.15', 'Taxa Sindical', null),  -- 350.02.94
        ('600013', '140.01.04', 'INSS', 'pessoal (350.01): entra pela folha gerencial'),  -- 350.01.57
        ('600014', '140.01.05', 'FGTS', 'pessoal (350.01): entra pela folha gerencial'),  -- 350.01.14
        ('600015', '140.01.19', 'Feiras e Eventos', null),  -- 350.02.74
        ('893001', '420.02.03', 'Imovéis', null),  -- 810.01.19
        ('893002', '420.02.08', 'Leasing', null),  -- 810.01.18
        ('893003', '420.02.03', 'Terreno', null),  -- 810.01.07
        ('893004', '420.02.02', 'Aquisição De Veiculos', null),  -- 810.01.10
        ('893005', '420.02.02', 'Veículo e Moto', null),  -- 810.01.09
        ('893006', '420.02.08', 'Consórcio', null),  -- 810.01.11
        ('893007', '420.02.08', 'Consórcio e Leasing', null),  -- 810.01.01
        ('893008', '400.02.01', 'Amortização de Financiamentos', null),  -- 800.02.08
        ('893009', '170.01.05', 'Juros de Financiamentos', null),  -- 380.01.28
        ('893010', '420.01.01', 'Capitalização', null),  -- 810.02.03
        ('893011', '420.02.05', 'Investimento em Sistema', null),  -- 810.01.20
        ('893013', '400.02.01', 'Amortização de Empréstimo Bancário', null),  -- 800.02.01
        ('893015', '420.01.03', 'Investimento à Terceiros', null),  -- 810.02.06
        ('900001', '180.01.02', 'Perdas', null),  -- 390.02.11
        ('900002', '140.01.22', 'Pró-Labore', null),  -- 350.06.07
        ('900003', '140.03.03', 'Viagem', null),  -- 350.04.02
        ('900004', '210.01.01', 'Retirada de Sócio', null),  -- 410.01.01
        ('900005', '210.01.01', 'Participação dos Sócio', null),  -- 410.01.02
        ('900006', '210.01.01', 'Retirada de Lucro', null)  -- 410.01.03
       ) v (conta, id_descritivo, descricao, fora_da_dre)
 where c.slug = 'pj-cortes';

-- conferência
do $$
begin
    if (select count(*) from cfg.conta_dre d join cfg.cliente c on c.id = d.cliente_id
         where c.slug = 'pj-cortes') <> 238 then
        raise exception 'esperava 238 contas da PJ Cortes em cfg.conta_dre';
    end if;
end
$$;

commit;
