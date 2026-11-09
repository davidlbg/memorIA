# Validação reproduzível

Use ambiente local, fixtures sintéticas, provedores simulados e PostgreSQL descartável. Não use a instalação pessoal como destino da suíte. Não execute scripts com nomes `personal`, `openai` ou `evaluate` sem revisar seus efeitos: podem ler configuração privada ou acionar o provedor.

## Verificações estáticas e unitárias

Na raiz, com dependências instaladas:

```powershell
npm run typecheck
npm run lint
npm test
npm run build
.venv/Scripts/python.exe -m ruff check backend/app backend/tests
.venv/Scripts/python.exe -m mypy backend/app
```

## Backend e banco

```powershell
docker compose -f infrastructure/test.compose.yml up -d --wait
$env:TEST_DATABASE_URL = 'postgresql+psycopg://memory:memory@127.0.0.1:55433/memory_test'
.venv/Scripts/python.exe -m pytest backend/tests -q
docker compose -f infrastructure/test.compose.yml down
Remove-Item Env:TEST_DATABASE_URL
```

O banco usa tmpfs, nome terminado em `_test` e porta 55433. A suíte aplica migrations e limpa tabelas entre testes. O fixture rejeita destinos que não atendam às restrições de teste. Sem essa variável, testes de banco podem ser ignorados.

Os cenários cobrem importação/idempotência, retenção de fontes, pesquisa, configuração criptografada, chamadas HTTP simuladas, reservas de orçamento e concorrência. Aprovação com mocks não comprova qualidade semântica de um modelo real nem conectividade WhatsApp.

## Navegador

```powershell
npx playwright install chromium
npm run test:e2e
npm run test:extension
```

`test:e2e` inicia a interface em 127.0.0.1:5173 e usa API simulada para os fluxos do painel. `test:extension` carrega o build MV3 em um perfil temporário do Chromium, valida service worker e permissões. Compile antes. Capturas ficam em `test-results`, ignorado pelo Git.

`npm run test:integration` exige API + worker descartáveis e as variáveis `MEMORY_E2E_URL` e `MEMORY_E2E_TOKEN`. O teste importa e exclui uma conversa sintética; não aponte para um backend pessoal. Ele não faz parte do comando padrão `test:e2e`.

## Aceite adicional

A conectividade real do dispositivo, recuperação após reinício, restauração de backups e qualidade do modelo precisam de ensaios próprios. Não trate testes simulados como comprovação desses itens. Use o [checklist](ACCEPTANCE_CHECKLIST.md) e registre versão, ambiente, resultado e limitações sem incluir dados privados.

Este guia descreve comandos reproduzíveis, não certifica uma implantação específica. Relatórios de operação pessoal não são distribuídos com o código.

## Conferência de publicação — 2026-11-09

- Typecheck, lint e build dos workspaces aprovados; Ruff e mypy aprovados.
- Backend: 154 testes aprovados com PostgreSQL/pgvector descartável, incluindo reservas concorrentes.
- Extensão: 21 testes unitários e cinco cenários do painel aprovados.
- Conector: 20 testes aprovados; três testes de integração opt-in não executados.
- Extensão MV3 empacotada: carregamento, service worker, contrato de contexto e permissões aprovados. A asserção de título foi atualizada para o redesign existente.
- Preparação geral/pessoal repetida e configuração Compose verificadas em cópia limpa; dependências npm instaladas com scripts desabilitados e build aprovado nessa cópia.
- Imagens Docker de backend e conector compiladas, com reaproveitamento de camadas quando disponíveis.
- Revisão independente de publicação sem achados críticos/importantes; arquivos privados não incluídos.

Não houve chamadas pagas, pareamento real ou acesso ao banco pessoal. O banco de testes foi removido ao final. Não foi executado o E2E contra API + worker reais nesta conferência.

`npm audit` registrou dez ocorrências moderadas na cadeia de ferramentas ESLint/TypeScript/minimatch, sem altas ou críticas. Permanecem avisos de depreciação no backend e anotações de dependências no build. Não foi aplicada atualização forçada com mudanças incompatíveis; essa manutenção fica separada da publicação.
