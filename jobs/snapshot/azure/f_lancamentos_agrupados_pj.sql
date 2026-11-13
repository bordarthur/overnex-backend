-- PJ Cortes — lançamentos agrupados da DRE (view no Azure SQL Database).
--
-- NÃO roda no Supabase: crie no Azure SQL (SSMS ou Query editor do portal), no banco que o
-- snapshot lê. Depois, acrescente dbo.f_Lancamentos_Agrupados_PJ à variável TABELAS do
-- GitHub: o snapshot copia a view como as outras tabelas (SELECT *), e a carga grava em
-- mart.f_lancamentos_agrupados_pj (migration 18, fonte em
-- supabase/producao/pj_cortes_lancamentos_agrupados.sql).
--
-- É a view do Power BI da PJ (Fabric), com três ajustes para o Azure SQL, sem mudar regra:
-- 1. as datas da PJ no Azure são texto ('01/08/2026 00:00:00'): toda data passa por
--    TRY_CONVERT(date, LEFT(x, 10), 103) (dia/mês/ano), com o próprio valor como reserva
--    se a coluna já for date. A comparação en_datemi >= '2025-01-01'
--    passa a ser entre datas (em texto, '15/03/2024' >= '2025-01-01' é verdadeiro);
-- 2. nomes de coluna como ficaram no Azure: Data_de_Competencia e
--    Valor_Regime_de_Competencia (no Fabric, com espaço e acento);
-- 3. saída com Data em date e a coluna Id_Descritivo (sem espaço); linha sem data fica
--    de fora (não entra em nenhum mês).
--
-- Precisa existir no mesmo banco: d_De_Para_PJ_Cortes (en_codigoplanocliente, Id_LANN) e
-- d_Plano_Contas (Id_Descritivo, Fator), além das tabelas que o snapshot já copia.
--
-- Valor: com o sinal do resultado (receita positiva, custo e despesa negativos).
--
-- A CONFERIR (regra igual à do Fabric, só marcada aqui):
-- - a conta 301001 sai da regra geral só se o Id_LANN dela estiver na lista de exclusão
--   (o '301001' da lista de contas está comentado) e também entra no bloco "Fretes e
--   Retorno": se não estiver, conta duas vezes;
-- - a conta 301002 entra no bloco "Juro de cliente"; se o Id_LANN dela for 160.01.04,
--   entra também no bloco seguinte (Id_LANN = '160.01.04').

CREATE OR ALTER VIEW dbo.f_Lancamentos_Agrupados_PJ
AS

WITH Base AS (

    -------------------------------------------------------------------
    -- LANÇAMENTOS  (f_Lancamentos_PJ_Cortes)
    -------------------------------------------------------------------
    SELECT
        L.Empresa                                 AS Empresa,
        COALESCE(TRY_CONVERT(date, LEFT(L.Data_de_Competencia, 10), 103),
                 TRY_CONVERT(date, L.Data_de_Competencia))
                                                  AS Data,
        CASE
            WHEN L.Tipo = 'E'
                 THEN L.Valor_Regime_de_Competencia
            ELSE L.Valor_Regime_de_Competencia * -1
        END                                       AS Valor,
        L.Id_Descritivo                           AS Id_Descritivo
    FROM f_Lancamentos_PJ_Cortes AS L

    UNION ALL

    -------------------------------------------------------------------
    -- DEVOLUÇÃO  (f_Devolucao_PJ)
    -------------------------------------------------------------------
    SELECT
        '1 PJ Cortes'                             AS Empresa,
        COALESCE(TRY_CONVERT(date, LEFT(D.en_dataemissao, 10), 103),
                 TRY_CONVERT(date, D.en_dataemissao))
                                                  AS Data,
        D.en_valortotal * -1                      AS Valor,
        '110.02.01'                               AS Id_Descritivo
    FROM f_Devolucao_PJ AS D

    UNION ALL

    -------------------------------------------------------------------
    -- DESPESAS (REGRA GERAL)  (f_Despesas_PJ + d_Plano_Contas)
    -------------------------------------------------------------------
    SELECT
        '1 PJ Cortes'                             AS Empresa,
        COALESCE(TRY_CONVERT(date, LEFT(DE.en_datemi, 10), 103),
                 TRY_CONVERT(date, DE.en_datemi)) AS Data,
        DE.en_valor * PC.Fator                    AS Valor,
        DP.Id_LANN                                AS Id_Descritivo
    FROM f_Despesas_PJ DE
    LEFT JOIN d_De_Para_PJ_Cortes DP
        ON DP.en_codigoplanocliente = DE.en_codigoplanocliente
    LEFT JOIN d_Plano_Contas PC
        ON PC.Id_Descritivo = DP.Id_LANN
    WHERE DE.en_datemi IS NOT NULL
      AND DP.Id_LANN NOT IN (
        '120.01.01',   -- 330.01.01
        '100.02.03',   -- 310.03.01
        '100.02.02',   -- 310.03.01
        '100.01.17',   -- 310.03.01
        '120.03.03',   -- 330.02.42 & 330.02.43
        '140.01.22',
        '210.01.01','110.01.01',
        '110.03.04',
        '120.01.52',
        '120.03.09',
        '120.04.02',
        '130.01.01',
        '140.01.01',
        '140.01.02',
        '140.01.03',
        '140.01.04',
        '140.01.05',
        '140.01.06',
        '140.01.09',
        '140.01.10','140.01.12','140.01.13',
        '140.01.16',--'140.02.17',
        '140.01.18','140.01.20','140.01.21','140.01.23',
        '200.01.01', '160.01.01', '160.01.04'
        --'140.01.15', '140.01.19'
      )
      AND DE.en_codigoplanocliente NOT IN (
        '300012','301002','301017','100016',
        '301013','893006','893009', '201015'--,'301001'
      )

    UNION ALL
    SELECT
        '1 PJ Cortes'                             AS Empresa,
        COALESCE(TRY_CONVERT(date, LEFT(DE.en_datemi, 10), 103),
                 TRY_CONVERT(date, DE.en_datemi)) AS Data,
        DE.en_valor * -1                          AS Valor,
        DP.Id_LANN                                AS Id_Descritivo
    FROM f_Despesas_PJ DE
    LEFT JOIN d_De_Para_PJ_Cortes DP
        ON DP.en_codigoplanocliente = DE.en_codigoplanocliente
    WHERE DE.en_datemi IS NOT NULL
      AND COALESCE(TRY_CONVERT(date, LEFT(DE.en_datemi, 10), 103),
                   TRY_CONVERT(date, DE.en_datemi)) >= '20250101'
      AND DP.Id_LANN IN ('120.01.103')

    UNION ALL
    SELECT
        '1 PJ Cortes'                             AS Empresa,
        COALESCE(TRY_CONVERT(date, LEFT(DE.en_datpag, 10), 103),
                 TRY_CONVERT(date, DE.en_datpag)) AS Data,
        DE.en_valor * -1                          AS Valor,
        DP.Id_LANN                                AS Id_Descritivo
    FROM f_Despesas_PJ DE
    LEFT JOIN d_De_Para_PJ_Cortes DP
        ON DP.en_codigoplanocliente = DE.en_codigoplanocliente
    WHERE DE.en_datemi IS NOT NULL
      AND DP.Id_LANN IN ('210.01.01')

    UNION ALL

    -------------------------------------------------------------------
    -- JURO DE CLIENTE (160.01.01)  (f_Despesas_PJ)
    -------------------------------------------------------------------
    SELECT
        '1 PJ Cortes'                             AS Empresa,
        COALESCE(TRY_CONVERT(date, LEFT(DE.en_datemi, 10), 103),
                 TRY_CONVERT(date, DE.en_datemi)) AS Data,
        DE.en_valor * -1                          AS Valor,
        DP.Id_LANN                                AS Id_Descritivo
    FROM f_Despesas_PJ DE
    LEFT JOIN d_De_Para_PJ_Cortes DP
        ON DP.en_codigoplanocliente = DE.en_codigoplanocliente
    WHERE DE.en_datemi IS NOT NULL
      AND DE.en_codigoplanocliente = '301002'

    UNION ALL
    SELECT
        '1 PJ Cortes'                             AS Empresa,
        COALESCE(TRY_CONVERT(date, LEFT(DE.en_datemi, 10), 103),
                 TRY_CONVERT(date, DE.en_datemi)) AS Data,
        DE.en_valor * -1                          AS Valor,
        DP.Id_LANN                                AS Id_Descritivo
    FROM f_Despesas_PJ DE
    LEFT JOIN d_De_Para_PJ_Cortes DP
        ON DP.en_codigoplanocliente = DE.en_codigoplanocliente
    WHERE DE.en_datemi IS NOT NULL
      AND DP.Id_LANN = '160.01.04'

    UNION ALL

    -------------------------------------------------------------------
    -- JURO DE FINANCIAMENTO (Codigo 893009)  (f_Despesas_PJ)
    -------------------------------------------------------------------
    SELECT
        '1 PJ Cortes'                             AS Empresa,
        COALESCE(TRY_CONVERT(date, LEFT(DE.en_datemi, 10), 103),
                 TRY_CONVERT(date, DE.en_datemi)) AS Data,
        DE.en_valor * -1                          AS Valor,
        DP.Id_LANN                                AS Id_Descritivo
    FROM f_Despesas_PJ DE
    LEFT JOIN d_De_Para_PJ_Cortes DP
        ON DP.en_codigoplanocliente = DE.en_codigoplanocliente
    WHERE DE.en_datemi IS NOT NULL
      AND DE.en_codigoplanocliente = '893009'

    UNION ALL

    -------------------------------------------------------------------
    -- PROLABORE (140.01.22)  (f_Despesas_PJ + d_Plano_Contas)
    -------------------------------------------------------------------
    SELECT
        '1 PJ Cortes'                             AS Empresa,
        COALESCE(TRY_CONVERT(date, LEFT(DE.en_datemi, 10), 103),
                 TRY_CONVERT(date, DE.en_datemi)) AS Data,
        DE.en_valor * PC.Fator                    AS Valor,
        DP.Id_LANN                                AS Id_Descritivo
    FROM f_Despesas_PJ DE
    LEFT JOIN d_De_Para_PJ_Cortes DP
        ON DP.en_codigoplanocliente = DE.en_codigoplanocliente
    LEFT JOIN d_Plano_Contas PC
        ON PC.Id_Descritivo = DP.Id_LANN
    WHERE DE.en_datemi IS NOT NULL
      AND DP.Id_LANN = '140.01.22'

    UNION ALL

    -------------------------------------------------------------------
    -- FOLHA DE PAGAMENTO GERENCIAL  (f_Folha_PJ_Cortes)
    -------------------------------------------------------------------
    SELECT
        '1 PJ Cortes'                             AS Empresa,
        COALESCE(TRY_CONVERT(date, LEFT(F.[Data], 10), 103),
                 TRY_CONVERT(date, F.[Data]))     AS Data,
        F.Valor * -1                              AS Valor,
        F.Id_Descritivo                           AS Id_Descritivo
    FROM f_Folha_PJ_Cortes AS F
    WHERE F.[Data] IS NOT NULL

    UNION ALL

    -------------------------------------------------------------------
    -- VENDAS - RECEITA DE VENDAS  (f_Vendas_PJ)
    -------------------------------------------------------------------
    SELECT
        '1 PJ Cortes'                             AS Empresa,
        COALESCE(TRY_CONVERT(date, LEFT(V.en_dataemissao, 10), 103),
                 TRY_CONVERT(date, V.en_dataemissao))
                                                  AS Data,
        V.en_valortotal                           AS Valor,
        B.id_secao                                AS Id_Descritivo
    FROM f_Vendas_PJ AS V
    LEFT JOIN d_Bitem_PJ B
       ON B.en_codigoproduto = V.en_codigoproduto

    UNION ALL

    -------------------------------------------------------------------
    -- VENDAS - CMV  (f_Vendas_PJ): custo médio da seção × (venda − devolução), por mês
    -------------------------------------------------------------------
    SELECT
        '1 PJ Cortes'                             AS Empresa,
        V.Data                                    AS Data,
        V.CMV_Calculado                           AS Valor,
        V.Secao                                   AS Id_Descritivo
    FROM (
        SELECT
            DATEFROMPARTS(YEAR(VV.Dia), MONTH(VV.Dia), 1)                    AS Data,
            B.id_secao_cmv                                                   AS Secao,
            -(
                CAST(SUM(VV.en_custototal) AS FLOAT)
                / NULLIF(CAST(SUM(VV.en_valortotal) AS FLOAT), 0)
            )
            * (
                CAST(SUM(VV.en_valortotal) AS DECIMAL(38,10))
                - ISNULL(CAST(MAX(D.Devolucao) AS DECIMAL(38,10)), 0)
            )                                                                AS CMV_Calculado
        FROM (
            SELECT X.*,
                   COALESCE(TRY_CONVERT(date, LEFT(X.en_dataemissao, 10), 103),
                            TRY_CONVERT(date, X.en_dataemissao)) AS Dia
            FROM f_Vendas_PJ X
        ) VV
        LEFT JOIN d_Bitem_PJ B
            ON B.en_codigoproduto = VV.en_codigoproduto
        LEFT JOIN (
            SELECT
                DATEFROMPARTS(YEAR(D0.Dia), MONTH(D0.Dia), 1)                AS Data,
                B2.id_secao_cmv                                              AS Secao,
                SUM(D0.en_valortotal)                                        AS Devolucao
            FROM (
                SELECT Y.*,
                       COALESCE(TRY_CONVERT(date, LEFT(Y.en_dataemissao, 10), 103),
                                TRY_CONVERT(date, Y.en_dataemissao)) AS Dia
                FROM f_Devolucao_PJ Y
            ) D0
            LEFT JOIN d_Bitem_PJ B2
                ON B2.en_codigoproduto = D0.en_codigoproduto
            WHERE D0.Dia IS NOT NULL
            GROUP BY DATEFROMPARTS(YEAR(D0.Dia), MONTH(D0.Dia), 1), B2.id_secao_cmv
        ) D ON D.Secao = B.id_secao_cmv
           AND D.Data  = DATEFROMPARTS(YEAR(VV.Dia), MONTH(VV.Dia), 1)
        WHERE VV.Dia IS NOT NULL
        GROUP BY DATEFROMPARTS(YEAR(VV.Dia), MONTH(VV.Dia), 1), B.id_secao_cmv
    ) V

    UNION ALL

    -------------------------------------------------------------------
    -- VENDAS - RECEITA DE VENDAS  (f_Vendas_Agile_PJ_Cortes)
    -------------------------------------------------------------------
    SELECT
        '1 PJ Cortes'                             AS Empresa,
        COALESCE(TRY_CONVERT(date, LEFT(VA.en_dataemissao, 10), 103),
                 TRY_CONVERT(date, VA.en_dataemissao))
                                                  AS Data,
        VA.en_valortotal                          AS Valor,
        B.id_secao                                AS Id_Descritivo
    FROM f_Vendas_Agile_PJ_Cortes AS VA
    LEFT JOIN d_Bitem_PJ B
       ON B.en_codigoproduto = VA.en_codigoproduto
    WHERE VA.en_codigoproduto IS NOT NULL

    UNION ALL

    -------------------------------------------------------------------
    -- VENDAS - CMV  (f_Vendas_Agile_PJ_Cortes)
    -------------------------------------------------------------------
    SELECT
        '1 PJ Cortes'                             AS Empresa,
        COALESCE(TRY_CONVERT(date, LEFT(VA.en_dataemissao, 10), 103),
                 TRY_CONVERT(date, VA.en_dataemissao))
                                                  AS Data,
        VA.en_custototal * -1                     AS Valor,
        B.id_secao_cmv                            AS Id_Descritivo
    FROM f_Vendas_Agile_PJ_Cortes AS VA
    LEFT JOIN d_Bitem_PJ B
       ON B.en_codigoproduto = VA.en_codigoproduto

    UNION ALL

    -------------------------------------------------------------------
    -- DEVOLUÇÃO  (f_Devolucao_PJ_Agile)
    -------------------------------------------------------------------
    SELECT
        '1 PJ Cortes'                             AS Empresa,
        COALESCE(TRY_CONVERT(date, LEFT(DA.en_datemi, 10), 103),
                 TRY_CONVERT(date, DA.en_datemi)) AS Data,
        DA.en_VALTOT * -1                         AS Valor,
        DA.Id_Descritivo                          AS Id_Descritivo
    FROM f_Devolucao_PJ_Agile AS DA

    UNION ALL

    -------------------------------------------------------------------
    -- FRETES E RETORNO  (f_Despesas_PJ)
    -------------------------------------------------------------------
    SELECT
        '1 PJ Cortes'                             AS Empresa,
        COALESCE(TRY_CONVERT(date, LEFT(DE.en_datemi, 10), 103),
                 TRY_CONVERT(date, DE.en_datemi)) AS Data,
        DE.en_valor * -1                          AS Valor,
        DP.Id_LANN                                AS Id_Descritivo
    FROM f_Despesas_PJ DE
    LEFT JOIN d_De_Para_PJ_Cortes DP
        ON DP.en_codigoplanocliente = DE.en_codigoplanocliente
    WHERE DP.Id_LANN IN ('100.02.02')

    UNION ALL
    SELECT
        '1 PJ Cortes'                             AS Empresa,
        COALESCE(TRY_CONVERT(date, LEFT(DE.en_datemi, 10), 103),
                 TRY_CONVERT(date, DE.en_datemi)) AS Data,
        DE.en_valor * -1                          AS Valor,
        DP.Id_LANN                                AS Id_Descritivo
    FROM f_Despesas_PJ DE
    LEFT JOIN d_De_Para_PJ_Cortes DP
        ON DP.en_codigoplanocliente = DE.en_codigoplanocliente
    WHERE DP.Id_LANN IN ('100.02.03')

    UNION ALL
    SELECT
        '1 PJ Cortes'                             AS Empresa,
        COALESCE(TRY_CONVERT(date, LEFT(DE.en_datemi, 10), 103),
                 TRY_CONVERT(date, DE.en_datemi)) AS Data,
        DE.en_valor * -1                          AS Valor,
        DP.Id_LANN                                AS Id_Descritivo
    FROM f_Despesas_PJ DE
    LEFT JOIN d_De_Para_PJ_Cortes DP
        ON DP.en_codigoplanocliente = DE.en_codigoplanocliente
    WHERE DE.en_codigoplanocliente IN ('301001')
)

SELECT
    Empresa,
    Data,
    Id_Descritivo,
    SUM(Valor) AS Valor
FROM Base
WHERE Data IS NOT NULL
GROUP BY
    Empresa,
    Data,
    Id_Descritivo;
GO
