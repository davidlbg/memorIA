# Operação pessoal isolada

A composição pessoal mantém banco, sessão e credenciais separados do ambiente geral. Não representa hospedagem pública pronta. O guia de instalação completo está no [README](../README.md).

## Primeira execução

Instale dependências e compile a extensão conforme o README. Na raiz:

```powershell
.venv/Scripts/python.exe scripts/prepare_personal.py
docker compose -f docker-compose.yml -f infrastructure/personal.compose.yml --profile whatsapp build migrate backend worker indexer whatsapp-connector
.\memoria start
```

A preparação preserva arquivos existentes. A API usa `http://127.0.0.1:58003`; o token está em `.local/personal/auth.env`. A porta do PostgreSQL não é publicada. Configuração inicial: mocks, IA externa/local desativada e processamento automático pausado. A rede WhatsApp fica habilitada, mas o pareamento depende da ação do titular no painel.

## Uso diário

```powershell
.\memoria start
.\memoria status
.\memoria stop
```

No Linux/macOS, use `.venv/bin/python scripts/personal.py` seguido da ação. `start` não compila imagens. Ele para o conector antes de recriar os serviços necessários e só o reinicia após o backend. `stop` preserva volumes e não encerra outros projetos.

## Atualização

Pare esta instalação, faça backup e revise as migrations antes de atualizar. Atualize o código, instale dependências dos lockfiles, execute `npm run build` e repita o build Compose acima. Depois execute `start` e recarregue a extensão no Chrome. O serviço de migração aplica Alembic antes da API; migrations publicadas são imutáveis.

Não altere `EMBEDDING_DIMENSIONS` no banco existente sem migração/reindexação planejada. Não misture os tokens, projetos ou volumes da instalação geral e pessoal.

## Credenciais e IA

Ajustes permite cadastrar a chave OpenAI com consentimento explícito. A validação pode consumir saldo. A proteção criptográfica usa `.local/personal/ai.key`, separado do banco; a sessão WhatsApp usa uma chave própria. Configuração de runtime fica em `.local/personal/runtime.env`.

O processamento de novos trabalhos e consultas não autoriza reprocessamento em massa do histórico anterior. IA local não é fallback automático. O orçamento mensal é opcional: configure-o antes de iniciar uso pago e confira [reservas e limitações](API_BUDGET.md).

## Backup e recuperação

```powershell
.venv/Scripts/python.exe scripts/backup.py --personal
```

Dumps ficam em `.local/backups` e contêm dados privados. Preserve separadamente configuração e chaves de proteção; o dump do banco não substitui o backup do volume criptografado da sessão WhatsApp. Mantenha cópias protegidas por controle de acesso e criptografia de disco.

Teste restauração em banco descartável. `scripts/verify_backup.py --help` descreve o verificador isolado; revise o arquivo escolhido e nunca restaure sobre uma instalação ativa sem um plano. Exclusões posteriores não apagam backups anteriores. `docker compose down -v` remove volumes e não deve ser usado como parada cotidiana.

## Diagnóstico

Use `status` primeiro. Autenticação exige URL e token do mesmo ambiente. Conector indisponível exige conferir saúde, configuração e dependência do backend; não significa necessariamente QR inválido. Evite logs de conteúdo e nunca compartilhe tokens, QR Codes ou saída bruta de banco. A captura é parcial e reconexões não comprovam recuperação integral.
