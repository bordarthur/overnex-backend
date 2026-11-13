# Convite de usuário pela Gestão: onde guardar a chave secreta do Supabase

Documento de decisão. **Decidido em 13/11/2026: opção E (fila de convites, enviada por um job) e convite sem empresa aceito.** Implementado no mesmo dia; como ficou, em [api/README.md](../api/README.md#convite-pela-gestão-fila--job). O texto abaixo é o da proposta, como foi apresentado.

## O que precisa ser decidido

Hoje só a equipe BORD cria acessos ao Overnex, por um comando rodado na máquina de quem cadastra (`app/cli/convidar_usuario.py`). O pedido é que o Admin BORD passe a convidar e reenviar convites pela tela da Gestão.

Para mandar um convite, é preciso criar o usuário no Supabase Auth, e isso só se faz com a **chave secreta** do Supabase. Essa chave dá acesso total ao projeto: cria, apaga e assume qualquer login, e lê os arquivos de dados de todos os clientes no Storage, por fora das regras de acesso do banco.

Por isso ela fica hoje só em dois lugares: no comando de cadastro e nos jobs diários (segredos do GitHub). A API publicada não a tem, de propósito. A decisão é: **onde essa chave fica para o convite funcionar pela tela.**

O contrato que o front propôs (resposta na hora, com o erro pronto) **não é uma restrição**: ele pode mudar se a opção escolhida pedir.

## O que vale para qualquer opção

- **A conexão administrativa do banco continua fora da API.** O comando a usa só porque a API não pode inserir pessoas. Uma migration nova resolve isso com uma função que só roda para o Admin BORD; a API segue com o usuário de banco restrito de hoje.
- **As regras do cadastro não mudam:** mesmos perfis, Admin BORD só com e-mail da BORD, e nada fica pela metade se algo falhar.
- **Só o Admin BORD convida,** e todo convite fica registrado com quem o fez.
- **O comando continua existindo.**
- **Um limite que nenhuma opção tira:** quem invadir a API consegue pedir convites como se fosse um Admin BORD. As opções diferem no que o invasor ganha além disso.

## As opções

### A. Chave no ambiente da API

**Como fica.** A chave entra como segredo no ambiente da API, numa configuração separada que só as duas rotas de convite leem. O convite sai na hora e a tela mostra o resultado na mesma resposta.

**Riscos.**
- Quem invadir o processo da API passa a ter o que a chave dá: assumir qualquer login, inclusive o de Admin BORD, e ler os dados brutos de todos os clientes.
- É a única opção que põe a chave no serviço exposto à internet.

**Como reduzir.** Chave própria para a API (pode ser revogada sem parar os jobs), guardada como segredo do Container Apps e limite de convites por administrador.

**Custos adicionais.**
- Infraestrutura: nenhum.
- Desenvolvimento: o menor. Uma migration, duas rotas, uma variável de ambiente.
- Operação: uma chave a mais para girar.

### B. Serviço separado só para o convite

**Como fica.** Um segundo serviço pequeno guarda a chave: outra Container App no Azure ou uma Edge Function do Supabase. A API repassa o pedido com a identificação do administrador, e o serviço confere de novo antes de convidar. O convite sai na hora.

**Riscos.**
- A chave fica fora da API, mas num serviço novo que também recebe chamadas pela rede e precisa ser protegido.
- Mais uma peça que pode cair: sem ela, o convite pela tela para.

**Custos adicionais.**
- Infraestrutura: um serviço a mais. Pelo volume (poucos convites por mês), o valor tende a ficar na faixa gratuita ou perto de zero; conferir a tabela de preços.
- Desenvolvimento: médio. Segundo deploy e a comunicação entre os dois serviços. Se for Edge Function, o código é em TypeScript, fora do padrão do projeto.
- Operação: dois lugares para publicar, configurar e monitorar.

### C. Chave guardada no banco

**Como fica.** A chave fica no cofre do Supabase (Vault), e uma função do banco, que só roda para o Admin BORD, chama o serviço de login. Há duas formas: com a extensão `pg_net`, o convite sai em segundo plano e o resultado aparece depois; com a extensão `http`, sai na hora. A disponibilidade da `http` no projeto precisa ser conferida no painel.

**Riscos.**
- A chave não sai do banco: a API só consegue chamar a função.
- As regras do cadastro passam a existir em dois lugares (no comando, em Python, e na função, em SQL) e podem divergir.
- Na forma imediata, o banco fica esperando uma chamada externa dentro da transação; se o serviço de login demorar, a conexão fica presa.

**Custos adicionais.**
- Infraestrutura: nenhum.
- Desenvolvimento: médio a alto. A lógica do convite é reescrita em SQL.
- Operação: a função não roda no Postgres local usado nos testes (falta o Vault e a extensão); só dá para testar de verdade no Supabase.

### D. Sem a chave: cadastro aberto com link por e-mail

**Como fica.** A API pede ao Supabase um link de acesso que cria o usuário, com a chave pública que ela já tem.

**Riscos.**
- Exige abrir o cadastro público no Supabase. Qualquer pessoa com a chave pública (que vai no front) faz o projeto mandar e-mails a qualquer endereço: abuso, estouro do limite de envio e risco para a reputação do domínio.
- Não dá para apagar um login criado por engano, porque isso exige a chave secreta.

**Custos adicionais.**
- Infraestrutura: nenhum.
- Desenvolvimento: alto. Muda como o login é ligado ao cadastro da pessoa.
- Operação: monitorar abuso do envio de e-mails.

### E. Fila de convites, enviada por um job (recomendada)

**Como fica.** A tela grava o pedido de convite no banco, e a pessoa aparece na lista como "Convite na fila". Um job no GitHub Actions, onde a chave já está hoje, lê a fila e manda os convites com a mesma lógica do comando. Depois a situação vira "Convite enviado" ou "Convite não enviado", com o motivo.

O job pode ser acionado de duas formas, que se somam:
- **na hora**, pela API, com um token do GitHub que só permite disparar esse job. O convite sai em cerca de um minuto;
- **de tempos em tempos**, como rede de segurança (por exemplo, de hora em hora no horário comercial).

**Riscos.**
- A chave não muda de lugar: nenhum serviço novo passa a tê-la. É a única opção que mantém a regra atual do projeto.
- O token do GitHub que a API guardaria é pouco poderoso: dispara jobs do repositório, não lê segredos nem dados.
- O envio depende do GitHub Actions. Se ele atrasar ou cair, o convite fica na fila (e o comando continua disponível).

**Custos adicionais.**
- Infraestrutura: minutos do GitHub Actions. Acionado na hora, cada convite gasta cerca de um minuto. Rodar a cada poucos minutos o dia inteiro, sem o acionamento na hora, passaria da cota gratuita de um repositório privado; por isso a recomendação é acionar na hora e deixar a rede de segurança espaçada. Conferir a cota do plano.
- Desenvolvimento: médio. Uma migration (a fila), as duas rotas, o job (que reaproveita o comando) e o acionamento.
- Operação: acompanhar convites que ficaram na fila ou falharam; a própria lista da Gestão mostra.

**O que muda na tela em relação ao que o front propôs.**
- Depois de convidar, o passo final diz "Convite na fila: sai em instantes", não "Convite enviado".
- Erros conferidos na hora continuam aparecendo na hora: e-mail inválido, e-mail que já tem acesso, perfil, empresa e tempo de sessão.
- Erros do envio (limite de e-mails do Supabase, serviço de login fora do ar) aparecem depois, na situação da pessoa, com um botão para tentar de novo.

## Comparação

| | A. Chave na API | B. Serviço separado | C. Chave no banco | D. Sem a chave | E. Fila + job |
|---|---|---|---|---|---|
| Chave em lugar novo | Sim, na API | Sim, no serviço novo | Sim, no banco | Não existe | Não |
| Invasor da API assume logins e lê dados brutos | Sim | Não | Não | Não | Não |
| Convite sai | Na hora | Na hora | Na hora ou depois | Na hora | Em ~1 minuto |
| Erro do envio aparece | Na hora | Na hora | Na hora ou depois | Na hora | Depois, na lista |
| Serviço novo para manter | Não | Sim | Não | Não | Não (um job a mais) |
| Esforço de desenvolvimento | Baixo | Médio | Médio a alto | Alto | Médio |
| Custo mensal a mais | Nenhum | Baixo, a conferir | Nenhum | Nenhum | Minutos do GitHub Actions, a conferir |

## Recomendação

**Opção E, a fila com o job.**

Sem a obrigação de responder na hora, ela é a que menos mexe na segurança: a chave fica onde já está, a regra "chave secreta só no comando e nos jobs" continua valendo, e o job reaproveita a lógica do comando em vez de reescrevê-la. O preço é o convite sair em cerca de um minuto e os erros de envio aparecerem na lista, não na resposta. Para um cadastro que acontece poucas vezes por mês, é uma troca pequena.

**Se o convite precisar sair na hora,** a segunda escolha é a opção A com os três cuidados dela: é a mais simples, ao custo de pôr a chave no serviço exposto. A opção C imediata protege melhor a chave que a A, mas duplica as regras em SQL e não dá para testar fora do Supabase. A opção B só compensa se já houver motivo para um segundo serviço. A opção D fica descartada, porque abre o cadastro ao público.

## Segunda decisão: convite sem empresa marcada

O mockup deixa convidar sem nenhuma empresa (mostra o aviso e envia assim mesmo); o comando exige pelo menos uma para quem não é Admin BORD.

| | Aceitar (recomendada) | Recusar |
|---|---|---|
| Como fica | A pessoa recebe o convite e aparece na lista como "Sem empresa" até alguém marcar uma. | A tela mostra "Marque pelo menos uma empresa. Sem empresa, a pessoa entra e não vê nada." |
| A favor | É o que a edição pela Gestão já permite; a situação "Sem empresa" já aparece no filtro de atenção. | A pessoa nunca entra e encontra a tela vazia. |
| Contra | Quem entrar antes de ter empresa vê só o aviso para falar com a BORD. | Duas regras para o mesmo cadastro: o convite recusa o que a edição aceita. |

## Respostas necessárias

1. Onde fica a chave: A, B, C, D ou E?
2. Se for a E: o convite em cerca de um minuto, com o erro de envio aparecendo depois na lista, serve?
3. Convite sem empresa: aceitar ou recusar?
