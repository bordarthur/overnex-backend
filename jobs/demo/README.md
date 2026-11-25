# Overnex — dados da empresa de demonstração

Gera os dados fictícios da **Distribuidora Overnex** (slug `distribuidora-overnex`), a empresa que a LANN usa para apresentar o Overnex a clientes, e envia os Parquet ao Supabase Storage. A carga diária os leva ao `mart` como os de qualquer cliente.

```
jobs/demo (todo dia, junto com o snapshot) ──► Storage: overnex-bucket/demo/distribuidora-overnex/<tabela>.parquet
                                                         │
                                                         ▼  carga (a mesma dos clientes)
                                              mart.<tabela>, linhas do cliente distribuidora-overnex
```

Nada vem de cliente real: nomes de clientes, fornecedores, pessoas e produtos são combinações inventadas, e os números saem de curvas e sorteios.

## A empresa

Atacadista de alimentos, bebidas, limpeza e higiene, com dois CNPJs:

| | Overnex Fortaleza (matriz) | Overnex Recife (filial) |
|---|---|---|
| Faturamento | ~R$ 6 milhões/mês | ~R$ 3,3 milhões/mês |
| Vendedores | 14 (varejo, food service e redes) | 9 |
| Pessoas na folha | ~75 | ~40 |

- **Faturamento** perto de R$ 9 a 11 milhões por mês, com crescimento de ~10% ao ano e sazonalidade (dezembro forte, janeiro e fevereiro fracos).
- **Margem bruta** de ~30% sobre o preço; **EBITDA** de 4% a 7%.
- **Caixa** entre R$ 1,5 e 3 milhões: o que passa de R$ 2,3 milhões vira retirada dos sócios.
- **12 linhas de produto** (Grãos e cereais, Bebidas, Laticínios…), cada uma com a sua conta de receita e de CMV no plano da LANN. A margem de cada linha passeia mês a mês (até ±4 p.p.).
- **Clientes** com perfis de pagamento: a maioria pontual, alguns atrasam, poucos dão calote. Uma rede de Recife (Rede Frevo Supermercados) paga tudo com 35 a 55 dias de atraso: é a primeira da lista "Quem cobrar".
- **Vendedores** com desempenho que varia mês a mês e trocas de tempos em tempos: sempre há alguém em rampa (os 6 primeiros meses de casa) e alguém em queda.
- Empréstimo a cada 26 meses, caminhão a cada 7, equipamentos a cada 11; 13º em novembro e dezembro.

O passado não muda de um dia para o outro: cada dia, cliente, pessoa e mês tem o seu sorteio, com semente fixa (`demo/sorteio.py`). A carga de amanhã repete tudo e acrescenta o dia novo. Trocar a regra do gerador muda a história inteira, de uma vez, na carga seguinte.

## As tabelas

As mesmas da PJ Cortes (migrations 12, 13, 18 e 23): o gerador escreve as colunas do mart, e a carga troca só as linhas deste cliente. Não precisa de migration.

| Tabela | O que é |
|---|---|
| `f_vendas_pj` | itens de pedido dos dias úteis até a véspera (o mart guarda 13 meses; o resumo mensal, tudo) |
| `f_metas_comercial_pj` | meta por vendedor e mês |
| `d_vendedor_pj`, `d_cliente_pj`, `d_bitem_pj`, `d_fornecedor_pj` | cadastros |
| `f_receitas_pj`, `f_despesas_pj` | títulos a receber e a pagar, desde set/2024 |
| `f_folha_pj_cortes` | folha por pessoa, mês e lançamento (meses fechados) |
| `f_fluxo_pj` | caixa realizado por dia, desde 01/11/2024 (saldo inicial no cadastro) |
| `f_lancamentos_agrupados_pj` | a DRE por conta do plano, filial e mês |
| `f_orcamento_pj` | orçado, cenário "Base", até dezembro do ano corrente |

O cadastro de produção (empresas, mapa, fontes, de-para das contas e saldo inicial) está em [supabase/producao/distribuidora_overnex_cadastro.sql](../../supabase/producao/distribuidora_overnex_cadastro.sql). O que está lá precisa bater com `demo/modelo.py` e `demo/tabelas.py`: `tests/test_cadastro_sql.py` confere, e `tests/test_esquema.py` confere as colunas contra as migrations.

## Rodar

```bash
uv sync
uv run python -m demo --saida saida                     # só grava os Parquet na pasta
uv run python -m demo --saida saida --data 2026-11-25   # como se a carga fosse nesse dia
uv run python -m demo                                   # gera e envia (precisa do .env)
```

| Variável | Obrigatória | O que faz |
|---|---|---|
| `SUPABASE_URL` | sim (para enviar) | a mesma do snapshot |
| `SUPABASE_SECRET_KEY` | sim (para enviar) | chave secret do projeto, a mesma do snapshot |
| `SUPABASE_BUCKET` | não | padrão `overnex-bucket` |
| `PREFIXO_DEMO` | não | padrão `demo/distribuidora-overnex` (precisa bater com `cfg.fonte_carga.caminho_bucket`) |
| `FUSO` | não | padrão `America/Fortaleza`, para saber que dia é hoje |
| `DATA_SNAPSHOT` | não | `AAAA-MM-DD`: gera como se a carga fosse nesse dia |

Códigos de saída: `0` tudo enviado · `1` algum arquivo falhou · `2` configuração inválida.

No GitHub Actions, é o job `demo` do [diario.yml](../../.github/workflows/diario.yml), em paralelo com o snapshot e com os mesmos segredos. A carga espera os dois, mas roda mesmo se a demo falhar (as fontes da demo terminam com erro e o mart dela fica como estava).

## Checagens

```bash
uv run ruff format --check . && uv run ruff check . && uv run mypy && uv run pytest
```

Os testes não precisam de banco: geram os dados duas vezes (hoje e 9 dias depois) e conferem que o passado é igual, que a DRE bate com as vendas e a folha, que o a receber soma os pedidos, que o caixa nunca fica negativo e que margem e inadimplência ficam numa faixa plausível.
