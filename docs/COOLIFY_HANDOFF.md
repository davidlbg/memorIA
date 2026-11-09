# MemorIA no Coolify: preparação e ensaio

Escopo autorizado em 09/11/2026: preparar e ensaiar em ambiente isolado. A troca de produção depende de uma segunda aprovação. Este documento não autoriza executar os comandos de corte.

## Contrato da primeira troca

- Manter a aplicação 0.2.0 e as imagens locais já em uso; nenhum build, pull de aplicação ou atualização de esquema durante a troca.
- O serviço `migrate` do novo Compose apenas consulta `alembic_version` e exige `0009`. Não executa migrations.
- Preservar Caddy, certificados, DNS, `memoria.home.arpa` e a publicação atual em `0.0.0.0:58003`. Coolify continua acessível somente pelo túnel SSH existente.
- Usar os volumes externos existentes `whatsapp-memory-personal_memory_data` e `whatsapp-memory-personal_whatsapp_session`, sem renomear, copiar sobre eles ou removê-los.
- Somente uma instância de PostgreSQL pode abrir o volume do banco; somente um conector pode abrir a sessão do WhatsApp. Nunca iniciar a nova stack de produção enquanto a antiga estiver ativa.
- Configurações e chaves continuam em arquivos privados no servidor; não entram no Git. Não sobrescrever os arquivos atuais em `.local/personal/`.
- A primeira troca muda o gerenciador, não a versão da aplicação. Atualizações futuras e migrations precisam de plano separado.

## Compose e parâmetros

`infrastructure/coolify.compose.yml` é independente dos dois Compose atuais. Não deve ser combinado com eles. As variáveis `MEMORIA_*` são obrigatórias; nomes de volumes têm verificações adicionais no ambiente dos serviços porque o parser do Coolify não aceita a sintaxe `:?` no nome externo.

| Variável | Origem no corte, após aprovação |
| --- | --- |
| `MEMORIA_BACKEND_IMAGE` | Imagem local do backend, worker e indexer, com ID conferido |
| `MEMORIA_CONNECTOR_IMAGE` | Imagem local do conector, com ID conferido |
| `MEMORIA_DB_IMAGE` | Mesma imagem pgvector/PostgreSQL 17, com ID conferido |
| `MEMORIA_AUTH_ENV_FILE` | Caminho absoluto do `auth.env` original |
| `MEMORIA_RUNTIME_ENV_FILE` | Caminho absoluto do `runtime.env` original |
| `MEMORIA_DB_ENV_FILE` | Arquivo privado novo com os MESMOS parâmetros PostgreSQL atuais, sem alterar o banco |
| `MEMORIA_DATABASE_URL` | Mesma identidade de banco, host `db`; valor protegido no Coolify |
| `MEMORIA_AI_KEY_FILE` | Caminho absoluto da chave original de IA |
| `MEMORIA_WHATSAPP_KEY_FILE` | Caminho absoluto da chave original da sessão |
| `MEMORIA_DB_VOLUME` | `whatsapp-memory-personal_memory_data` |
| `MEMORIA_SESSION_VOLUME` | `whatsapp-memory-personal_whatsapp_session` |
| `MEMORIA_BIND_IP` / `MEMORIA_HOST_PORT` | `0.0.0.0` / `58003` |
| `MEMORIA_NETWORK_INTERNAL` | `false` em produção para preservar acesso aos provedores e WhatsApp; `true` no ensaio |

No Coolify 4.7.2: Docker Compose Raw, sem domínio/proxy, sem conexão a redes predefinidas, sem auto-deploy e sem previews. O comando de build personalizado deve somente verificar as imagens locais:

```sh
docker image inspect --format {{.Id}} whatsapp-memory-local-backend:0.2.0 whatsapp-memory-local-connector:0.2.0 pgvector/pgvector:0.8.1-pg17
```

Não configurar comando de start personalizado: o start Raw padrão roda no host e encontra os arquivos privados absolutos; comandos dentro do helper não encontram esses caminhos. Configurar prazo de parada no próprio Coolify em 90 segundos, além do Compose. `init: true` encaminha os sinais de parada aos processos Python.

## Ambiente de ensaio

- Diretório privado: `/opt/cortex/rehearsals/memoria-coolify-20261109`.
- Aplicação Coolify: `memoria-rehearsal-20261109`, UUID `oar5k3e6xxfctdpurywxcfue`, ambiente `rehearsal`.
- Volumes: `memoria_rehearsal_db_20261109` e `memoria_rehearsal_session_20261109`.
- Publicação do backend somente em `127.0.0.1:58013`; banco e conector sem portas publicadas.
- Ambas as redes da aplicação são internas. Provedores mock, dimensões 1024, IA externa desativada, conector sem rede e sem credenciais reais. Chaves de teste próprias.
- Banco `memory_rehearsal`: fixtures sintéticas para testar aplicação. Banco separado `restore_rehearsal`: restauração de backup existente para verificar recuperabilidade; nenhum processo da aplicação é apontado para essa cópia.
- Fonte Git temporária contém somente o Compose. É servida em container na rede privada do Coolify, sem publicação de porta no host; será parada ao terminar o ensaio.
- O candidato ainda não foi publicado no GitHub. O ensaio usa o pipeline Git/Compose do Coolify com fonte local; o checkout da futura revisão pelo GitHub continua sendo uma verificação anterior ao corte.

## Sequência de corte proposta — NÃO EXECUTADA

1. Publicar e revisar a revisão candidata no repositório autorizado; conferir acesso pela integração GitHub somente leitura. Fixar o SHA aprovado no recurso de produção, sem auto-deploy. Se a versão original tiver mudado, repetir o ensaio.
2. Confirmar que os provedores efetivos não dependem do Ollama opcional da rede antiga; se dependerem, bloquear o corte até existir uma estratégia de rede aprovada e ensaiada. Conferir novamente a publicação da porta: `0.0.0.0:58003` foi observada no Cortex por alteração local do Compose; o arquivo versionado usa loopback. Qualquer divergência bloqueia o corte, sem ampliar a exposição automaticamente. Criar arquivos privados novos com parâmetros do banco e variáveis do Coolify. Validar IDs das imagens, volumes, permissões, redes, portas e Compose renderizado. Exigir revisão `0009`. Fazer nova cópia de segurança do Coolify e dos arquivos de configuração.
3. Iniciar janela de manutenção. Parar primeiro o conector antigo, depois backend, worker e indexer. Aguardar a liberação de leases e transações. Copiar a sessão somente após parar o conector; guardar junto com a chave original em backup protegido, sem mostrá-la.
4. Fazer dump consistente final do banco ainda ativo, conferir seu término e então parar somente o container PostgreSQL antigo. Registrar contagens e identificadores para comparação. Os containers antigos ficam preservados, parados.
5. Iniciar manualmente o recurso Coolify nos mesmos volumes e na mesma porta 58003. O novo conector começa somente depois de o backend ficar saudável. Não permitir que os dois gerenciadores iniciem a mesma stack ao mesmo tempo.
6. Conferir autenticação, banco `0009`, contagens do marco final, listagens ou buscas com consulta vazia e leitura por ID (sem gerar embeddings), configurações de IA descriptografáveis, acesso pelo domínio e extensão. Conferir abertura da sessão sem pedir pareamento novo e ingestão sem duplicação. Não disparar mensagens ou chamadas pagas como teste sem autorização.
7. Adaptar o backup agendado, que hoje usa nomes antigos de containers, para os novos nomes/labels; criar uma versão nova do script e preservar a anterior. Executar e validar o primeiro backup após a troca. Até isso passar, manter manutenção e possibilidade de retorno.
8. Encerrar manutenção somente depois das validações. Não remover stack antiga, imagens, redes, volumes ou backups nessa etapa.

Exemplo de parada seletiva da stack original, SOMENTE durante corte aprovado, a partir de `/opt/cortex/applications/memoria` e usando o mesmo ambiente/projeto original:

```sh
docker compose -p whatsapp-memory-personal -f docker-compose.yml -f infrastructure/personal.compose.yml --profile whatsapp stop --timeout 90 whatsapp-connector
docker compose -p whatsapp-memory-personal -f docker-compose.yml -f infrastructure/personal.compose.yml --profile whatsapp stop --timeout 90 backend worker indexer
# Após backup final e conferência dos escritores:
docker compose -p whatsapp-memory-personal -f docker-compose.yml -f infrastructure/personal.compose.yml --profile whatsapp stop --timeout 90 db
```

Não usar `down`, `-v`, prune ou remover recursos para realizar o corte. Usar os procedimentos atuais do projeto para fornecer seus arquivos de ambiente, sem exibi-los.

## Retorno ao gerenciador anterior

Se qualquer validação falhar: parar primeiro o conector novo enquanto o backend ainda responde, depois backend/worker/indexer novos e por último o PostgreSQL novo. Conferir liberação dos volumes e prazo das leases. Usar `docker start` nos containers antigos preservados, sem recriar ou executar o migrate original: banco primeiro (aguardar `pg_isready`), backend depois (aguardar health autenticado), worker/indexer em seguida e conector por último. Conferir a disponibilidade do namespace de rede original antes de iniciar o conector. Como imagem e esquema são os mesmos, reutilizar os volumes atuais preserva também as gravações posteriores ao corte. Não restaurar um dump automaticamente, pois isso apagaria gravações mais recentes. Recuperação por backup é um procedimento separado, somente em caso de dano comprovado e com decisão explícita sobre perda de dados.

A etapa de parada dos containers antigos pode exigir até 90 segundos por serviço. Reservar inicialmente 15–20 minutos de manutenção para backup, troca e validações, sem prometer indisponibilidade zero. Durante o intervalo desconectado do WhatsApp, existe risco de lacunas nos eventos; não há garantia de histórico completo do provedor.

## Evidências e pendências

Resultados finais do ensaio e decisões de compatibilidade estão registrados abaixo neste documento. Evidências detalhadas ficam no diretório privado `evidence/` do ensaio. Elas não devem ser publicadas: a cópia restaurada contém dados pessoais e os arquivos operacionais incluem segredos de teste.

Pendente antes do corte: segunda aprovação, revisão publicada no GitHub, conferência de drift da produção, backup final com sessão parada, configuração privada final e adaptação do backup aos novos nomes. Não ativar agendamento global do Coolify nem auto-deploy como parte desta primeira troca.

## Resultado do ensaio — 09/11/2026

- Quatro implantações Raw Compose concluídas no Coolify, incluindo recriações com os mesmos volumes. A última revisão do Compose na fonte temporária é ecef615; essa fonte contém somente o arquivo de implantação, não é o SHA da aplicação completa.
- O ensaio usou exatamente os IDs de imagem já presentes: backend e4f4e07e6b1e, conector 738c76d3032f, PostgreSQL 3e8b3adfd27b. Não houve build ou pull das imagens da aplicação; foi baixado o helper oficial do Coolify, e git-daemon foi instalado somente no container temporário da fonte.
- Backup existente restaurado com pg_restore sem erro em restore_rehearsal. Revisão 0009 e vetores de 1024 dimensões nas duas tabelas verificados. Não representa um backup final da produção nem uma cópia da sessão real.
- Fixtures: duas importações concorrentes do mesmo TXT resultaram em uma conversa e duas mensagens; worker e indexer produziram duas memórias e dois vetores em cada tabela. Os identificadores e contagens persistiram após redeploy e retorno ao runtime independente.
- HTTP autenticado 200 e sem autenticação 401, buscas sintéticas, modelo mock, dimensões efetivamente armazenadas, ausência de configuração persistida de IA e configuração efetiva da API conferidos.
- Conector saudável sem credenciais reais, rede desativada, sem QR e recusando /connect com 409. Uma única identidade de conector persistiu no banco durante as recriações. O cofre sintético foi reaberto após retorno usando a mesma chave de teste.
- Redes internas inspecionadas antes de executar fixtures; teste de saída TCP recusado. O retorno independente também foi executado em namespace sem rede externa, sem portas publicadas.
- Testes negativos: ausência de cada variável de volume impede validar o Compose; configuração de IA persistida ou provedor efetivo incompatível impede importar; execução otimizada do verificador é recusada. Em banco descartável de revisão divergente, o antigo assert sob -O falhou como barreira e o novo sys.exit rejeitou a revisão.
- Backend: 154 testes aprovados, sem skips, incluindo banco/concorrência em memory_test através de túnel local temporário. Ruff aprovado e mypy aprovado nos 38 arquivos da aplicação e no verificador. Permanecem cinco avisos de depreciação da suíte.
- Nenhum arquivo TypeScript ou código do produto foi alterado; as verificações e builds dos workspaces JavaScript não foram repetidos nesta operação. O primeiro handoff usa as imagens existentes, deliberadamente sem rebuild.
- Estado final: aplicação de ensaio, inicializador, runtime de retorno e fonte Git parados; volumes, containers, arquivos privados e histórico preservados. O túnel temporário dos testes foi encerrado. Para novo deploy do ensaio, a fonte Git temporária precisa ser reativada e seu endereço privado reconferido.
- Os seis containers originais conservaram IDs, horários de início e contadores de reinício. A API original respondeu 200 autenticada; o domínio original respondeu 401 sem autenticação. A publicação original observada é 0.0.0.0:58003; o painel continua limitado ao acesso local/túnel.

### Decisões registradas

1. Manter imagens e esquema atuais em vez de compilar ou migrar durante o handoff: separa a troca de gerenciador da troca de versão. Se houver drift, a validade deste ensaio termina e ele deve ser repetido.
2. Usar fonte Git local temporária porque a integração concedida é somente leitura e o candidato ainda não foi publicado: exercita Git/Raw Compose, mas não comprova o checkout dessa futura revisão pelo GitHub; isso continua bloqueando o corte até sua validação.
3. Separar banco restaurado e banco de fixtures: evita carregar configurações pessoais de provedores no runtime de teste. A recuperação de configurações criptografadas e da sessão real continua sendo uma validação controlada do corte.
4. Adicionar init aos serviços Python e prazo de parada de 90 segundos também no Coolify: o ensaio observou processos Python como PID 1 aguardando o timeout de parada. O runtime com init passou por novo redeploy e pelos testes; transações interrompidas continuam sujeitas a rollback do PostgreSQL.
5. Manter arquivos privados no host e usar o start Raw padrão, com build restrito à inspeção de imagens locais: evita reconstrução e mantém os caminhos atuais. Depende da existência e permissões desses arquivos em Cortex.
6. Parar o ensaio sem limpeza automática do Docker, com dockerCleanup=false e removeContainers=false na ação do Coolify. Não executar limpeza global como parte da migração.

Revisão independente local: nenhum achado crítico. Achados importantes sobre a barreira de esquema, configuração efetiva dos provedores, buscas pagas, ordem do retorno e diferenças de portas/rede foram corrigidos ou transformados em condições explícitas que bloqueiam o corte. A revisão estática não inspecionou segredos nem produção; as conferências operacionais acima foram executadas separadamente. A observação sobre dimensões/modelo foi atendida nas duas tabelas.

Limitações: mocks não comprovam qualidade semântica nem conectividade do WhatsApp/OpenAI. Não houve pareamento real, mensagem enviada ou chamada paga nesta operação. A ausência de Ollama entre os containers ativos não substitui a conferência dos provedores efetivos antes do corte.
