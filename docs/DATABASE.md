# Banco de dados e invariantes

UUIDs e timestamps com fuso são usados em todos os registros. As tabelas são
`conversations`, `contacts`, `messages`, `memories`, `memory_sources`,
`message_embeddings`, `memory_embeddings`, `import_jobs`.

Contatos são locais à conversa: a unicidade é `(conversation_id, normalized_name)`.
Não há fusão de pessoas por coincidência de nome. `external_identifier` é
reservado a identificação explícita futura.

Mensagens preservam o conteúdo decodificado e as quebras de linha normalizadas
pelo TXT; espaços e Unicode do corpo não são reescritos. Hashes incluem hora UTC,
nome do remetente, tipo, conteúdo e ordinal de ocorrências idênticas. A constraint
`(conversation_id, content_hash)` arbitra deduplicação, inclusive concorrente.
Exportações sobrepostas devem ser direcionadas à mesma conversa pelo usuário.
Sem identificadores nativos, exportações que omitem uma dentre várias mensagens
idênticas no mesmo minuto têm uma ambiguidade irredutível. Não há como recuperar
identidade perdida no TXT.

Memórias têm tipo, confiança limitada a 0–1, status, sujeito, texto literal e
evidências. O fingerprint agrupa tipo + sujeito + conteúdo normalizado apenas
na mesma conversa. Eventos, intenções e compromissos também incluem a data observada no fingerprint para preservar episódios repetidos. `memory_sources` não permite pares duplicados.

Triggers de constraint **deferred**, no commit, impedem inserir memória sem fonte
ou remover sua última fonte, mesmo por SQL fora do serviço. Conversa, mensagens,
contatos, jobs, memórias e vetores são removidos por cascata ao excluir a conversa.
Memórias nunca cruzam conversas; o pipeline valida essa regra.

Imports e workers adquirem locks na ordem conversa → job. `FOR UPDATE SKIP LOCKED`
permite vários workers sem processar a mesma conversa simultaneamente. Um lote
pendente permanece recuperável se o processo morrer antes do commit. A contagem
de processamento é confirmada na mesma transação que vetores e memórias.

Índices B-tree cobrem FKs e datas. GIN em `to_tsvector('simple', content)` cobre a
busca de palavras em português e inglês sem configuração linguística implícita.
pgvector executa busca exata de cosseno; HNSW é uma evolução de desempenho a ser
medida, não necessário para a correção do MVP.

`EMBEDDING_DIMENSIONS` define o esquema somente na primeira migration. A API
verifica as duas colunas no startup e recusa divergências. Identidade do vetor
inclui provedor, modelo/versão configurada e dimensão. Alterações de esquema
devem ser novas migrations; `0001_initial.py` não deve ser alterada após publicação.

Referência primária: [constraint triggers do PostgreSQL](https://www.postgresql.org/docs/18/sql-createtrigger.html).


## Extensão v0.2

A migration 0004 acrescenta lease/fence, chats e aliases comprovados, estado por chave nativa, registros idempotentes e trabalho por revisão. Campos novos em mensagens, contatos e jobs têm defaults compatíveis com TXT. As migrations 0001–0003 permanecem inalteradas. Identificadores de controle sobrevivem à exclusão da conversa para impedir ressurreição por replay. Veja [invariantes e retenção](WHATSAPP_CONNECTOR.md).


## Aceite operacional v0.2

A matriz de auditoria, isolamento do ensaio e roteiro humano estão no [checklist de aceite](ACCEPTANCE_CHECKLIST.md). A release não está aprovada enquanto os critérios obrigatórios estiverem pendentes. As migrations 0001–0004 são preservadas; ensaios não usam volumes de dados pessoais.

## Evolução posterior

As migrations 0005–0007 acrescentam evidência contextual, identidade de reprocessamento
e contexto das memórias. `0008_ai_credentials` persiste a configuração de IA e a
credencial criptografada; sua chave de proteção permanece fora do banco.
`0009_ai_usage_budget` acrescenta períodos, operações e reservas de custo compartilhadas.
A árvore de migrations é a referência completa do esquema; todas as migrations
publicadas devem permanecer imutáveis.
