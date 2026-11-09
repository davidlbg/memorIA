# MemorIA

MemorIA permite consultar conversas do WhatsApp em um chat no painel lateral do Chrome. As respostas incluem mensagens originais como fontes, para recuperar contexto sem precisar reler uma conversa inteira.

O projeto reúne uma extensão Manifest V3, uma API FastAPI, PostgreSQL com pgvector e um conector de dispositivo vinculado. O modelo pesquisa mensagens, contatos e linhas do tempo antes de responder. Perguntas gerais buscam contexto; perguntas específicas devem se limitar às evidências relevantes.

**Versão: 0.2.0.** Projeto em evolução. Respostas podem ser incompletas ou incorretas; confira as fontes. A captura não garante acesso a todo o histórico da conta.

## Recursos

- Chat com pesquisa por ferramentas, filtros e fontes verificáveis.
- Importação TXT do WhatsApp com prévia, formato de data e fuso horário.
- Captura de eventos entregues a um dispositivo vinculado, após pareamento por QR Code.
- Busca textual/vetorial, memórias derivadas e contexto de períodos longos.
- Invalidação de textos antigos e derivados ao aplicar edições e revogações capturadas.
- Configuração nativa da chave OpenAI, sem colocá-la no código.
- Orçamento opcional compartilhado no backend e acompanhamento de custos estimados.
- Temas claro/escuro, navegação inferior e interface adaptada ao painel lateral.
- REST autenticado e MCP somente leitura.

O conector não envia mensagens, não baixa mídias e não promete recuperar todo o histórico. A importação TXT permite incluir conversas antigas disponíveis em exportações.

## Arquitetura

```mermaid
flowchart LR
  UI[Extensão Chrome] --> API[API FastAPI]
  MCP[Cliente MCP] --> API
  WA[WhatsApp vinculado] --> C[Conector Baileys]
  C --> API
  API --> DB[(PostgreSQL + pgvector)]
  W[Worker de memórias] --> DB
  I[Indexador] --> DB
  API --> AI[Provedor de IA]
  W --> AI
  I --> AI
```

| Local | Responsabilidade |
|---|---|
| `apps/extension` | Chat, fontes, memórias, importação e ajustes |
| `backend/app` | Autenticação, pesquisa, importação, ferramentas e custos |
| `backend/migrations` | Evolução do banco com Alembic |
| `services/whatsapp-connector` | Sessão vinculada, fila e entrega de eventos |
| `packages/shared-types` | Contratos compartilhados |
| `scripts` e `infrastructure` | Preparação, serviços, backup e ambientes isolados |

## Requisitos

- Git, Node.js **24+** e npm.
- Python **3.12+**.
- Docker Engine/Desktop com contêineres Linux e Docker Compose V2 recente, com suporte a `!override` na composição pessoal.
- Chrome com suporte a painel lateral e extensões sem compactação.
- Para OpenAI: chave válida, consentimento de envio dos dados necessários e saldo no provedor. O desenvolvimento começa com mocks, sem chamadas pagas.

Os exemplos usam Windows/PowerShell. No Linux/macOS, substitua `.venv/Scripts/python.exe` por `.venv/bin/python`. Os comandos Docker pressupõem um contexto configurado para o engine Linux.

Os utilitários pessoais e de backup usam Docker Desktop Linux no Windows e o socket padrão `/var/run/docker.sock` nos demais sistemas. Engines remotos ou sockets alternativos exigem adaptação desses utilitários.

## Instalação para desenvolvimento

```powershell
git clone https://github.com/davidlbg/memorIA.git
cd memorIA
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r backend/requirements.lock
.venv/Scripts/python.exe -m pip install --no-deps -e backend
npm ci
.venv/Scripts/python.exe scripts/setup.py
docker compose up --build -d
npm run build
```

A preparação cria `.env`, `.env.connector` e chaves separadas em `.local/secrets`. Arquivos existentes são preservados. Esses arquivos são privados e não devem ser publicados.

O Compose executa as migrations antes dos serviços. A API fica em **http://127.0.0.1:8000**; o banco de desenvolvimento, em `127.0.0.1:55432`. As credenciais de banco do Compose são apenas para uso local. O provedor inicial é mock e a rede do conector começa desabilitada.

### Carregar a extensão

1. Abra `chrome://extensions` e ative **Modo do desenvolvedor**.
2. Clique em **Carregar sem compactação** e selecione `apps/extension/dist`.
3. Abra a MemorIA e vá a **Ajustes**.
4. Informe `http://127.0.0.1:8000` e o `AUTH_SECRET` do `.env` local.
5. Clique em **Verificar e salvar conexão**.
6. Em **Importar**, envie um TXT exportado, confira a prévia e confirme.

O mock testa o fluxo, não a qualidade de uma IA real. Após recompilar, recarregue a extensão no Chrome.

Para parar: `docker compose stop`. Para iniciar novamente: `docker compose up -d`. **Não use `down -v` se quiser preservar os dados:** essa opção remove volumes.

## Instalação pessoal isolada

Esta composição separa histórico, sessão e credenciais do desenvolvimento. Usa o projeto Docker `whatsapp-memory-personal`, a API em **http://127.0.0.1:58003** e não publica a porta do banco no host.

Após instalar as dependências e compilar a extensão:

```powershell
.venv/Scripts/python.exe scripts/prepare_personal.py
docker compose -f docker-compose.yml -f infrastructure/personal.compose.yml --profile whatsapp build migrate backend worker indexer whatsapp-connector
.\memoria start
.\memoria status
```

O primeiro build é obrigatório. O comando `start` reutiliza imagens existentes; ele **não compila atualizações**. A preparação preserva a configuração existente em `.local/personal`.

Nos Ajustes da extensão, informe a porta **58003** e o `AUTH_SECRET` de `.local/personal/auth.env`. O token do ambiente de desenvolvimento é diferente.

```powershell
.\memoria stop
.\memoria start
.\memoria status
```

Em Linux/macOS, use `.venv/bin/python scripts/personal.py start`, `stop` ou `status`.

A parada encerra primeiro o conector e preserva os volumes. Não desliga outros projetos Docker. Veja [operação pessoal](docs/PERSONAL_OPERATION.md).

### Vincular o WhatsApp

A preparação pessoal habilita a rede do conector. O pareamento ainda exige clicar em **Conectar WhatsApp** e ler o QR Code pelo celular do titular. A instalação geral começa com `CONNECTOR_NETWORK_ENABLED=false`.

Baileys é uma integração não oficial. Mudanças no WhatsApp podem exigir manutenção ou novo pareamento. Reconectar não comprova recuperação completa do histórico. Veja [conector](docs/WHATSAPP_CONNECTOR.md).

### Configurar a OpenAI

Use o formulário OpenAI nos **Ajustes** e confirme o consentimento para enviar os trechos necessários ao processamento escolhido. A validação pode fazer uma chamada sintética e consumir saldo.

A chave é enviada ao backend autenticado, criptografada com uma chave de proteção separada e não é devolvida à extensão. O campo é limpo após o envio. Preserve a chave de proteção separadamente dos backups do banco.

A instalação pessoal começa com mocks e processamento automático pausado. A ativação pela interface mantém um marco para novos trabalhos; não autoriza reprocessamento em massa do acervo anterior. Consultas podem pesquisar mensagens históricas disponíveis.

`OPENAI_QUERY_MODEL` permite separar o modelo de consultas do modelo de extração. Não há fallback automático para IA local quando a API falha. O adaptador local permanece opcional, desabilitado por padrão nesta composição.

## Configuração

| Variável | Finalidade |
|---|---|
| `AUTH_SECRET` | Token Bearer para todo o backend, gerado pela preparação |
| `LLM_PROVIDER` / `EMBEDDING_PROVIDER` | Provedores de respostas/extração e vetores |
| `EXTERNAL_AI_ENABLED` | Consentimento externo na configuração por ambiente |
| `OPENAI_API_KEY` | Alternativa de configuração no backend; há formulário nativo |
| `LLM_MODEL` | Modelo de extração e consultas sem override |
| `OPENAI_QUERY_MODEL` | Modelo exclusivo de consultas |
| `EMBEDDING_DIMENSIONS` | Dimensão: 256 no exemplo geral, 1024 na preparação pessoal |
| `AI_BACKGROUND_ENABLED` | Controle do processamento automático |
| `AI_MONTHLY_BUDGET_USD` | Teto opcional da contabilização compartilhada |
| `AI_BUDGET_PRIOR_SPEND_USD` / `AI_BUDGET_PRIOR_SPEND_MONTH` | Gasto anterior e mês UTC correspondente |
| `WORKER_INDEXES_MESSAGES` | Desative quando o indexador dedicado assumir os vetores dos originais |
| `EXTENSION_ORIGIN` | Origem exata autorizada da extensão |
| `CONNECTOR_NETWORK_ENABLED` | Permissão de rede externa do conector |

Referência: [`.env.example`](.env.example). Os scripts pessoais geram uma configuração isolada. **Não mude a dimensão dos embeddings de um banco existente apenas editando a variável:** planeje uma migração/reindexação compatível.

### Custos

O teto mensal é opcional e não vem ativo em instalações novas. Quando configurado, é compartilhado entre pessoas e processos do mesmo backend. Reservas são feitas antes de chamadas pagas, inclusive sob concorrência. Chamadas com cobrança incerta mantêm a reserva até conferência; timeout não é tratado como chamada gratuita.

Os valores são estimativas locais, não uma garantia de limite na conta OpenAI. Uso da chave por outros sistemas não é medido. Leia [orçamento e reservas](docs/API_BUDGET.md).

## Segurança e compartilhamento

- Há **um token para todo o ambiente**. Compartilhá-lo dá acesso ao histórico completo e às operações disponíveis.
- Contas individuais, convites, permissões por pessoa e **Espaços Compartilhados ainda não estão implementados**.
- Não exponha as portas locais diretamente. Acesso remoto exige HTTPS, controle de rede, origens e autenticação adequados à implantação.
- Banco, exportações e dumps não têm criptografia própria nesta versão; proteja disco e backups.
- A sessão WhatsApp e a credencial OpenAI usam chaves de proteção separadas.
- Conteúdo recuperado é dado não confiável. Confira as fontes das respostas.

Veja [segurança](docs/SECURITY.md), [REST](docs/API.md) e [MCP](docs/MCP.md). Não publique tokens, QR Codes, conversas ou dumps em issues.

## Desenvolvimento e testes

```powershell
npm run typecheck
npm run lint
npm test
npm run build
.venv/Scripts/python.exe -m ruff check backend/app backend/tests
.venv/Scripts/python.exe -m mypy backend/app
```

Backend com PostgreSQL descartável, separado dos serviços pessoais:

```powershell
docker compose -f infrastructure/test.compose.yml up -d --wait
$env:TEST_DATABASE_URL = 'postgresql+psycopg://memory:memory@127.0.0.1:55433/memory_test'
.venv/Scripts/python.exe -m pytest backend/tests -q
docker compose -f infrastructure/test.compose.yml down
Remove-Item Env:TEST_DATABASE_URL
```

O banco usa tmpfs e perde os dados ao encerrar. Sem `TEST_DATABASE_URL`, testes de banco podem ser ignorados; isso não constitui validação completa.

Interface com API simulada:

```powershell
npx playwright install chromium
npm run test:e2e
```

`test:extension` e `test:integration` são opt-in. Consulte [validação](docs/VALIDATION.md). Scripts de avaliação semântica/operação podem chamar provedores ou acessar instalações reais: revise suas condições antes de executá-los.

## Backup e manutenção

```powershell
.venv/Scripts/python.exe scripts/backup.py --personal
```

Salva o dump pessoal em `.local/backups`. Sem `--personal`, usa o Compose geral. Dumps podem conter todo o histórico; mantenha-os privados. Preserve configurações e chaves de proteção separadamente. Teste restauração em banco descartável, nunca sobre uma instalação ativa. Migrations publicadas são imutáveis.

## Problemas comuns

| Sintoma | Conferir |
|---|---|
| Autenticação necessária | URL, porta e token do mesmo ambiente |
| Backend desconectado | Docker ativo, serviços e migrations |
| Conector indisponível | Saúde do serviço, configuração e backend correspondente |
| WhatsApp desconectado | Sessão e necessidade de pareamento pelo titular |
| Resposta incompleta | Filtros, período disponível, captura/importação e fontes |
| Processamento aguardando | Consentimento, provedor, saldo e reservas incertas |
| Interface antiga | Recompilar e recarregar a extensão |

Remova dados privados e credenciais antes de compartilhar diagnósticos.

## Documentação

- [Produto](PRODUCT.md) e [direção visual](DESIGN.md)
- [Arquitetura](docs/ARCHITECTURE.md) e [banco](docs/DATABASE.md)
- [Motor de memória](docs/MEMORY_ENGINE.md) e [importação](docs/IMPORT_FORMATS.md)
- [REST](docs/API.md) e [MCP](docs/MCP.md)
- [Operação](docs/PERSONAL_OPERATION.md), [custos](docs/API_BUDGET.md) e [segurança](docs/SECURITY.md)
- [Validação](docs/VALIDATION.md) e [checklist de aceite](docs/ACCEPTANCE_CHECKLIST.md)

## Contribuições e licença

Use fixtures sintéticas e ambientes descartáveis. Descreva o problema, comportamento esperado e testes relevantes. Mudanças em autorização, retenção ou compartilhamento exigem decisão explícita de produto.

A licença do projeto ainda não foi definida pelo proprietário. Publicar o código não concede automaticamente uma licença de uso, modificação ou redistribuição. Licenças de fontes e ativos de terceiros permanecem junto aos arquivos correspondentes.
