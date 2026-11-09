# MemorIA: produto e limites

A MemorIA ajuda a recuperar informações de conversas do WhatsApp. O fluxo principal é perguntar no painel lateral, ler uma resposta contextualizada e conferir as mensagens originais.

## Comportamento esperado

Perguntas amplas devem reunir contexto relevante, evolução temporal e lacunas. Perguntas específicas devem responder ao ponto com evidências suficientes, sem preencher ausências por suposição. Quando há contradições, a resposta deve distinguir os momentos e a decisão mais recente comprovada. Ausência de evidência não prova ausência do fato.

O backend intermedeia as ferramentas de pesquisa e valida as referências retornadas pelo modelo. A qualidade depende do histórico disponível, dos filtros e dos limites de pesquisa; não é uma garantia de exatidão.

## Entregue nesta versão

- Chat, fontes, memórias, importação TXT e ajustes no painel lateral.
- REST e MCP de leitura, autenticação por token do ambiente.
- Conector de dispositivo vinculado com captura parcial e retenção de controle de eventos.
- Provedores mock, OpenAI e adaptador local opcional; consentimentos próprios.
- Configuração nativa da chave OpenAI e contabilização compartilhada opcional.
- Exclusão de conversas/memórias e invalidação de derivados de fontes alteradas.

## Limites

Não há envio de mensagens, coleta da tela, download/transcrição de mídias ou garantia de recuperação integral do histórico. A versão atual não oferece contas individuais, convites, permissões por contato ou isolamento multi-tenant. Compartilhar o token compartilha acesso ao ambiente inteiro.

Espaços Compartilhados são uma direção de produto ainda não implementada: um titular configuraria WhatsApp/backend/IA e convidaria outras pessoas sem repetir essa configuração. Autorização, isolamento, revogação e cobrança precisam ser definidos antes dessa implementação.

Interface: [DESIGN.md](DESIGN.md). Operação e requisitos: [README.md](README.md).
