# Segurança e privacidade

O padrão é totalmente local, com provedores mock. Na configuração por ambiente,
OpenAI exige `EXTERNAL_AI_ENABLED=true` e uma chave no backend. O formulário nativo
persiste a chave criptografada e o consentimento, aplicados em runtime sem exigir
essa edição manual do ambiente. Nenhuma chave
OpenAI entra no bundle da extensão ou em scripts de páginas. O token local da
extensão permite acesso ao histórico e deve ser tratado como credencial.

O token deve ter ao menos 32 caracteres; `scripts/setup.py` gera 48 bytes
aleatórios. A API falha no startup se ele não estiver configurado. A comparação
é feita em tempo constante. Autenticação antecede a leitura do upload. A extensão
mantém o token em `chrome.storage.session`, inacessível a content scripts por
padrão, sem solicitar essa exposição. A extensão não injeta content scripts.
Não se persistem perguntas/respostas. O tema é a única preferência em localStorage.

HTTP é aceito pela UI apenas para loopback. HTTPS remoto exige concessão de
host pelo usuário. A extensão nunca segue redirects de API carregando token.
Permissões obrigatórias: `sidePanel`, `storage` e hosts HTTP loopback; outros
hosts HTTPS são opcionais. CORS aceita apenas origens locais de desenvolvimento
e a origem exata de extensão configurada. Não há CORS wildcard com credenciais.

O limite de corpo é aplicado aos bytes reais, inclusive chunked, antes do parser
multipart. Tamanhos, IDs, datas, páginas, schemas de IA e vetores são validados.
Conteúdo recuperado é dado não confiável, nunca instrução do sistema.
Citações e IDs de evidência são verificados após geração.

Logs comuns não devem conter conteúdo, perguntas, chaves, uploads ou corpo de
respostas. O worker registra apenas ID do job e falha genérica. O comando padrão
desabilita access logs do Uvicorn. Não habilite logs SQL `echo`, debug de HTTP ou
dump de prompts em um banco real. Os exemplos de testes usam apenas dados sintéticos.

## Exclusão e retenção

Excluir uma conversa apaga contatos, mensagens, jobs, memórias, fontes e vetores
associados por cascata. A operação serializa com imports/workers. Excluir uma
memória apaga texto derivado, resumo e embedding; fica um tombstone com
fingerprint, metadados e vínculos às fontes para não recriá-la em reprocessamento.
As mensagens originais não são apagadas por essa ação. Para apagar fontes e
tombstones, exclua a conversa. A UI informa essa diferença antes da confirmação.

PostgreSQL, volumes Docker, exports originais e backups não têm criptografia
própria nesta versão. Exclusão lógica/física no banco não garante apagamento
forense de WAL, páginas antigas, snapshots do sistema ou backups. Use criptografia
de disco e políticas de retenção externas. Não há criptografia ponta a ponta.

## Backup e restauração

Execute `python scripts/backup.py` para gerar um dump **do serviço Compose local**
em `.local/backups`. Não há backup automático sem agendamento explícito.
Proteja e remova cópias conforme sua necessidade. Teste a restauração em outro
banco descartável antes de confiar em um backup. Com o banco de destino criado,
use `pg_restore --no-owner --dbname=<banco-descartavel> arquivo.dump`; nunca
restaure sobre dados existentes sem avaliar o impacto. Dumps contêm mensagens
em texto claro. Backups antigos não são alterados por exclusões feitas pela UI.

Limitações: usuário único, sem MFA, RBAC, OAuth, proteção contra comprometimento
da conta do sistema operacional ou processos locais maliciosos. Não publique
diretamente as portas do Compose na internet.


## Extensão v0.2

O vault do conector usa AES-256-GCM, nonce aleatório e gravação atômica; chave separada do volume de sessão. Tokens, QR, conteúdo e JIDs não são logs. Rede WhatsApp exige flag explícito e ação de conexão; providers de IA mantêm o consentimento próprio. A retenção escolhida apaga texto anterior e derivados após aplicação de edição/revogação; outbox não confirmado e backups exigem considerar os limites descritos em [segurança e retenção do conector](WHATSAPP_CONNECTOR.md).


## Aceite operacional v0.2

A matriz de auditoria, isolamento do ensaio e roteiro humano estão no [checklist de aceite](ACCEPTANCE_CHECKLIST.md). A release não está aprovada enquanto os critérios obrigatórios estiverem pendentes. As migrations publicadas são preservadas; ensaios não usam volumes de dados pessoais.

## Credencial OpenAI configurada pela extensão

A configuração OpenAI permite consultas e novos trabalhos conforme consentimento;
não implica reprocessamento automático do acervo anterior. O consentimento
é explícito no formulário. A chave não fica no código, no armazenamento do Chrome
ou nas respostas da API. O campo é limpo após enviar. O backend usa AES-GCM com
nonce aleatório e chave de proteção dedicada de 32 bytes, montada como secret e
guardada fora do banco. A migration 0008 armazena somente ciphertext e estado.
Uma substituta rejeitada preserva a credencial anterior. Texto arbitrário de
exceções nunca é devolvido; mensagens de chave e cota usam lista fixa permitida.

O dump inclui o ciphertext, mas não `ai.key`. Proteja esse arquivo separadamente
do banco, junto da configuração privada necessária à recuperação. A remoção da
credencial local não revoga a chave no projeto OpenAI. A composição pessoal não inicia Ollama por padrão; falhas da API não acionam fallback local.
