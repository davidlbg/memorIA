# Checklist de aceite

Execute em instalação isolada com dados sintéticos. Marcar um item exige evidência da versão e ambiente avaliados. Chamadas pagas e pareamento real exigem autorização própria.

## Instalação e operação

- [ ] Clone limpo, dependências dos lockfiles e build sem arquivos privados preexistentes.
- [ ] Preparação gera chaves distintas e preserva configuração já criada.
- [ ] Migrations aplicadas antes da API; reinício preserva os dados.
- [ ] Start/stop não afetam outros projetos e mantêm a ordem do conector.
- [ ] Backup restaurado em destino descartável; chaves de proteção preservadas separadamente.

## Fluxos

- [ ] Prévia TXT informa datas/fuso, erros e contagens corretamente.
- [ ] Reimportação não duplica mensagens; falha permite recuperação.
- [ ] Perguntas gerais reúnem contexto; específicas respondem ao ponto.
- [ ] Contradições e evolução temporal são distinguidas com fontes.
- [ ] Evidência ausente não vira afirmação inventada.
- [ ] Edição/revogação invalida fontes antigas e derivados.
- [ ] Exclusão de memória difere de exclusão da conversa e suas fontes.

## Segurança e custos

- [ ] API, OpenAPI e MCP recusam acesso sem Bearer válido.
- [ ] Chaves/QR/conteúdo não entram em logs nem respostas de diagnóstico.
- [ ] Configuração OpenAI exige consentimento e rejeição preserva a chave anterior.
- [ ] Reservas compartilhadas respeitam concorrência e mantêm chamadas incertas.
- [ ] Credenciais e dados locais não entram no Git nem nos contextos Docker.

## Interface e conector

- [ ] Painel estreito, temas e teclado permitem leitura e navegação sem sobreposição.
- [ ] Estados sem conexão, sem resultados e erros são compreensíveis.
- [ ] Fontes abrem e continuam válidas; conteúdo revogado não reaparece.
- [ ] Pareamento autorizado, reconexão e entrega confirmada funcionam no ambiente real.
- [ ] Não há envio de mensagens; cobertura parcial fica explícita.

Veja [validação](VALIDATION.md) para comandos. O checklist não presume aprovação de uma instalação e não substitui revisão de exposição remota/autorização.
