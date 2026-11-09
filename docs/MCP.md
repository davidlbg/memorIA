# MCP somente leitura

Transporte: Streamable HTTP em `http://127.0.0.1:8000/mcp/`.
Implementação: SDK Python oficial `mcp` v1, com dependência limitada a `<2`.
Autenticação Bearer é aplicada antes de qualquer negociação ou chamada de tool.
Não há ferramenta de importação, exclusão, execução de SQL ou envio de mensagens.

| Tool | Argumentos |
|---|---|
| search_messages | query, contact_id, conversation_id, date_from, date_to, limit |
| search_memories | query, memory_type, contact_id, limit |
| get_conversation | conversation_id, include_messages, limit |
| get_contact_context | contact_id, query |
| get_timeline | contact_id, date_from, date_to, limit |

`limit` deve estar entre 1 e 100. `get_contact_context` retorna no máximo 20
memórias com fontes. `get_timeline` seleciona as mensagens mais recentes que
passam pelos filtros e as devolve em ordem cronológica. Use a paginação REST para
percorrer um histórico completo. Ferramentas reutilizam serviços do backend.

Exemplo de cliente Python (token obtido de variável do seu ambiente):

```python
import asyncio
import os
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async def main():
    async with streamablehttp_client(
        "http://127.0.0.1:8000/mcp/",
        headers={"Authorization": "Bearer " + os.environ["MEMORY_TOKEN"]},
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print(await session.list_tools())
            result = await session.call_tool("search_messages", {"query": "documento"})
            print(result)

asyncio.run(main())
```

Não registre resultados de conversas em logs compartilhados. Para acesso remoto,
adicione um proxy HTTPS autenticado e restrição de rede. O token é de usuário
único e dá acesso a todo o histórico; não há OAuth nem escopo individual por
contato. HTTP remoto sem TLS não é uma implantação suportada. O SDK também
protege Host/Origin; ajuste uma política explícita antes de publicar outro host.

Referência: [documentação oficial do SDK Python v1](https://py.sdk.modelcontextprotocol.io/v1/).
