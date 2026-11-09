# Importação WhatsApp TXT

Formatos sintéticos cobertos pelos testes:

```text
31/10/2026, 18:31 - João: Eu trabalho como engenheiro.
10/31/26, 6:31 PM - Alice: I work as an engineer.
[31/10/2026, 18:31:05] João: Eu prometo enviar o documento.
2026-10-31, 18:31 - Alice: Planejo mudar de emprego.
```

São aceitos separadores `/`, `-`, `.`, segundos opcionais, AM/PM com ou sem
pontos, caracteres direcionais comuns de exportações iOS, Unicode e mensagens
em múltiplas linhas. UTF-8 (com/sem BOM) e UTF-16 com BOM são suportados. Outras
codificações falham explicitamente para evitar corromper mensagens.

A interface exige `date_order` (DMY, MDY, YMD) e fuso IANA. Não é possível saber
se `01/02/26` é janeiro ou fevereiro apenas pela sintaxe. O ano de dois dígitos
significa 2000–2099 nesta versão, informado na prévia. Datas inválidas, fuso
inválido e horários locais ambíguos/inexistentes por horário de verão são rejeitados.

Linhas com data/hora reconhecidas iniciam mensagens. Demais linhas pertencem à
mensagem anterior; texto não vazio antes do primeiro cabeçalho é erro. O separador
de remetente é `: `; mensagens sem esse separador são do sistema. Nomes contendo
esse separador e mensagens de sistema com o mesmo padrão são ambíguos no TXT.
Uma continuação que imita exatamente um cabeçalho também não é distinguível;
a prévia alerta para essa limitação. A prévia retorna até 100 mensagens; a UI
mostra as primeiras dez e o total reconhecido.

Marcadores como `<Media omitted>`, `<Mídia oculta>`, `(file attached)` e
`(arquivo anexado)` são preservados como `media_reference`. Arquivos de imagem,
áudio e vídeo não são importados. Mensagens do sistema também são preservadas.

O commit exige o hash do arquivo da prévia. Cada arquivo tem no máximo o limite
configurado em bytes, 100 mil mensagens e 100 mil caracteres por mensagem.
O limite de corpo HTTP também cobre uploads chunked antes do parser multipart.
Linhas e conteúdo não são incluídos em erros comuns ou logs.
