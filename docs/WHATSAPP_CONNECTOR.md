# MemorIA v0.2 — conector de dispositivo vinculado

## Entrega e estado de aceitação

Serviço independente em `services/whatsapp-connector`, com sessão criptografada,
ingestão autenticada, fila durável e controle em **Ajustes → WhatsApp**. TXT,
REST, MCP, worker e motor de memória continuam usando a mesma base canônica.
O escopo é somente leitura: não há envio de mensagens nem download de mídia.

Pareamento real, restauração de sessão e cobertura histórica precisam ser
validados pelo titular em cada instalação. Fixtures não comprovam esses critérios.
A configuração isolada está no guia de [operação pessoal](PERSONAL_OPERATION.md).
Os consentimentos de WhatsApp e de IA são independentes.

## Dependências e estrutura

Node.js 24+, Baileys `7.0.0-rc14` fixado, `qrcode`, `pino`, Zod e os contratos
compartilhados do workspace; versões transitivas no `package-lock.json`.
Baileys é uma implementação não oficial e esta versão é uma release candidate.
Uma atualização requer repetir os testes de protocolo e a aceitação autorizada.
Referências: [release oficial do projeto](https://github.com/WhiskeySockets/Baileys/releases/tag/v7.0.0-rc14)
e [README da versão fixada](https://github.com/WhiskeySockets/Baileys/blob/v7.0.0-rc14/README.md).

| Área | Arquivos principais | Responsabilidade |
|---|---|---|
| Contrato | `packages/shared-types/whatsapp.ts` | Eventos, lease, métricas e status validados |
| Sessão | `src/authentication/state.ts`, `src/persistence/vault.ts` | Credenciais e Signal keys criptografadas, outbox atômico |
| Conexão | `src/connection/{socket,machine,manager}.ts` | Adaptador Baileys, estados, geração, retries e backpressure |
| Normalização | `src/normalization/messages.ts` | Identidade nativa, texto, tipos e metadados permitidos |
| Transporte | `src/transport/backend.ts`, `src/health/server.ts` | HTTP loopback autenticado e controle local |
| Backend | `backend/app/services/{whatsapp,persistence}.py` | Deduplicação, alterações e persistência canônica |
| API | `backend/app/api/routes/whatsapp.py` | Lease, ingestão e proxy público autenticado |
| Banco | `backend/migrations/versions/0004_whatsapp_connector.py` | Novas tabelas e campos, sem editar migrations anteriores |
| Interface | `apps/extension/src/components/WhatsAppConnection.tsx` | QR temporário, estado, métricas e desconexão |

Os caminhos `src/` desta tabela pertencem ao novo serviço. O restante da integração
inclui config, DTOs, worker, evidências, consulta, exclusão de conversa, Compose,
setup e testes dos respectivos módulos. Não existe um segundo motor de memória.

## Executar localmente

1. Faça backup da sua base antes de aplicar uma nova migration. Execute
   `python scripts/setup.py`: preserva arquivos existentes sem lê-los e cria
   `.env.connector` e uma chave aleatória separada em `.local/secrets/whatsapp.key`.
2. Mantenha `CONNECTOR_NETWORK_ENABLED=false` para validação local sem WhatsApp.
   O token `CONNECTOR_SECRET` deve ser diferente de `AUTH_SECRET`, com pelo menos
   32 caracteres. Nunca coloque esse token ou a chave na extensão.
3. Execute `npm ci --ignore-scripts` e `npm run build`.
4. Com Docker local iniciado: `docker compose --profile whatsapp up --build -d`.
   O perfil aplica a migration e inicia API, worker e conector. Sem o perfil,
   a importação TXT continua disponível.
5. Para vincular uma conta sob sua responsabilidade, habilite explicitamente
   `CONNECTOR_NETWORK_ENABLED=true` em `.env.connector`, recrie o serviço
   (`docker compose --profile whatsapp up -d --force-recreate whatsapp-connector`),
   abra **Ajustes** na extensão e clique **Conectar WhatsApp**. Escaneie o QR
   no telefone em **Dispositivos conectados**. O QR expira e não é salvo em disco.
6. Confira estado, captura e última sincronização; envie/receba mensagens apenas
   no seu próprio aplicativo e verifique as fontes no Assistente e MCP.
7. Use **Desconectar** e confirme para solicitar logout e apagar a sessão local.
   Se houver aviso de logout remoto não confirmado, remova o dispositivo também
   no telefone. Desligar o processo apenas interrompe a captura e preserva a sessão.

Fora do Docker, inicie migrations, API e worker conforme o README e execute
`npm run connector:start` na raiz. A API lê `.env` e `.env.connector`; o serviço
lê `.env.connector`. A porta de controle é **127.0.0.1:3001**, fixa. É necessário
um único processo e um único diretório de sessão por instalação. No Compose,
API e conector compartilham o namespace de rede; a porta não é publicada.

## Fluxo e invariantes

`Baileys → normalização → outbox criptografado → API interna → mensagem canônica
→ ImportJob/MessageWorkItem → worker existente → memória/embedding → REST/MCP`.

A porta exclusiva é adquirida antes de abrir o vault. O backend concede lease
com expiração e fencing crescente; ingestão exige instância, execução e fence
vigentes. O heartbeat renova o lease e uma geração invalida callbacks antigos.
O ACK só sai depois do commit, inclusive triggers diferidas. Falhas HTTP mantêm
os eventos no outbox; reinício recupera a sequência e reenvia com idempotência.

Identidade usa conta derivada do JID próprio, chat, ID nativo e direção. Nome não
é chave. PN/LID só são associados quando o evento fornece a relação; conflito
entre chats já independentes retorna 409 e exige decisão explícita. Importação
TXT não é fundida automaticamente com chats nativos por semelhança de texto.

Estados: `DISCONNECTED`, `INITIALIZING`, `QR_REQUIRED`, `CONNECTING`, `CONNECTED`,
`RECONNECTING`, `LOGGED_OUT`, `ERROR`. Falhas transitórias usam backoff
exponencial com jitter e teto de 60s. Logout, sessão inválida e restrições não
entram em loop de reconexão. Uma restrição exige desconexão explícita antes
de nova tentativa. Uma sessão previamente habilitada pode restaurar a intenção
de conectar no reinício; o flag de rede continua obrigatório.

## Mensagens, edições e exclusão

Texto, legendas, grupos, direção, referências de resposta, localização, contatos
e descrição de mídia são normalizados. Arquivos, URLs de download, chaves de mídia
e binários não entram no contrato. Tipo sem corpo permanece sem corpo; não se
inventa texto para mídia. Metadados ficam disponíveis junto das evidências.

A política escolhida pelo usuário é **apagar o texto anterior e derivados,
mantendo identificadores de controle**. Uma edição remove embedding e suportes
anteriores, substitui o conteúdo e enfileira a nova revisão no mesmo worker.
A data original permanece; o instante nativo da edição orienta a memória nova.
Revogação remove corpo/metadados e suportes; tombstones impedem ressurreição por
replay. Memórias sem suporte são apagadas. Memórias ainda apoiadas em outras
mensagens válidas preservam somente esse suporte. Jobs de revisões antigas não
reindexam texto removido. Uma resposta em andamento revalida suas evidências
antes de retornar para não citar uma revisão que mudou durante a chamada.

Não há arquivo de revisões no banco. Eventos fora de ordem ficam pendentes até
a mensagem original; revogação é terminal. Edições diferentes com o mesmo instante
nativo tornam a mensagem `conflicted`: texto, metadados e derivados são apagados,
o evento é confirmado e a fila continua com outras mensagens. A primeira edição
explícita pode substituir a mensagem original no mesmo segundo; outra edição
diferente nesse segundo é ambígua. Somente uma edição estritamente posterior pode
restabelecer o conteúdo ativo. O painel informa a quantidade de conflitos.
Colisões de ID de evento e identidades PN/LID incompatíveis continuam retornando
409. Excluir uma
conversa também suprime seus identificadores para impedir reimportação por replay.

O purge ocorre quando o evento chega e é aplicado. Eventos ainda não confirmados
podem conter conteúdo no outbox criptografado; offline não permite apagar de
imediato uma cópia remota. WAL, backups, páginas antigas e comportamento do SSD
não são apagamento forense. Uma política de backup precisa respeitar essa retenção.

## API, observabilidade e limites

API pública autenticada pelo token habitual:
`GET /api/v1/whatsapp/connection`, `POST /api/v1/whatsapp/connect`,
`POST /api/v1/whatsapp/disconnect`. A API faz proxy ao controle loopback e não
entrega segredos. Erros e logs evitam conteúdo, tokens, QR e JIDs.

Internamente, `CONNECTOR_SECRET` distinto protege `/internal/whatsapp/lease`,
`/lease/heartbeat`, `/lease/release`, `/connection/status`, `/connection`,
`/events`, `/sync/checkpoint`. Controle local: `/health`, `/connection`,
`/connect`, `/disconnect`, também autenticado e sem requisições com Origin.

Métricas incluem recebidas, descartadas, duplicadas, falhas, reconexões, pendências,
tempo conectado, última sincronização, latência e lacuna conhecida. O total
confirmado e a última sincronização vêm do banco; contadores de execução reiniciam
com o processo. Limites padrão: outbox 10.000 eventos, vault 50 MB, 256 tarefas
de callback pendentes, 500.000 registros de controle e 5.000 eventos pendentes
no backend. Excesso falha de forma explícita; não descarta a fila durável em silêncio.

O histórico do dispositivo vinculado é **parcial**, sem garantia de completude ou
recuperação de tudo que ocorreu offline. TXT continua sendo complemento. Uma
queda antes da persistência do callback pode perder eventos; a indicação de
lacuna só cobre falhas conhecidas. Não há garantia de captura exatamente uma vez
na origem; há persistência idempotente dos eventos entregues.

**UNKNOWN / validação necessária:** comportamento real de mudanças da identidade
própria PN/LID, variantes nativas sem timestamp e disponibilidade de histórico por
conta. Exclusão em massa de chat sem chaves individuais é contabilizada como
não suportada; não inferimos revogação de cada mensagem. Fila em conflito exige
diagnóstico local; não existe botão de descarte automático. Não escale o serviço
nem compartilhe o vault em namespaces diferentes nesta versão.

## Verificação e aceite autorizado

As suítes cobrem normalização, Signal keys, vault/restart, quotas, estados,
reconexão/restrições, API autenticada, deduplicação concorrente, ordem inversa,
purge e trabalho obsoleto. O teste `tests/backend.integration.test.ts` requer
`CONNECTOR_TEST_BACKEND_URL`, `CONNECTOR_TEST_SECRET`, `CONNECTOR_TEST_API_TOKEN`
apontando para uma API local com banco descartável e providers mock. Sem essas
variáveis ele é explicitamente pulado. Não usa conta real.

O aceite final deve vincular uma conta autorizada, verificar captura recebida e
enviada/grupo, consulta REST e MCP com fonte literal, edição/revogação sem texto
antigo, reinício sem novo QR, duplicação de eventos, reconexão após interrupção,
logout remoto e ausência de envio pelo conector. Registre resultados e lacunas
em `docs/VALIDATION.md`; não marque como comprovado antes desse ensaio.

## Troubleshooting e recuperação

| Sintoma | Diagnóstico e ação |
|---|---|
| Conector não configurado/indisponível | Execute o setup, confira o perfil `whatsapp` e os healthchecks. A API precisa do mesmo CONNECTOR_SECRET do conector, distinto do token público. |
| Rede desabilitada | É o padrão do ambiente geral; a preparação pessoal habilita a rede. Habilite o flag somente para a conta cujo uso foi autorizado e recrie o serviço. |
| QR expirou | Aguarde o próximo QR/estado; se necessário desconecte e inicie uma nova sessão. Não reutilize uma captura antiga. |
| Inicialização falha | Verifique chave de exatamente 32 bytes, vault correspondente, permissões e lease. Uma chave diferente não deve conseguir abrir a sessão. Não substitua a chave enquanto precisar recuperar o vault. |
| Outro processo/LEASE_LOST | Pare o processo antigo, mantenha um único dono do volume e aguarde a expiração do lease antes de iniciar novamente. Não altere fences manualmente. |
| SESSION_RESTRICTED | Desconecte, investigue o estado da conta no aplicativo oficial e só faça nova tentativa após resolver a restrição; não há retry automático desse caso. |
| Falha HTTP temporária | A fila permanece criptografada; restaure a API e aguarde o backoff. Não apague o volume para resolver uma indisponibilidade transitória. |
| BACKEND_REJECTED_EVENT / limite | Preserve a fila, inspecione códigos seguros e os registros locais. Conflitos de identidade/revisão exigem decisão explícita. Capacidade pode ser ajustada dentro dos limites documentados, sem descartar eventos. |
| LOCAL_BACKPRESSURE / EVENT_OR_STORAGE_FAILURE | Captura é interrompida e a lacuna fica sinalizada. Corrija espaço/permissões/capacidade antes de retomar; confira mensagens faltantes com TXT. |
| REMOTE_LOGOUT_UNCONFIRMED | A credencial local foi removida; remova também o dispositivo pelo telefone para confirmar a revogação remota. |

Para backup, preserve vault e chave em locais separados e protegidos. Restaurar
um backup pode restaurar conteúdo anterior à exclusão; não o reintegre sem aplicar
a política de retenção e reconciliação. Uma instalação nova não deve compartilhar
simultaneamente a mesma sessão com a anterior.

## Inventário das alterações

Novos: toda a pasta `services/whatsapp-connector` (configuração npm/TS/lint/teste,
Dockerfile, 11 módulos de runtime e dois arquivos de teste),
`packages/shared-types/whatsapp.ts`, `backend/app/api/routes/whatsapp.py`,
`backend/app/schemas/whatsapp.py`, `backend/app/security/internal.py`,
`backend/app/services/persistence.py`, `backend/app/services/whatsapp.py`,
`backend/migrations/versions/0004_whatsapp_connector.py`,
`backend/tests/test_whatsapp_connector.py`,
`apps/extension/src/components/WhatsAppConnection.tsx` e seu teste,
`.dockerignore` e este documento.

Modificados: manifests npm e lockfile; manifest da extensão;
`backend/pyproject.toml`; `backend/tests/conftest.py`; módulos backend
`config.py`, `main.py`, `api/routes/v1.py`, `core/memory/pipeline.py`,
`core/retrieval/search.py`, `integrations/providers.py`, `models/entities.py`,
`schemas/domain.py`, `security/body_limit.py`, `services/imports.py`,
`services/knowledge.py`, `workers/runner.py`; contratos em
`packages/shared-types/index.ts`; interface em `src/sidepanel/Settings.tsx`,
`src/components/Evidence.tsx`, `src/services/api.ts`, `src/styles/app.css`;
`docker-compose.yml`, `scripts/setup.py`, `.env.example`, `.gitignore`, README
e documentos ARCHITECTURE, DATABASE, MEMORY_ENGINE, API, SECURITY e VALIDATION.
Builds e capturas em `.local` são artefatos ignorados, com dados sintéticos.


## Aceite operacional v0.2

A matriz de auditoria, isolamento do ensaio e roteiro humano estão no [checklist de aceite](ACCEPTANCE_CHECKLIST.md). A release não está aprovada enquanto os critérios obrigatórios estiverem pendentes. As migrations 0001–0004 são preservadas; ensaios não usam volumes de dados pessoais.
