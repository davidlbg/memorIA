# REST API v1

Todas as rotas, inclusive `/docs`, `/openapi.json` e `/mcp/`, exigem
`Authorization: Bearer <AUTH_SECRET>`. As rotas de aplicação ficam em `/api/v1`.
Use um cliente REST com esse cabeçalho para obter o OpenAPI; o navegador normal
sem token recebe 401. Não envie o token na URL.

O POST `/assistant/query` aceita `timezone` IANA (padrão `America/Sao_Paulo`).
No provedor OpenAI, o servidor executa a pesquisa por ferramentas de leitura
escolhidas pelo modelo e devolve `research_steps` (tool, results, status) e
`research_limited`. Os filtros da consulta não podem ser ampliados pelo modelo.
`context_sources` inclui os originais inspecionados além das fontes citadas.
O endpoint de validação de fontes recebe até 500 referências por pedido;
clientes com mais referências devem enviar lotes e agregar os resultados.

As ferramentas internas de pesquisa são chamadas pelo modelo durante a consulta,
não novos endpoints públicos sem autenticação. `search_messages` aceita offset e
limit, retornando paginação e cobertura da janela ranqueada. `search_timeline`
retorna amostras mensais, contagem de correspondências e faixa temporal; respeita
os mesmos filtros fixos. A faixa do histórico disponível no escopo é informada ao
pesquisador antes da primeira chamada. O registro `research_steps` pode incluir
`status=limited` na etapa final reservada à seleção das evidências.

`GET /health` inclui `query_model`, separado de `llm_model` (background).
`OPENAI_QUERY_MODEL` permite configurar o modelo de consultas sem mudar o worker
ou os embeddings. GPT-5.4 mini usa Responses, estado opaco por consulta e citações
por referências de trechos originais; o formato público de Answer é preservado.

| Método | Rota | Uso |
|---|---|---|
| POST | `/imports/preview` | Multipart: file, date_order, timezone |
| POST | `/imports` | Multipart anterior + title, preview_hash, conversation_id opcional |
| GET | `/imports` | Jobs e contagens de importação/processamento |
| GET | `/imports/{id}` | Estado do job |
| POST | `/imports/{id}/retry` | Retomar job failed |
| GET | `/conversations` | Conversas importadas |
| GET | `/conversations/{id}` | Conversa e página de mensagens |
| DELETE | `/conversations/{id}` | Excluir conversa e dados dependentes |
| GET | `/messages/search` | query, contact_id, conversation_id, date_from, date_to, limit |
| GET | `/memories` | memory_type, contact_id, conversation_id, limit, offset |
| GET | `/memories/search` | Busca híbrida e os filtros anteriores, com datas |
| GET | `/memories/{id}` | Memória e fontes |
| DELETE | `/memories/{id}` | Excluir texto/vetor e preservar tombstone contra recriação |
| GET | `/contacts` | Contatos separados por conversa |
| GET | `/contacts/{id}` | Identificação do contato |
| GET | `/contacts/{id}/context` | query opcional, contexto com evidências |
| POST | `/assistant/query` | JSON: question e filtros opcionais |
| GET | `/health` | Conectividade e configuração pública dos provedores |
| PATCH | `/settings/provider` | JSON: llm_provider (mock ou openai), seleção local persistente |

Listagens usam `limit` (1–100) e `offset` (0–1.000.000); buscas ranqueadas usam
top-k `limit`. As ferramentas de contexto têm limites fixos documentados. Datas
de consulta precisam incluir fuso e formar intervalo válido. Perguntas têm no
máximo 2.000 caracteres. As respostas não incluem chaves de provedores.

Formato comum de erro:

```json
{"error":{"code":422,"message":"Parâmetros inválidos","fields":["body.question"]}}
```

401 = autenticação; 404 = recurso ausente; 409 = estado/hash incompatível;
413 = tamanho; 422 = validação; 503 = falha de IA ou evidência inválida.
A ausência de evidência é uma resposta bem-sucedida, com `claims: []`,
`sources: []` e `insufficient_evidence: true`.

Respostas de assistente distinguem `explicit_statement`, `extracted_memory` e
`interpretation`. Cada claim tem `quote` e `source_message_ids`; fontes retornam
conteúdo original, remetente, data e conversa. A API não persiste as perguntas.


## Extensão v0.2

Foram adicionados GET /api/v1/whatsapp/connection, POST /api/v1/whatsapp/connect e POST /api/v1/whatsapp/disconnect, com autenticação pública existente. A família /internal/whatsapp exige loopback e CONNECTOR_SECRET distinto. ACK de ingestão ocorre após commit. SourceMessage inclui origem, revisão, metadados permitidos, instante de edição, ID nativo e direção. Veja [contratos e operação](WHATSAPP_CONNECTOR.md).
# Recursos de uso pessoal v0.2

- `GET /api/v1/conversations/{id}?order=desc&before_id={message_id}`: timeline
  com cursor estável; o cursor precisa pertencer à conversa selecionada.
- `POST /api/v1/conversations/{id}/reprocess`: agenda um job sobre as revisões
  ativas existentes. Retorna 202 com o contrato habitual de importação; não
  duplica mensagens. Use `GET /imports/{id}` para acompanhar seu andamento.
- `POST /api/v1/messages/validate`: recebe `sources: [{id, revision}]`, até 500,
  e retorna somente referências ainda ativas na mesma revisão. Não retorna texto.
- `POST /api/v1/assistant/query` aceita `previous_question` opcional; é contexto
  de tópico, nunca evidência. As fontes incluem título da conversa e indicação
  de correspondência/diálogo adjacente.
- Memórias incluem `evidence_basis` e `supporting_quotes` por mensagem. O modo
  local requer configuração explícita, modelos instalados e digests exatos.


Memórias e respostas incluem também `context_sources`: inputs considerados pela IA
que não foram citados pelo modelo. Não confundir com `sources`/`supporting_quotes`.
Validar todas as referências de ambas as listas ao reexibir uma resposta.
A alteração de qualquer input da extração invalida o derivado integralmente.

## Configuração nativa da OpenAI

`POST /api/v1/settings/openai` exige Bearer local e JSON com `api_key` e
`consent: true`. Verifica embeddings e geração usando texto sintético; somente
após sucesso grava a credencial criptografada e ativa os provedores. A resposta
é HealthView, sem chave nem ciphertext. `DELETE` desativa novos lotes externos,
apaga a credencial ativa e seleciona mock explicitamente. Chamadas em andamento
podem concluir. `/health` informa disponibilidade de setup e o corte de novos
trabalhos em `ai_processing_since`, sem revelar credenciais.

O transporte usa JSON Schema estrito, temperatura zero e `store:false`; valida
trechos e referências contra as fontes canônicas antes de retornar. As sínteses
geradas são interpretações; os trechos literais permanecem nas citações.
Contrato de schema: [documentação oficial da OpenAI](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses).
