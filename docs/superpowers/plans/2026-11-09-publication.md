# Preparação para publicação

Escopo: README detalhado e publicação do código existente em davidlbg/memorIA, sem alterar regras do produto ou iniciar serviços pessoais.

- [x] Conferir configuração real e documentar instalação limpa, operação, limites e segurança.
- [x] Proteger artefatos privados no Git e nos contextos Docker; preservar relatórios locais fora da publicação.
- [x] Executar typecheck, lint, testes, build e testes de banco descartável.
- [x] Revisar arquivos publicáveis e documentação com contexto independente.
- Publicação após esta preparação: commit e push normal; conferir o SHA remoto.

Referências: README.md, docker-compose.yml, infrastructure/personal.compose.yml, scripts/setup.py, scripts/prepare_personal.py, scripts/personal.py e backend/app/config.py.

Revisão: procurar credenciais/dados pessoais, comandos não reproduzíveis, promessas sem implementação e divergências entre ambientes. Não ler segredos nem executar provedores externos.

Decisão: a solicitação autoriza a publicação no destino indicado. Não atribuir licença ao projeto sem decisão do proprietário. Relatórios operacionais anteriores são preservados em arquivo local ignorado.
