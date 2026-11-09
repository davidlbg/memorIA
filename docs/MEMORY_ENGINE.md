# Motor de memória

1. O parser identifica limites, datas e remetentes, mantendo mensagens originais.
2. A transação de importação cria conversa/contatos necessários, deduplica e grava
   mensagens com sua importação de origem.
3. O worker usa mensagens como unidades, agrupadas em lotes limitados. Mensagens
   de sistema, referências de mídia e textos acima de 8.000 caracteres continuam
   disponíveis por busca de palavras, mas não são enviados para IA nesta versão.
4. `EmbeddingProvider` gera vetores; quantidade, dimensões e finitude são validadas.
5. `LLMProvider.extract_memories` retorna JSON validado por Pydantic.
6. Todos os IDs devem existir no contexto limitado do lote, ter a mesma conversa
   e sustentar o sujeito e a data declarados. Ao menos uma fonte pertence ao lote
   atual; mensagens anteriores e respostas citadas podem explicar seu contexto.
7. Declarações explícitas de baixa confiança não viram fatos/eventos. Perguntas e alguns
   marcadores explícitos de incerteza são rejeitados. Não existe detector geral
   de sarcasmo; classificação semântica continua dependente do provedor.
8. Em `evidence_basis=explicit`, `content == evidence_quote` continua obrigatório.
   Em `interpretation`, o conteúdo pode relacionar mensagens, mas precisa de uma
   citação exata por fonte, data da observação mais recente e sujeito nulo quando
   os autores diferem. A interface identifica a interpretação; a validação de
   citações não constitui prova automática de que a conclusão do modelo é correta.
9. A consolidação junta duplicatas e fontes; não sobrescreve histórias conflitantes.

A única resolução automática de contradição em v0.1 é o padrão literal
“Pretendo/Planejo X” → “Desisti de X”, ou “I plan/intend to X” →
“I no longer plan to X”, do mesmo sujeito e conversa, com data posterior.
O registro anterior passa a `outdated` e ambas as evidências permanecem.
Essa resolução independe da ordem de importação. Contradições gerais ficam
**UNKNOWN / NEEDS BUSINESS DECISION**; não há inferência de estado por similaridade.

Falhas de provedor, JSON, fontes ou banco revertem o lote inteiro e incrementam
tentativas. Após três falhas o job passa a `failed`; o usuário pode retomar pela
interface. Mensagens previamente importadas e lotes já concluídos permanecem.
Chamadas têm timeout e até três tentativas para falhas elegíveis. Chamadas pagas
com resultado financeiro incerto não são repetidas automaticamente; consulte
[orçamento e reservas](API_BUDGET.md).

Na consulta, palavras-chave e cosseno produzem rankings independentes, combinados
por reciprocal rank fusion. Filtros são aplicados antes da seleção dos candidatos.
Nomes de contatos só são resolvidos automaticamente se houver uma única
correspondência literal no escopo; casos ambíguos usam filtros explícitos.
O contexto da resposta é limitado a 24 mensagens/24 mil caracteres. Cada afirmação
retorna IDs e citação literal validada. Declarações explícitas precisam ser a
própria citação; paráfrases são identificadas como interpretação. Uma citação
válida não comprova toda interpretação de um LLM: o usuário pode conferir a fonte.

O modo mock não usa serviços externos nem simula inteligência geral: classifica
alguns padrões explícitos e devolve citações relevantes. Não há memória fake.

O modo `ollama` usa modelos treinados locais para embeddings, extração e resposta,
sem fallback para mock. Consentimento local e digests de ambos os modelos são
obrigatórios. O reprocessamento explícito usa os originais e revisões existentes;
pedidos concorrentes com a mesma configuração reutilizam o trabalho pendente.
Mudanças nas fontes invalidam interpretações inteiras e removem seus derivados.
Veja [operação pessoal](PERSONAL_OPERATION.md).

No modo local, toda memória gerada é marcada conservadoramente como interpretação
da IA, mesmo quando usa as palavras originais. As citações literais permanecem
em campos separados e precisam corresponder exatamente a cada fonte. O adaptador
não transforma uma resposta inválida em válida depois da geração: o schema exige
essa classificação antes de o modelo responder, e a validação continua rejeitando
suportes, datas e sujeitos inválidos.


## Extensão v0.2

O conector usa o mesmo process_batch. WorkItems só processam revisões ainda ativas. Editar/revogar apaga embedding e suporte anteriores antes de enfileirar a revisão; memórias sem suporte são removidas. A edição usa seu instante nativo como data de observação, preservando sent_at original. Respostas revalidam conteúdo/revisão antes de expor fontes. Veja [política de exclusão](WHATSAPP_CONNECTOR.md).


## Linhagem integral e concorrência

`memory_sources` registra apenas citações declaradas pelo modelo. `memory_context`
registra separadamente todos os inputs e suas revisões, inclusive os que o modelo
não citou. A interface apresenta esse contexto à parte, sem fingir uma citação.
Editar/revogar qualquer input remove a memória derivada inteira. A migration 0007
rejeita contexto de outra conversa ou revisão inativa/desatualizada.

Inferência não mantém bloqueios de linha nem vetores não confirmados no banco.
Um advisory lock transacional exclui workers concorrentes; captura/edição continuam
funcionando durante a geração. Antes de persistir, o pipeline bloqueia brevemente
a conversa, revalida todos os inputs e rejeita resultados de revisões antigas.
O teste comportamental mantém a IA suspensa enquanto entrega uma mensagem e uma
edição reais à API sintética, comprova o ACK e impede derivados obsoletos.

O adaptador local envia um JSON Schema estrito ao Ollama, com instrução compacta
no prompt e validação Pydantic completa antes de retornar ou persistir dados.
O ensaio sem schema foi rejeitado: omitiu campos obrigatórios e acrescentou
campos indevidos. Não há fallback para mock nem reparo de saída.

Cada fonte é escolhida uma vez como par referência/trecho literal. O modelo pode
copiar o menor trecho suficiente, sem ser obrigado a gerar uma mensagem inteira.
O servidor rejeita referências inexistentes, trechos que não pertencem à fonte,
autores incompatíveis e estruturas fora dos limites. A observação é calculada
como a data mais recente das fontes escolhidas, usando a revisão quando editada.
Os limites de 2 MB, tokens e timeout continuam ativos; saídas incompletas ou
inválidas geram erro visível. A identidade de reprocessamento inclui versão do
contrato local, hash da instrução e identidade do modelo/embedding.

O serviço `indexer` gera embeddings dos originais independentemente do LLM.
Com Ollama, processa até quatro mensagens/8 mil caracteres e usa uma thread
para embeddings; outros provedores mantêm 16 mensagens/32 mil caracteres.
Não mantém bloqueios durante inferência;
revalida conteúdo/revisão sob o bloqueio breve da conversa antes de inserir.
Conflitos de escrita não duplicam vetores; fontes alteradas permanecem pendentes.
Assim a cobertura semântica pode avançar mesmo com extração de memórias lenta.
Use os logs de `indexer` para acompanhar lotes e falhas, sem texto das conversas.


A resposta local inclui exemplos equilibrados de resposta sustentada e ausência
de informação. Eles demonstram formato e relação entre pergunta e aceitação,
não evidência. Referências curtas `m1`, `m2` são locais a cada extração ou consulta; o payload de IA
omite identificadores de controle desnecessários. Cada referência e trecho são
validados contra um único original atual antes de converter as escolhas nos UUIDs
públicos, sem acrescentar fontes. A validação rejeita trechos de exemplos ou de
outra mensagem. O ensaio real usa uma paráfrase com pedido/aceitação e uma pergunta
sobre valor ausente no mesmo assunto recuperado; não certifica toda interpretação.

O contexto local usa por padrão `LOCAL_AI_CONTEXT_CHARACTERS=8000`, selecionando
originais completos sem truncar seus textos. Os modos não locais mantêm seus
orçamentos anteriores. O payload compacto preserva autor, identidade própria,
datas, revisão e papel no diálogo; referências locais distintas preservam a
separação de contatos homônimos e conversas. A identidade de reprocessamento inclui
o orçamento e a instrução completa. Testes com banco verificam esse limite tanto
na extração quanto na consulta, sem confundir redução de contexto com exclusão.

O adaptador local não pede uma segunda síntese em `evidence_summary`. Esse campo
é metadado de proveniência calculado pelo servidor: informa quantas fontes foram
citadas e remete aos trechos de apoio. A síntese semântica fica em `content`;
isto evita que um segundo resumo invente uma relação entre prazos diferentes.
O schema rejeita saídas que tentem acrescentar esse campo. O contrato v8 altera
a identidade de reprocessamento; não corrige silenciosamente saídas antigas.

## Contrato compartilhado com OpenAI

OpenAI e Ollama agora compartilham o encoder e a validação de evidências. O
transporte OpenAI usa JSON Schema estrito, temperatura zero e `store=false`.
Cada memória começa por `current_quote`, restrita a uma mensagem atual;
`supporting_quotes` acrescenta outras fontes sem duplicá-la. O servidor valida
o vínculo com a mensagem atual também fora do decoder. Isso impede aceitar um
resumo só de contexto antigo como processamento da mensagem nova.

A observação e o resumo de proveniência são calculados a partir dos pares
referência/trecho escolhidos. Não se reclassifica nem repara uma saída inválida.
A identidade de reprocessamento inclui o contrato e a instrução compartilhados.
O encoder preserva papéis, identidades distintas e vínculos citados antes de
remover identificadores de controle do payload externo.

As respostas geradas escolhem seus pares fonte/trecho antes da síntese. O schema
permite apenas `kind=interpretation` para saídas do LLM; citações literais continuam
separadas nas fontes. Isso impede classificar uma paráfrase como transcrição
literal, sem reescrever o resultado depois da geração. O mock continua retornando
somente citações literais verificadas.

## Pesquisa dirigida pelo modelo (OpenAI)

A consulta OpenAI usa chamadas de ferramentas antes da síntese. O modelo decide
se precisa localizar contatos, listar conversas, buscar mensagens ou memórias,
ler páginas de uma conversa ou expandir o diálogo. Cada resultado volta ao modelo,
que pode consultar novamente e então selecionar as mensagens originais que
fundamentarão a resposta. Isso permite consultar histórico ainda sem embeddings,
sem reprocessá-lo em lote. Mock e provedor local mantêm seu fluxo anterior.

A API intermediária oferece somente operações de leitura com argumentos tipados;
não aceita SQL, URLs ou caminhos. Referências opacas valem apenas durante a
consulta. Filtros explícitos de contato, conversa e período são limites impostos
pelo servidor. A identificação usa cadastro e nomes observados na autoria ligados
ao contato: uma menção no corpo não identifica o interlocutor. Nomes iguais não
mesclam pessoas. O modelo pode indicar ambiguidade e pedir seleção da conversa.

Por padrão há até oito etapas e 90 segundos de pesquisa. O servidor limita a
inspeção a 64 mensagens/48 mil caracteres e a seleção final a 40 mensagens/24 mil
caracteres. Mensagens acima de oito mil caracteres são omitidas integralmente;
não se trunca texto usado como citação. Omissões e pesquisa incompleta são
indicadas. Os limites não representam cobertura completa de todo o histórico.
As fontes inspecionadas são revalidadas contra revisão, edição e revogação antes
da resposta. Falhas do provider não acionam fallback para mock ou IA local.

`research_steps` registra ferramenta, quantidade retornada e estado, sem expor
raciocínio interno ou argumentos privados. A extensão apresenta esse registro
na resposta. Datas relativas recebem o relógio da aplicação e o fuso informado.
Cada etapa pode adicionar chamadas, latência e consumo OpenAI; o limite de etapas
não é um orçamento monetário. O processamento automático continua restrito às
mensagens novas conforme o marco já configurado.

A pesquisa longitudinal também oferece `search_timeline`: originais relevantes
distribuídos por mês, contagens e datas de cada período, com até 12 meses e cinco
mensagens por mês em cada chamada. Termos raros no escopo têm maior peso que o
nome de projeto repetido em mensagens rotineiras. A amostra não implica leitura
completa de cada mês; é possível refinar os termos e o período. A busca geral
pagina uma janela de até 100 candidatos ranqueados. O contexto inicial informa
a faixa real do histórico permitido, e as instruções distinguem envio da mensagem
de vigência do acordo. A última chamada é reservada à seleção de evidências,
sinalizando limite atingido quando necessário. [Avaliação com meses simulados](LONG_HISTORY_EVAL.md).
