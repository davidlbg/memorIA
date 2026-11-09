# Avaliação de históricos longos — 05/11/2026

## Dados e critérios

O script `scripts/check_long_history.py` exercita o endpoint real de consultas,
com OpenAI e banco sintético separado, em 127.0.0.1:58004. O conjunto
tem 3.341 mensagens em três conversas ao longo de 92 dias (agosto a outubro de
2026): projetos, responsabilidades e convivência. Há 12 mensagens rotineiras por
dia e marcos deliberadamente espaçados entre semanas e meses. Worker e indexer
da fixture permanecem parados. A pesquisa precisa consultar originais ainda sem
embeddings; nenhum histórico pessoal participa da avaliação.

Os 12 casos principais avaliam data atual versus previsão antiga, cronologia com
causas, estado conhecido numa data passada, teto contratual versus verba separada,
pedido versus autorização condicionada, exceção temporária, transferência aceita
de responsabilidade, possibilidade versus promessa, evolução de uma parceria com
limites declarados, versões conflitantes, ausência de evidência e troca de assunto.
Quatro casos adicionais testam comparação entre datas, diferença de valores,
entrega parcial versus conclusão e atribuição de prazo à pessoa/tarefa corretas.

Cada caso fixa antes da chamada conceitos esperados, mensagens decisivas que
precisam ter sido inspecionadas/citadas e conversa permitida. O relatório separa
falhas de recuperação das falhas da síntese. A checagem por expressões não é um
juiz semântico completo: as respostas sintéticas também são revisadas, incluindo
atribuição de declarações contestadas. Não se usa o próprio modelo para conceder
nota a si mesmo. A pergunta sobre um dado inexistente precisa retornar ausência
de evidência, não uma resposta inventada.

## Problemas encontrados e correções

- Vizinhos de uma mensagem relevante não cobrem mudanças ocorridas meses depois.
  A nova ferramenta `search_timeline` busca os termos fornecidos e distribui
  originais relevantes por mês. Retorna contagem e intervalo de resultados em cada
  mês, com até cinco originais por mês e 12 meses por chamada. São amostras
  identificadas como tal; datas e termos podem ser refinados pelo modelo.
  A relevância usa frequência dos termos no escopo: repetir o nome de um projeto
  em mensagens rotineiras não deve superar um termo específico de uma decisão.
- Uma comparação entre dias falhou porque o modelo inventou o ano de 2023 e
  restringiu buscas aos dias de vigência, excluindo mensagens anteriores que
  anunciavam o acordo. O contexto inicial agora inclui a faixa real do histórico
  dentro dos filtros fixos e distingue data de envio da vigência.
- A busca geral agora permite paginação dentro da janela de 100 candidatos
  ranqueados e informa `has_more`, `next_offset` e cobertura não exaustiva.
- Pesquisas podiam gastar todas as chamadas expandindo contexto e descartar
  evidências úteis. A última chamada oferece somente `finish_research`; atingir
  esse limite também sinaliza `research_limited`. Falhas continuam visíveis.
- A síntese passa a preservar datas, condições, destinatários de exceções,
  distinção entre proposta/aceitação/entrega e atribuição individual de versões
  contestadas. Uma ressalva posterior não legitima narrar a primeira versão como fato.

As rodadas exploratórias também detectaram fixtures antigas com outro projeto
chamado Aurora. A limpeza original por um único filename era insuficiente, porque
execuções anteriores usavam outros nomes de arquivo. O runner remove somente o
título sintético antigo exatamente identificado, no endpoint de fixture fixo.
Perguntas ambíguas com homônimos não têm um gabarito válido de uma única conversa;
essas primeiras rodadas não são uma medida comparável de precisão geral.

Uma rodada com corpus limpo, antes da ferramenta temporal, passou em 22/24
execuções. Falhou uma vez na recuperação do cancelamento intermediário e uma
vez na aceitação do acordo pessoal. Essas falhas motivaram a coleta por mês.

As ferramentas adicionais, sozinhas, não resolveram a capacidade do modelo:
outras rodadas do mini passaram em 21/24 e 20/24, com falhas também nos casos
adicionais. Trocar para GPT-4.1 ou usar GPT-5.4 mini sem raciocínio não resolveu
todos os quatro casos críticos. Nenhuma dessas versões foi implantada no ambiente
pessoal durante os ensaios.

A revisão 2 do corpus explicita que R$ 48 mil era o teto inicial do contrato.
O texto original só dizia “orçamento aprovado”, insuficiente para assumir sem
ressalvas que esse valor correspondia ao mesmo teto contratual. O gabarito não
deve exigir que o modelo invente essa regra de negócio. As rodadas são identificadas
por `dataset_revision`; notas de revisões diferentes não são comparações diretas.

## Transporte de raciocínio e citações

GPT-5.4 mini usa Responses com esforço configurável e `store=false`. A validação
final usa medium; as primeiras comparações usaram low. O loop preserva os
itens opacos de raciocínio entre as chamadas de ferramentas na memória da consulta,
sem persistir ou exibir seu conteúdo. Uma nova consulta reinicia esse estado.
Respostas incompletas, recusas e IDs de chamadas incorretos falham explicitamente.

A API também devolve um erro estruturado com as datas reais disponíveis quando
o período proposto pelo modelo não intersecta o histórico permitido. Não corrige
o ano automaticamente nem amplia filtros fixos. A pesquisa mensal inclui um
vizinho de cada lado e o original respondido, quando disponível, classificados
como contexto; a vizinhança não prova relação por si só. Isso permite verificar
uma aceitação curta que não repete as palavras do assunto, dentro dos mesmos limites.

O modelo de raciocínio recuperou corretamente três dos quatro casos críticos na
primeira rodada; o quarto foi rejeitado por citar dois trechos da mesma mensagem.
Também houve trechos redigitados com diferenças literais. O novo contrato usa um
objeto com uma chave por mensagem e seleção de `quote_ref` em opções originais
preparadas antes da geração. A API converte a referência escolhida para o trecho
literal correspondente. Não há correção de texto inventado, deduplicação tardia
nem relaxamento da validação. Referências desconhecidas continuam sendo rejeitadas.

As opções cobrem o original inteiro; textos longos usam trechos contíguos de até
2.000 caracteres com sobreposição. O texto privado fica no payload de evidência,
não nos enums do JSON Schema: o schema contém apenas IDs das opções. Testes
verificam cobertura do texto, literalidade, nulidade das fontes não selecionadas,
rejeição de chaves repetidas e ausência de texto original no schema.

`OPENAI_QUERY_MODEL` separa o modelo usado para pesquisar/responder do modelo de
extração em background. O override é opcional e não altera embeddings, identidade
de processamento ou marco de mensagens novas. `/health` expõe `query_model` e a
extensão apresenta esse campo como modelo de respostas.

## Resultado final

Na revisão 2 do corpus, GPT-5.4 mini (`gpt-5.4-mini-2026-03-17`) com raciocínio
medium passou nos 12 casos principais e nos quatro casos adicionais após revisão
das fontes. A checagem automática inicial marcou 15/16: o caso restante descrevia
corretamente que as demais unidades continuavam no horário padrão, mas o regex
exigia a palavra “não”. O avaliador passou a aceitar a paráfrase equivalente.
O relatório bruto foi preservado, e a revisão offline registra a alteração de
critério. Nenhuma resposta, fonte ou checagem de evidência foi alterada na revisão.

Os originais decisivos foram inspecionados e citados em todos os casos sustentados.
O caso sem evidência retornou claims vazio. O banco confirmou 3.341 mensagens-alvo
e zero embeddings dessas mensagens: a pesquisa funcionou sem indexação histórica.
A média foi 18,65 segundos, mediana 16,84 e máximo 32,53. Duas consultas atingiram
o limite de contexto e sinalizaram `research_limited`; seus resultados não equivalem
a cobertura completa da conversa.

Relatórios locais: `.local/long-history/medium-hardened-v2-core.json`,
`medium-hardened-v2-transfer.json`, seus pares `.reviewed.json` e `final-summary.json`.
As notas anteriores permanecem disponíveis. Isto é uma avaliação controlada de
16 cenários, não uma estimativa de precisão de produção ou garantia contra
omissões em novas conversas. Uma ocorrência de mistura de idioma numa rodada
anterior também mostra que a qualidade verbal pode variar, mesmo com fatos corretos.

121 testes backend passaram, além de 17 testes da extensão e 20 do conector.
Três integrações Node opt-in não foram executadas. Typecheck, mypy, lint e build
passaram. Permanecem cinco avisos Python de depreciação e avisos Rollup de
dependências, sem falhas de build. Não houve migration.

## Reprodução e limites

Com o serviço sintético configurado para OpenAI e seu conector de rede desabilitado:

```powershell
.venv/Scripts/python scripts/check_long_history.py --label core --repeat 2
.venv/Scripts/python scripts/check_long_history.py --label transfer --suite transfer --reuse
```

Os relatórios JSON em `.local/long-history/` contêm apenas respostas e referências
sintéticas. `--reuse` exige arquivos, títulos e contagens conhecidos, além de zero
mensagens processadas; não tenta reaproveitar arbitrariamente outra conversa.
A configuração da chave usa o formulário/API nativos, sem chave em código ou no
relatório. A fixture não deve permanecer com uma credencial real após os testes.

O conjunto contém ruído sintético repetitivo e três temas controlados, não o
vocabulário inteiro de uma conta real. A nota não estima precisão de produção nem
prova cobertura completa do histórico. A coleta temporal é textual, sem stemming
nem dedução automática de sinônimos; o modelo pode precisar reformular termos.
Não houve fine-tuning ou reprocessamento em lote do histórico pessoal.
As chamadas adicionais têm custo e latência variáveis.

O desenho segue a separação entre recuperação, uso de ferramentas e resposta
recomendada na [documentação oficial de avaliação da OpenAI](https://developers.openai.com/api/docs/guides/evaluation-best-practices).
O transporte segue a [documentação de raciocínio e estado sem respostas armazenadas](https://developers.openai.com/api/docs/guides/reasoning).
