---
name: "MemorIA"
description: "Conversa legível com mensagens originais ao alcance."
colors:
  bg: "#f8f8f8"
  surface: "#ffffff"
  ink: "#202723"
  muted: "#58625c"
  line: "#d4d9d5"
  accent: "#17634b"
  soft: "#edf1ee"
  question: "#e0f2e5"
  error: "#a32c30"
  error-bg: "#fff0ef"
  button-text: "#ffffff"
  dark-bg: "#181d1a"
  dark-surface: "#202722"
  dark-ink: "#edf1ee"
  dark-muted: "#b1beb5"
  dark-line: "#424e46"
  dark-accent: "#9bd7b3"
  dark-soft: "#29332d"
  dark-question: "#2c4034"
  dark-error: "#ffb6b6"
  dark-error-bg: "#382526"
  dark-button-text: "#173424"
typography:
  wordmark:
    fontFamily: "Epilogue Variable, sans-serif"
    fontSize: "1.375rem"
    fontWeight: 700
    letterSpacing: "-0.025em"
  headline:
    fontFamily: "Catamaran Variable, sans-serif"
    fontSize: "1.5rem"
    fontWeight: 700
    lineHeight: 1.25
    letterSpacing: "-0.025em"
  title:
    fontFamily: "Catamaran Variable, sans-serif"
    fontSize: "1.125rem"
    fontWeight: 700
    lineHeight: 1.35
  body:
    fontFamily: "Catamaran Variable, sans-serif"
    fontSize: "1rem"
    lineHeight: 1.5
  answer:
    fontFamily: "Catamaran Variable, sans-serif"
    fontSize: "1.25rem"
    lineHeight: 1.3
  label:
    fontFamily: "Catamaran Variable, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 600
  caption:
    fontFamily: "Catamaran Variable, sans-serif"
    fontSize: "0.8125rem"
    lineHeight: 1.5
rounded:
  citation: "4px"
  tag: "6px"
  logo: "8px"
  field: "10px"
  control: "12px"
  panel: "14px"
  composer: "15px"
  question: "16px"
spacing:
  7: "7px"
  10: "10px"
  12: "12px"
  14: "14px"
  16: "16px"
  18: "18px"
  20: "20px"
  24: "24px"
  32: "32px"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.button-text}"
    rounded: "{rounded.control}"
    padding: "10px 17px"
    height: "44px"
  button-secondary:
    backgroundColor: "{colors.soft}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "10px 17px"
    height: "44px"
  button-danger:
    backgroundColor: "{colors.error-bg}"
    textColor: "{colors.error}"
    rounded: "{rounded.control}"
    padding: "10px 17px"
    height: "44px"
  input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.field}"
    padding: "10px 12px"
    height: "44px"
  nav:
    backgroundColor: "{colors.bg}"
    textColor: "{colors.muted}"
  tag:
    backgroundColor: "{colors.soft}"
    textColor: "{colors.muted}"
    rounded: "{rounded.tag}"
    padding: "3px 8px"
  notice:
    backgroundColor: "{colors.soft}"
    rounded: "{rounded.control}"
    padding: "14px 16px"
  source-disclosure:
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "11px 13px"
  composer:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.composer}"
---

# Design System: MemorIA

## Overview

**Creative North Star: "Conversa com origem"**

MemorIA é uma superfície compacta de leitura e conversa: fundos minerais, tinta nítida e verde reservado para ações, seleção e sinais de conexão. O texto da resposta tem espaço para respirar; a mensagem original permanece ao alcance, sem competir com a leitura.

A direção B — Conversa foi aprovada em 6/11/2026 para o painel lateral do Chrome. O logo original fornecido permanece a identidade visual; a aproximação do comp não foi adotada. As regras abaixo descrevem a implementação, incluindo os temas claro, escuro e automático.

**Key Characteristics:**
- Leitura primeiro, controles por divulgação progressiva.
- Navegação inferior persistente com quatro destinos.
- Fontes inline, com autoria e data verificáveis.
- Superfícies neutras e acento verde nos dois temas.

## Colors

A paleta usa branco e cinza mineral no claro, neutros verdes profundos no escuro e um único acento verde. Os tokens sem prefixo são claros; `dark-` registra a substituição do mesmo papel no tema escuro. Os valores normativos estão no frontmatter.

### Primary

- **Verde de ação:** accent e dark-accent orientam botões, seleção, fontes e estado conectado; button-text acompanha seu contraste em cada tema.

### Neutral

- **Papel mineral:** bg é o plano de leitura; surface diferencia campos e popovers.
- **Tinta:** ink sustenta respostas e títulos; muted identifica contexto e metadados.
- **Regra e camada:** line separa regiões; soft agrupa informação secundária.
- **Pergunta:** question distingue a mensagem do usuário e estados suaves de sucesso.
- **Erro:** error e error-bg sinalizam falhas e ações destrutivas com texto explícito.

**The Green Action Rule.** Use o verde para ações, seleção, citações e conexão; a leitura principal usa tinta neutra.

## Typography

**Display Font:** Epilogue Variable, sans-serif, exclusivamente no nome da marca.
**Body Font:** Catamaran Variable, sans-serif, nas respostas, títulos, rótulos e controles.

As famílias variáveis são hospedadas localmente via Fontsource; licenças em `apps/extension/public/licenses`. A hierarquia é humana e compacta, sem títulos decorativos acima do conteúdo.

### Hierarchy

- **Wordmark:** peso (700), escala wordmark; reduz para (1.125rem) em larguras até (340px).
- **Headline:** escala headline para títulos de seção; a introdução vazia usa (1.875rem), entrelinha (1.2).
- **Title:** escala title para subtítulos.
- **Body:** escala body para texto e campos; respostas usam answer, e perguntas (1.125rem).
- **Label:** escala label para rótulos; navegação usa (0.8125rem), peso (500), passando a (700) na seleção.
- **Caption:** escala caption para ajuda; metadados variam de (0.6875rem) a (0.75rem). Datas e quantidades usam números tabulares.

**The Reading First Rule.** Mantenha a resposta aberta e revele fontes e diagnósticos sob demanda.

## Layout

O shell ocupa a altura disponível (100dvh), centralizado e limitado a (640px), com largura mínima do documento (280px). A grade tem cabeçalho (58px), conteúdo flexível e navegação inferior. Em janelas maiores que (640px), bordas laterais delimitam o painel; não existe composição desktop de largura total.

As páginas comuns rolam dentro do conteúdo, com respiro lateral (20px) e conteúdo limitado a (600px). O chat tem histórico com rolagem própria e compositor persistente acima da navegação. O seletor de conversa e a busca ficam no topo do chat; as opções complementares abrem um popover. A navegação divide igualmente os quatro destinos Chat, Memórias, Importar e Ajustes.

A escala de espaçamento registra distâncias recorrentes, não uma grade rígida. Até (340px), o respiro lateral cai para (16px), a resposta usa (1rem) e formulários de duas colunas viram uma coluna. A navegação respeita a área segura inferior. A revisão observou uso em (300px), (390px), (480px) e painel centralizado em janela de (1440px); a comparação automática da janela larga com o comp em retrato falhou por proporções diferentes e não representa um gate aprovado.

## Elevation & Depth

O sistema é plano: fundo, superfície, camadas tonais e separadores finos criam ordem. O popover das opções do chat é a única superfície com sombra difusa observada (0 8px 28px #00000018).

**The Flat Surface Rule.** Use separadores e camadas tonais para organizar conteúdo; reserve a sombra para o popover de opções.

## Shapes

Controles têm cantos suaves: campos usam field, botões e disclosures usam control. O compositor usa composer; a pergunta e o seletor de arquivo usam question. Citações compactas usam citation, etiquetas usam tag. Listas de memórias e evidências são linhas separadas, sem cartões externos. Ícones de traço consistente acompanham rótulos ou nomes acessíveis; o status usa um ponto circular e texto.

## Components

### Buttons

Ações primárias usam accent e button-text; secundárias usam soft e ink; destrutivas usam error e error-bg. Botões de formulário têm altura mínima (44px), peso (650) e escala (0.9375rem). Hover primário mistura accent com ink (12%); secundário passa a question. Links de ação sublinham no hover. Foco visível usa contorno accent (2px) com afastamento (3px); desabilitados reduzem opacidade para (0.48).

### Chips

Etiquetas são pequenas, com fundo soft e texto muted. Falhas usam a variante error/error-bg. Não são controles de navegação.

### Cards / Containers

Avisos usam camada soft e cantos control; falhas têm cor e texto explícitos. Memórias, histórico de importação e evidências usam separadores. O seletor de arquivo é uma superfície com contorno tracejado; a prévia aparece antes de confirmar a importação.

### Inputs / Fields

Campos usam surface, borda line (1px), cantos field e altura mínima (44px). O seletor de escopo é a exceção compacta (40px). O compositor não encolhe com o histórico: textarea cresce de (52px) até (130px), com botão de envio (38px) no canto inferior. O contorno do compositor assume accent no foco interno.

### Navigation

Quatro colunas inferiores com ícone de traço (23px) e rótulo visível. Cada destino tem altura mínima (54px); ativo usa accent e peso (700), hover usa soft. A barra permanece separada por uma linha superior, inclusive em telas estreitas.

### Sources and progressive disclosure

Números de citação abrem a origem correspondente. O resumo das fontes é contornado; originais expandem inline, exibindo autoria, data e conteúdo apenas quando abertos. Metadados técnicos têm um segundo disclosure. A revelação e a seta usam (180ms) com `cubic-bezier(0.16, 1, 0.3, 1)`; botões usam transição de cor (160ms ease-out). Reduced-motion remove transições/animações e usa deslocamento imediato ao seguir citações.

Ajustes agrupa servidor, WhatsApp, inteligência/privacidade, consumo, aparência, MCP e dados. Diagnósticos ficam em níveis secundários. Opções do chat retém filtro de autor, limpeza, atualização e explicação de privacidade. Estados de carregamento, vazio, conexão, erro, confirmação de exclusão e importação continuam explícitos; exemplos de revisão são sintéticos.

## Do's and Don'ts

### Do:
- **Do** preservar o logo original e os nomes em português.
- **Do** manter Chat, Memórias, Importar e Ajustes acessíveis na navegação inferior.
- **Do** manter o compositor visível enquanto o histórico do chat rola.
- **Do** revelar fontes inline, mantendo autoria, data e detalhes secundários.
- **Do** respeitar prefers-reduced-motion em animações e deslocamento por citação.

### Don't:
- **Don't** transformar listas de memórias em cartões aninhados.
- **Don't** exibir métricas e diagnósticos avançados como conteúdo principal.
- **Don't** usar o logo aproximado do comp como substituto da marca original.
- **Don't** esticar o painel de leitura para preencher uma janela desktop larga.
