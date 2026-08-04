# Como o AsterMem funciona

A maioria dos produtos de "memória para IA" joga suas palavras em uma caixa-preta — você nunca sabe o que foi lembrado, por quê, nem quando vai ressurgir. O AsterMem segue outro caminho: **sua memória é, antes de tudo, um patrimônio seu; contexto para a IA vem em segundo lugar.** Este documento explica cada decisão de design por trás do framework.

## 1. O texto original é a única verdade

Cada memória é armazenada como Markdown puro. Tudo o que a IA gera — resumos, tags, seu perfil — é um **derivado** que pode ser reconstruído a partir da fonte a qualquer momento.

Isso não é purismo. É uma defesa contra um caminho fatal de degradação: **paráfrases de paráfrases**. Um resumo é compressão com perdas; se o sistema continua resumindo os próprios resumos, cada passada se afasta mais do que você de fato escreveu — como fotocopiar uma fotocópia até as letras borrarem. Por isso o AsterMem impõe uma restrição rígida: **qualquer chamada de IA que produza ou reescreva uma conclusão deve receber o texto original como entrada.** Artefatos intermediários servem apenas de referência.

Você pode editar os arquivos MD com qualquer editor e o índice sincroniza automaticamente. Seus dados nunca ficam presos em um banco de dados — exportar é apenas copiar uma pasta.

## 2. Recuperação em dois níveis: documentos e trechos

Em um material de memória longo, geralmente só um ou dois parágrafos são relevantes para a pergunta em questão. O AsterMem divide automaticamente cada memória em **trechos (trunks)**, cada um com seu próprio resumo, suas tags e seu embedding. No momento da consulta:

- A **busca por palavra-chave** (índice de texto completo Whoosh) cuida dos acertos exatos: nomes, projetos, jargões
- A **busca semântica** (vetores) cuida da intenção difusa: "o que eu disse que era preciso tomar cuidado?"
- O **modo híbrido** combina as duas com RRF (Reciprocal Rank Fusion), com pesos dinâmicos conforme as características da consulta

A IA recebe resultados com a precisão do trecho, não documentos inteiros. Janelas de contexto são escassas — 500 palavras relevantes valem mais do que 5.000 fora do assunto.

## 3. Recuperação é navegação, não perguntas e respostas

Cada busca retorna mais do que resultados — retorna **orientação para o próximo passo**: IDs de memórias semanticamente próximas que não foram exibidas, tags encontradas nos acertos, documentos que valem a pena expandir. A IA não precisa adivinhar a próxima consulta; ela segue os vínculos intrínsecos do seu grafo de memórias.

Isso imita a forma como as pessoas refazem o caminho da memória: você não para no primeiro resultado da busca — você segue "aquela coisa que esta fonte mencionou" adiante.

## 4. Perfil: "quem é esta pessoa" em uma única chamada

Fazer a IA aprender quem você é do zero a cada sessão é o desperdício fundamental do chat sem estado. A camada de perfil do AsterMem sintetiza todo o seu acervo de memórias em um contexto denso que um agente recupera com uma única chamada `get_profile`.

O perfil tem três camadas de origem:

1. **Informações básicas** — campos estruturados como nome, profissão e fuso horário. A IA os preenche automaticamente a partir das suas memórias; você pode alterar qualquer coisa, e **depois que você edita um campo, a IA nunca mais mexe nele**. Cada alteração arquiva o valor anterior no histórico de versões.
2. **Sua própria apresentação** — Markdown escrito por você, repassado literalmente à IA. Nenhum caminho de código no sistema pode modificá-lo.
3. **O que a IA sabe** — observações sintetizadas a partir das suas memórias, organizadas em traços de longo prazo, atividade recente e uma visão geral de temas.

## 5. Toda frase escrita pela IA é rastreável

Toda conclusão que a IA escreve no seu perfil deve citar os IDs das memórias de origem. **Afirmações não rastreáveis são descartadas na camada de parsing** — não são revisadas e excluídas: elas nunca chegam a ser admitidas.

Geração e revisão são duas chamadas de IA independentes: primeiro se sintetizam conclusões candidatas, depois um auditor verifica cada uma contra o texto original — "a fonte realmente sustenta esta afirmação?". Uma retrospecção diária também percorre as conclusões existentes: fontes excluídas são marcadas como "fonte inválida", as que estão há muito tempo sem verificação como "possivelmente desatualizadas", e tudo cai em uma lista de pendências para o seu julgamento. **O sistema nunca exclui em silêncio, e nunca acredita em silêncio.**

## 6. Sonho: consolidação profunda de baixa frequência

A síntese diária só enxerga o incremento de cada dia; ela não consegue perceber padrões que atravessam meses. O AsterMem toma emprestada a ideia de "sonho" (consolidação offline) proposta por pesquisadores da Anthropic: reexaminar periodicamente todo o acervo de memórias — remover duplicatas, mesclar, resolver contradições, induzir temas de longo prazo.

O design essencial: **a consolidação profunda nunca entra em vigor diretamente.** Ela produz uma versão candidata; você revisa o diff (o que foi adicionado, mesclado, removido) e a adota ou descarta manualmente. A consolidação é orientada por eventos — conteúdo novo suficiente acumulado, pendências se empilhando, uma importação em massa concluída — e não um cron rígido. Ninguém faz faxina pesada em horário fixo; a gente limpa quando a bagunça aparece.

A consolidação profunda também tem um companheiro leve para o dia a dia: **a arrumação na escrita**. Sempre que uma memória nova chega, ela é pesada contra memórias semelhantes já existentes — uma decisão ultrapassada é substituída, um fato já registrado não é guardado duas vezes. A arrumação apenas arquiva, nunca apaga; cada decisão fica registrada com seu raciocínio na trilha de manutenção, e tudo que foi arquivado volta com um clique. Na dúvida, tudo é mantido. E se você prefere uma biblioteca sem intervenção, os resultados do sonho podem entrar em vigor automaticamente — mas só quando cada conclusão passa pela auditoria; qualquer ponto duvidoso continua esperando por você.

## 7. Visível, editável, desligável

Um perfil é o resumo que a IA faz de você — possivelmente errado, possivelmente parcial. Por isso o produto precisa garantir três coisas:

- **Sempre visível** — "o que os agentes veem" é exibido literalmente; não há prompts ocultos
- **Sempre editável** — toda conclusão pode ser mantida ou excluída, todo campo pode ser reescrito
- **Sempre desligável** — o perfil vem desativado por padrão; desligado, ele não faz nenhuma chamada de IA e não custa nada

Confiança não se constrói com promessas. Constrói-se com "você pode abrir e conferir a qualquer momento, e corrigir com um clique".

## 8. Feito para agentes

O AsterMem não é uma ferramenta tradicional de documentos — é um **backend de memória para agentes**:

- Uma API de ferramentas completa (busca, leitura/escrita, perfil) com autenticação por token Bearer e níveis de permissão de leitura/escrita/ações destrutivas
- Um pacote de Skill incluído: Cursor, Claude Code e outros agentes instalam e usam na hora
- `quick_match` retorna, em uma única chamada, o contexto temporal + os trechos mais relevantes + a orientação para o próximo passo, pensado para a abertura de sessões
- `capture_conversation` permite ao agente entregar uma conversa inteira: o texto é guardado literalmente, e o que merece ser lembrado a longo prazo é destilado em segundo plano em memórias independentes, cada uma ligada ao original — salvar não depende mais de o agente lembrar de salvar
- As respostas de busca operam sob um orçamento de caracteres e um limite de tempo rígido: por maior que a biblioteca fique, ela nunca trava o turno do agente

Você fornece o material de memória. A IA lembra quem você é. Isso é o AsterMem.
