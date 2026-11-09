# Orçamento compartilhado de IA

O controle é opcional e fica no backend. Pessoas que usam o mesmo backend dividem a mesma contabilização, incluindo consultas, extração de memórias e indexação. Não é um limite imposto à conta do provedor.

## Configuração

Exemplo sintético para um teto de US$ 50:

```dotenv
AI_MONTHLY_BUDGET_USD=50
AI_BUDGET_PRIOR_SPEND_USD=0
AI_BUDGET_PRIOR_SPEND_MONTH=2026-11
```

Use o mês UTC e o gasto anterior reais da sua instalação. Omitir o teto desabilita a proteção. A composição pessoal define funções de uso separadas para API, worker e indexador. Se o indexador dedicado assumir os vetores dos originais, configure `WORKER_INDEXES_MESSAGES=false` para evitar trabalho redundante.

## Reservas e concorrência

Cada tentativa paga reserva margem antes do HTTP, em transação independente serializada no PostgreSQL. Reiniciar ou trocar a chave não zera o consumo. A reserva é conservadora e pode impedir uma chamada antes de o saldo estimado chegar a zero; não equivale a cobrança.

Um recibo válido liquida a reserva com o uso retornado. Timeout, erro de resultado financeiro incerto ou recibo inconsistente mantém a reserva. Não há liberação automática por idade nem repetição silenciosa de chamadas incertas. O processamento automático da finalidade afetada pode ficar pausado até conferência.

A captura do conector não exige saldo de IA. Trabalhos derivados podem aguardar orçamento, preservando os originais. Um novo mês abre outro período; o gasto anterior informado só se aplica ao mês correspondente. Configurações divergentes entre processos falham antes de chamar o provedor. Alterar um teto já persistido exige uma intervenção explícita e auditada.

## Consulta e limites

`GET /api/v1/settings/usage` é autenticado e lê apenas o banco. Ajustes mostra estimativas de consumo, reservas e saldo; as respostas podem incluir o custo estimado de suas chamadas. A contabilização não armazena o conteúdo de perguntas/respostas nem credenciais.

Os valores usam as tarifas codificadas na versão do backend. A fatura do provedor é a referência financeira: uso da chave fora deste backend, gastos anteriores desconhecidos e mudanças de tarifa não são reconciliados automaticamente. Confira os preços antes de uma implantação e mantenha o catálogo atualizado.

Chamadas incertas exigem conferência com o provedor antes de qualquer ajuste. Pare processos pagos antes de reconciliar e preserve valor e justificativa; não libere reservas simplesmente por estarem antigas. Não há endpoint público de conciliação automática.

Migração responsável: `0009_ai_usage_budget`. A suíte de backend cobre concorrência de reservas, rollback, virada de mês, configurações divergentes e falhas HTTP com provedores simulados.
