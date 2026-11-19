# Vários clientes no mart: como organizar as tabelas e a carga

Documento de decisão, aberto em 18/11/2026. **Ainda não decidido.**

## O que precisa ser decidido

Com o Supabase no plano Pro, os dados dos outros clientes vão subir para o mart. Hoje entram dois clientes (Nilo Maia e PJ Cortes), e o snapshot e a carga rodam juntos, um cliente depois do outro, num job só. Com dezenas de clientes, a atualização que começa às 9h pode terminar só no início da tarde, e o problema de um cliente atrasa todos.

São duas perguntas:

1. **Formato das tabelas:** uma tabela grande por assunto (vendas, receitas…) com a chave do cliente e da empresa, ou tabelas separadas por cliente?
2. **Formato da carga:** como fazer cada cliente carregar sozinho e rápido, sem depender dos outros?

A ideia do documento é mostrar que **as duas perguntas são quase independentes**. Uma tabela grande não impede a carga separada por cliente. O que deixa a carga lenta é o volume regravado por dia e a execução em sequência, não o formato da tabela.

## Como está hoje

### Tabelas

O plano das "tabelas enormes" nunca foi feito. Hoje **cada cliente tem as próprias tabelas** no `mart`, uma para cada Parquet do DW:

- 21 tabelas da Nilo Maia ([migration 5](../supabase/migrations/20261014000005_tabelas_de_dados.sql)) e 25 da PJ Cortes ([migration 12](../supabase/migrations/20261021000012_tabelas_pj_cortes.sql));
- todas com as mesmas colunas do Parquet, com os nomes preservados, mais `_linha`, `_cliente_id` e `_empresa_id`;
- as colunas mudam de um cliente para outro. A PJ usa `en_dataemissao` onde o DW padrão usa `en_datemicao`, por exemplo. Por isso existe `cfg.fonte_carga.colunas_modelo`, que diz à API qual coluna de cada cliente faz cada papel: `data`, `valor`, `custo`, `pedido`, `cliente`, `produto`, `abertura`, `vendedor`, `linha` e `quantidade`.

Uma tabela já é de todos os clientes: `mart.resumo_mensal`, com os totais mensais por cliente, empresa, tabela de origem e medida.

### Carga

[jobs/carga](../jobs/carga/README.md), para cada fonte de `cfg.fonte_carga`, uma depois da outra:

1. baixa o Parquet do Storage e valida a data do snapshot;
2. aplica a janela de datas, quando a fonte tem uma (só os últimos N meses entram no banco);
3. numa transação: tabela temporária e `COPY`, `delete from mart.<tabela> where _cliente_id = …` e `insert` de tudo;
4. commit. Até o commit, quem está no painel vê o dado anterior.

Cada fonte roda na própria transação, e a falha de uma não para as outras. O snapshot ([jobs/snapshot](../jobs/snapshot/README.md)) lê uma lista única de tabelas (`TABELAS`), também em sequência, e a carga só começa quando o snapshot inteiro termina.

### API

A API acha as tabelas do cliente pelo prefixo (`tabelas_do_cliente(conn, cliente, "f_vendas_", …)`) e monta o SQL com o nome da tabela e das colunas de cada cliente. Um painel de vendas soma todas as tabelas `f_vendas_*` do cliente. O `GET /api/dados/<tabela>` entrega ao front as colunas com os nomes do DW.

### Volume

As 21 tabelas do inventário do bucket que está na máquina somam **cerca de 5,8 milhões de linhas**. Três delas fazem quase tudo: vendas (2,0 mi), receitas (1,26 mi) e lançamentos agrupados (809 mil). Hoje tudo isso é baixado, lido e regravado **todo dia**, mesmo que só o mês corrente tenha mudado. Com 20 clientes desse porte, seriam perto de 100 milhões de linhas por dia.

## Algumas noções de Postgres usadas abaixo

- **Linha morta e vacuum.** No Postgres, `delete` não libera o espaço na hora: a linha fica "morta" até o *vacuum* (automático, o *autovacuum*) passar. Uma tabela totalmente apagada e reinserida todo dia carrega uma segunda cópia morta de si mesma até o vacuum limpar. Os índices também incham. O autovacuum começa quando as linhas mortas passam de cerca de 20% da tabela.
- **Trava.** `delete` e `insert` travam só as linhas que mexem. Duas cargas de clientes diferentes na mesma tabela **não se bloqueiam**, e quem lê também não espera: vê a versão anterior até o commit. `truncate` é diferente: trava a tabela inteira, inclusive para leitura, até o fim da transação.
- **Partição.** Uma tabela "mãe" sem dados próprios, dividida em tabelas "filhas" pelo valor de uma coluna. Aqui seria uma filha por cliente. Quem consulta usa só a mãe. Cada filha tem o próprio vacuum, as próprias estatísticas e os próprios índices, e pode ser solta ou apagada sem tocar nas outras.
- **Poda de partição.** Quando a consulta diz `where _cliente_id = '…'`, o Postgres abre só a filha daquele cliente. A poda acontece no planejamento só se o valor estiver na própria consulta. O filtro do RLS (`_cliente_id in (select seg.clientes_do_usuario())`) é uma subconsulta, que só se resolve na execução; aí o planejador precisa abrir todas as filhas.

## As opções

### 1. Uma tabela por cliente (como está hoje)

**Como fica.** Cada Parquet do DW vira `mart.<tabela>_<cliente>`, com as colunas do arquivo. Cliente novo = migration nova com os `create table` gerados pelo inventário (`python -m carga.inspecionar` já escreve o SQL) e o `mart.preparar_tabela_dados()` de cada um.

**Carga.** Sem mudança. Cada tabela só tem dados de um cliente, então apagar e inserir mexe só nela.

**API.** Sem mudança. O SQL é montado com o nome da tabela e as colunas de `colunas_modelo`.

**Cliente novo.** Uma migration com 15 a 25 tabelas, o cadastro em `cfg.fonte_carga` e `cfg.perfil_tabela` e, quando as colunas diferem do DW padrão, o `colunas_modelo` de cada fonte.

**Coluna nova no DW.** Um `alter table` para cada cliente que tiver a coluna.

**Prós.**
- Já está pronta e testada; trazer os clientes novos não exige mudar código.
- Cada cliente pode ter colunas diferentes. O DW não precisa ser padronizado antes.
- Uma carga com problema, uma tabela inchada ou uma migration errada afeta um cliente só.
- Tirar um cliente é `drop table`.
- O Postgres convive bem com mil tabelas; o limite não é técnico.

**Contras.**
- A manutenção cresce com o número de clientes. Com 30 clientes, são umas 600 tabelas, 30 migrations de entrada e N `alter table` para cada coluna nova.
- A diferença entre clientes fica escondida no `colunas_modelo`. Cada painel precisa tratar coluna que não existe ("Produtos por cliente" só aparece quando a coluna existe) e cada regra de indicador pode se comportar diferente de um cliente para outro.
- SQL dinâmico em toda consulta da API, com o nome da tabela montado na hora. Funciona e é seguro (`sql.Identifier`), mas é mais difícil de ler e de testar.
- Comparar clientes (média do setor, por exemplo) exige `union` de todas as tabelas.

**Esforço.** Nenhum para continuar; a cada cliente, uma migration e o cadastro.

### 2. Uma tabela grande por assunto, sem partição

**Como fica.** `mart.f_vendas` com todos os clientes, a coluna `_cliente_id` e índices que começam por ela.

```sql
create table mart.f_vendas (
    _cliente_id uuid not null references cfg.cliente (id),
    _empresa_id uuid,
    _linha      bigint not null,
    data        date not null,
    valor       numeric,
    custo       numeric,
    pedido      text,
    cliente     text,
    produto     text,
    -- … as demais colunas oficiais
    _data_snapshot date,
    primary key (_cliente_id, _linha)
);
```

**Colunas.** A tabela tem um conjunto só de colunas, as **colunas oficiais**. A carga traduz as colunas de cada cliente para elas, usando o mapa que hoje está no `colunas_modelo`. O que não tem coluna oficial é descartado ou vai para uma coluna `extras jsonb`.

**Carga.** A mesma de hoje: `delete … where _cliente_id = X` e `insert`. Cada cliente carrega sozinho, e dois clientes ao mesmo tempo não se bloqueiam.

**API.** Uma consulta fixa por painel (`from mart.f_vendas where _cliente_id = %s`), sem SQL dinâmico nem `colunas_modelo`.

**Prós.**
- Um modelo só para todos. Coluna nova = uma migration. Cliente novo = só cadastro, sem migration.
- O SQL da API fica fixo, mais simples de ler e de testar.
- Comparar clientes fica fácil.

**Contras.**
- **Exige padronizar as colunas** de todos os clientes antes de entrar.
- **Linhas mortas na tabela inteira.** Se um cliente com 2 milhões de linhas regrava tudo numa tabela de 60 milhões, sobram 2 milhões de linhas mortas espalhadas. O autovacuum só começa perto de 12 milhões de linhas mortas (20%) e, quando roda, varre os 60 milhões. Entre uma passagem e outra, a tabela e os índices crescem. Dá para regular por tabela (`autovacuum_vacuum_scale_factor`), mas o custo de varrer a tabela inteira continua.
- **Estatísticas misturadas.** O planejador vê a distribuição de todos os clientes juntos. Um cliente grande pode levar a um plano ruim para um pequeno.
- Tirar um cliente é um `delete` de milhões de linhas, com as mesmas linhas mortas.
- Um erro numa migration afeta todos os clientes de uma vez.

**Esforço.** Alto: colunas oficiais, carga que traduz colunas, reescrever as consultas dos painéis e o `GET /api/dados`.

É a opção que mais piora com o tempo. A sensação de que "fica pesado" vem daqui, e está certa.

### 3. Uma tabela grande por assunto, particionada por cliente ⭐ recomendada

**Como fica.** A mesma tabela da opção 2, dividida em uma filha por cliente:

```sql
create table mart.f_vendas (
    _cliente_id uuid not null,
    _empresa_id uuid,
    _linha      bigint not null,
    data        date not null,
    valor       numeric,
    -- … as demais colunas oficiais
    extras      jsonb,
    _data_snapshot date,
    primary key (_cliente_id, _linha)
) partition by list (_cliente_id);

-- uma por cliente, criada por função do banco (não por migration)
create table mart.f_vendas_pj_cortes
    partition of mart.f_vendas for values in ('<uuid da PJ>');
```

Para quem lê, existe só `mart.f_vendas`. Para o Postgres, cada cliente é uma tabela própria.

**Carga.** Igual à de hoje, só que dentro da filha do cliente:

1. `COPY` para a tabela temporária e tradução para as colunas oficiais;
2. `delete from mart.f_vendas where _cliente_id = X`. Com a poda, o Postgres mexe só na filha do cliente;
3. `insert` e commit.

As linhas mortas ficam só na filha do cliente. O autovacuum dela roda logo (20% de 2 milhões, não de 60) e varre só ela. Dois clientes carregando ao mesmo tempo mexem em tabelas físicas diferentes.

Mais tarde dá para trocar o `delete` por um `truncate` da filha, que não deixa linha morta nenhuma. Mas o `truncate` trava a filha até o commit, e uma consulta que não poda no planejamento (só com o filtro do RLS) espera por ele, mesmo sendo de outro cliente. Por isso a ordem seria: começar com `delete` + `insert` e passar ao `truncate` só se o vacuum virar problema, com todas as consultas da API já trazendo o cliente como valor.

**Cliente novo.** Uma função `mart.garantir_particao(tabela, cliente)` com `security definer`: o role `carga` não pode fazer DDL, mas pode chamar a função. A carga chama a função antes de gravar e a filha nasce sozinha. Sem migration.

**API.**
- Uma consulta fixa por painel.
- O RLS fica na mãe e vale para toda leitura feita por ela. As filhas ficam sem grant para o `api_site`, então ninguém lê uma filha direto.
- **Toda consulta traz `_cliente_id = %s` com o valor**, além do RLS. Assim a poda acontece no planejamento e a consulta abre uma filha só. A API já sabe o cliente da requisição. Como ela não usa prepared statements (`prepare_threshold: None`), cada consulta é planejada com o valor de verdade.
- O `GET /api/dados/<tabela>` passa a entregar as colunas oficiais. Hoje ele entrega as colunas com os nomes do DW, e o front lê por esses nomes, então essa mudança precisa ser combinada com o front.

**Coluna nova.** Uma migration na mãe vale para todas as filhas.

**Prós.**
- Tudo o que a opção 2 tem de bom: um modelo, consultas fixas, cliente novo sem migration, comparação entre clientes.
- Sem os problemas da opção 2: vacuum, estatísticas e índices por cliente.
- Tirar um cliente é soltar a filha (`detach`) e apagá-la, sem `delete` de milhões de linhas.
- Combina com a recarga só dos meses abertos (abaixo): o `delete` por data cai na filha certa, e mais tarde cada filha pode ser dividida por ano, se crescer muito.
- Supabase está no Postgres 17, que lida bem com partição, inclusive com algumas centenas de filhas.

**Contras.**
- **Exige as colunas oficiais**, igual à opção 2. É a maior parte do trabalho e depende de quanto o DW de cada cliente varia.
- A chave primária e todo índice único precisam incluir `_cliente_id`, por regra do Postgres.
- Uma consulta sem o valor do cliente funciona, mas abre todas as filhas e é mais lenta. Os testes precisam conferir isso.
- `garantir_particao` com `security definer` é uma função a mais com poder de DDL, que precisa ser restrita (só cria filha de tabela conhecida, com nome validado).
- A migração das tabelas de hoje para as novas leva algumas semanas e mexe na carga, na API e no `GET /api/dados`.

**Esforço.** Alto, mas pode ser feito uma tabela de cada vez, começando por vendas, enquanto as outras seguem na opção 1.

### 4. Um schema por cliente

**Como fica.** `mart_pj.f_vendas`, `mart_nilo.f_vendas`: nomes iguais, cada cliente com o seu schema.

**Carga.** Como hoje, no schema do cliente.

**API.** Ou monta o nome do schema em cada consulta (SQL dinâmico de novo), ou troca o `search_path` a cada requisição. A segunda forma é arriscada com o pooler em modo transação: um `search_path` esquecido numa conexão reaproveitada pode mostrar dado de outro cliente.

**Prós.**
- Nomes de tabela iguais para todos.
- Isolamento forte. Dá até para dar grants por schema.
- Tirar um cliente é `drop schema … cascade`.

**Contras.**
- Toda migration precisa rodar em N schemas: um laço em SQL, ou uma migration que gera outras.
- Colunas diferentes por cliente continuam possíveis, e por isso o problema do `colunas_modelo` continua.
- Comparar clientes exige `union`.
- Tem os custos da opção 1 e o risco do `search_path`, sem os ganhos da opção 3.

**Esforço.** Médio, sem ganho claro sobre as opções 1 e 3.

### Descartada: um projeto Supabase por cliente

Isola tudo, mas multiplica o custo do Pro, a configuração do login, as chaves e os jobs, e quebra a Gestão (o Admin BORD vê todos os clientes num lugar só). Não resolve nada que a opção 3 não resolva.

## O que tira o "começa às 9h, termina às 13h"

Estes pontos valem **para qualquer uma das opções** e têm mais efeito no tempo do que o formato da tabela.

### A. Um job por cliente, em paralelo

Hoje o `diario.yml` roda um snapshot com todas as tabelas e depois uma carga com todas as fontes. A troca:

- **Uma `matrix` por cliente** no `diario.yml`. Cada item da matriz roda o snapshot e a carga só daquele cliente. A lista de clientes e de tabelas pode vir de uma Variable em JSON ou do próprio `cfg.fonte_carga`.
- **A carga ganha um filtro** (`CLIENTE=<slug>`) e lê só as fontes daquele cliente. Cada execução continua gravando o seu `mart.carga`.
- **Concurrency por cliente:** duas execuções do mesmo cliente nunca ao mesmo tempo; de clientes diferentes, sim.
- **`max-parallel`** limita quantos clientes rodam juntos, para não esgotar a máquina do banco nem as conexões do pooler em modo sessão. O limite de conexões depende do tamanho da máquina; conferir no painel do Supabase.
- **`fail-fast: false`:** um cliente com erro não cancela os outros.
- Rodar de novo à mão passa a ser por cliente.

Com isso, o tempo total vira o do cliente mais lento, não a soma de todos. É também o que dá a independência que você quer: a carga de um cliente não depende de nenhum outro.

### B. Recarregar só os meses abertos

Nas tabelas de movimento (vendas, receitas, despesas, lançamentos), um mês fechado quase nunca muda. Regravar 2 milhões de linhas para trocar as 60 mil do mês corrente é a maior parte do custo.

- **Snapshot:** ler do Azure só a partir do início da janela (`where data >= …`). É aqui que mais se ganha: menos leitura no Azure, Parquet menor e envio menor.
- **Carga:** `delete … where _cliente_id = X and data >= <início>` e `insert` só do que veio. A `janela.py` já calcula o início.
- **Resumo mensal:** passa a gravar só os meses que vieram, sem apagar os outros.
- **Recarga completa uma vez por semana** (domingo, por exemplo). Nota cancelada ou lançamento corrigido em mês já fechado só aparece nela.
- **Nunca nas tabelas de posição** (a pagar, a receber, saldos, estoque): elas continuam inteiras todo dia. Um título antigo ainda aberto sumiria.

### C. Medir antes de mexer

Antes de decidir onde investir, separar quanto tempo cada etapa leva:

- **Carga:** `mart.carga_fonte` tem `inicio` e `fim` por fonte; dá para somar por cliente e por tabela.
- **Snapshot:** o tempo está nos logs do GitHub Actions. Meu palpite é que ler o Azure (`SELECT *`), gerar o Parquet e enviar pesa tanto quanto a carga.

### D. Tamanho da máquina do banco

O plano Pro vem com a máquina **Micro** (1 GB de RAM). `COPY`, índices e vacuum de milhões de linhas por dia sentem muito. Subir para Small ou Medium no período da carga, ou de vez, costuma ter mais efeito que qualquer mudança de formato. Conferir os preços no painel.

## Comparação

| | 1. Tabela por cliente | 2. Tabela única | 3. Única, particionada | 4. Schema por cliente |
|---|---|---|---|---|
| Carga de um cliente independente | sim | sim | sim | sim |
| Linhas mortas e vacuum | por cliente | na tabela inteira | por cliente | por cliente |
| Colunas diferentes por cliente | aceita | não | não | aceita |
| Cliente novo | migration | só cadastro | só cadastro (filha automática) | migration |
| Coluna nova | N `alter table` | 1 migration | 1 migration | N `alter table` |
| SQL da API | dinâmico | fixo | fixo | dinâmico ou `search_path` |
| Comparar clientes | `union` | direto | direto | `union` |
| Tirar um cliente | `drop` | `delete` grande | `detach` + `drop` | `drop schema` |
| Esforço para chegar lá | nenhum | alto | alto, por etapas | médio |

## Recomendação

**Opção 3 nas tabelas grandes de movimento, junto com os itens A e B.** Em etapas, para que cada uma já entregue algo:

1. **Agora, sem mudar o formato:** um job por cliente (A) e a medição (C). Os clientes novos entram na opção 1, como hoje. Isso já resolve a independência e boa parte do tempo.
2. **Logo depois:** recarga só dos meses abertos nas tabelas de movimento, com recarga completa semanal (B). Isso resolve o volume.
3. **Em seguida, tabela por tabela, começando por vendas:** definir as colunas oficiais, criar `mart.f_vendas` particionada, fazer a carga traduzir as colunas e trocar as consultas dos painéis. As tabelas pequenas de cadastro (`d_*`) podem ficar na opção 1 até valer a pena.

**Por quê.** As etapas 1 e 2 resolvem o tempo de carga com qualquer formato e com pouco código. A etapa 3 resolve o que vai pesar com 20 ou 30 clientes: as migrations por cliente, o `colunas_modelo` em cada painel e cada indicador se comportando diferente de um cliente para outro. A opção 3 é a única que faz isso sem os problemas de vacuum da tabela única.

**Quando não ir para a opção 3.** Se as colunas oficiais não puderem ser definidas por enquanto (o DW de cada cliente varia demais), fique na opção 1 com as etapas 1 e 2. O tempo e a independência ficam resolvidos; a manutenção continua crescendo com cada cliente.

## Perguntas para decidir

1. Quantos clientes devem entrar nos próximos meses, e de que tamanho, perto da Nilo Maia ou da PJ?
2. O DW (Azure SQL) segue um padrão de colunas, com exceções pontuais como a PJ, ou cada cliente é bem diferente?
3. O front usa o `GET /api/dados/<tabela>` com os nomes de coluna do DW em alguma tela? Se usa, a opção 3 muda esse contrato.
4. Quanto tempo leva hoje cada etapa (snapshot e carga) por cliente? A resposta vem do item C.
5. A recarga completa semanal basta para as correções em meses fechados, ou alguma tabela precisa ser recarregada inteira todo dia?
