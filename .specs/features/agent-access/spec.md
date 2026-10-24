# Acesso por agentes de IA — PACE 1.6.0

Pedido: permitir que agentes de IA (Claude Code, Claude Desktop, Codex, Cursor) transcrevam e
diarizem áudio/vídeo com o PACE **sem prompt manual**: o agente descobre o PACE sozinho, sabe
quando usá-lo e o que fazer com o resultado. Testar instalação limpa e atualização automática
neste computador, sem depender de terceiros, e garantir que nada novo quebre atualizações futuras.

## Requisitos

- **R1 — Comando de terminal.** Novo executável de console `pace-cli.exe`, na pasta da instalação,
  compartilhando o `_internal` do `Pace.exe` (sem duplicar o runtime). Subcomandos:
  `transcribe`, `status`, `result`, `wait`, `list`, `cancel`, `doctor`, `agents`, `mcp`, `--version`.
  - Funciona com o PACE fechado ou aberto. Cada trabalho vira um item da biblioteca do PACE
    (origem `import`), visível na janela de Gravações, com o mesmo pipeline das importações.
  - O processamento roda em `Pace.exe --process-recording` destacado (sem janela). `--no-wait`
    devolve o id na hora; `wait`/`status`/`result` acompanham.
  - Saída padrão em texto compacto para agentes (`[hh:mm:ss] SPEAKER_00: ...`); `--json` estruturado;
    `result --format srt|txt|json`. Progresso vai para stderr; resultado para stdout.
  - Códigos de saída estáveis: 0 ok · 1 erro · 2 uso inválido · 3 trabalho falhou · 4 tempo de espera
    esgotado (trabalho continua) · 5 cancelado.
  - Erros acionáveis em português ("sem chave do Groq: configure em PACE → Configurações ou use --local").
  - Reaproveita resultado pronto do mesmo arquivo (SHA-256) com as mesmas opções; `--force` reprocessa.
  - Nunca imprime chaves. `doctor` informa só se existem.
- **R2 — Servidor MCP** (`pace-cli mcp`, stdio, JSON-RPC 2.0, sem dependências novas).
  - `initialize` devolve `instructions` dizendo quando usar o PACE (áudio/vídeo de reunião, vídeo,
    áudio do WhatsApp) e o que fazer depois (nomear falantes, corrigir termos, resumir).
  - Ferramentas: `transcribe_audio`, `get_transcription`, `list_transcriptions`,
    `cancel_transcription`, `pace_status`. Descrições escritas para o agente escolher sozinho.
  - Espera limitada (`wait_seconds`, padrão 50 s) para não estourar o tempo das ferramentas; se não
    terminar, devolve id + etapa + instrução de continuar com `get_transcription`. Envia
    `notifications/progress` quando o cliente fornece `progressToken`.
  - Erros de ferramenta voltam como `isError` com mensagem acionável; o servidor nunca cai por erro
    de uma chamada.
- **R3 — Skill.** Um `SKILL.md` versionado no app (`pace-transcricao`) com: quando usar, como obter
  o arquivo (Downloads, WhatsApp, link de vídeo), como chamar (MCP ou `pace-cli`), pós-processamento
  (nomes, revisão da diarização, correção de termos) e modelo de resumo.
- **R4 — Conectar aos agentes.** `pace-cli agents status|connect|disconnect [--target ...]` e botão
  **"Conectar aos agentes de IA"** em Configurações → Sobre.
  - Detecta somente agentes presentes: Claude Code (`~/.claude.json`/`~/.claude`), Claude Desktop
    (`%APPDATA%\Claude\claude_desktop_config.json`, também a variante MSIX), Codex (`~/.codex`),
    Cursor (`~/.cursor`).
  - Registra o servidor MCP `pace` com caminho absoluto do `pace-cli.exe` e instala a skill onde o
    agente lê skills (`~/.claude/skills`, `~/.codex/skills`).
  - Edição segura: backup antes de alterar, escrita atômica, preserva o restante do arquivo, idempotente.
    `disconnect` remove só o que o PACE adicionou (registro em `PaceData\agents\state.json`).
  - Após atualização do PACE, a skill e o caminho registrados são atualizados automaticamente para os
    agentes já conectados (melhor esforço, nunca bloqueia a inicialização nem a confirmação da atualização).
  - Desinstalar o PACE remove as conexões feitas por ele.
- **R5 — Atualização automática não pode quebrar.**
  - A partir da 1.6, o auxiliar de atualização inclui `pace-cli.exe` no backup/rollback e encerra,
    antes de instalar, processos que rodam executáveis de dentro da pasta da instalação (servidores MCP
    abertos por agentes, trabalhos de transcrição), para o instalador não falhar por arquivo em uso.
  - "Reiniciar para atualizar" trata trabalhos de agentes em andamento como fila ocupada.
  - O PACE aberto não reprocessa nem "recupera" um trabalho cujo processo de agente ainda está vivo.
  - Instalador: `pace-cli.exe` em `InstallDelete`/`CloseApplicationsFilter`; sem alterar PATH nem
    registro além do que já existe.
  - Salto 1.5.2 → 1.6.0 continua usando o auxiliar da 1.5.2 (código já publicado): a 1.6 precisa
    funcionar mesmo assim, e o rollback desse auxiliar (que não conhece `pace-cli.exe`) não pode
    deixar o PACE quebrado.
- **R6 — Laboratório local (sem terceiros).** Script único que, no Windows Sandbox:
  instala do zero; valida `pace-cli`, MCP e conexão aos agentes (em perfis falsos); atualiza
  1.5.2 → 1.6.0 e 1.6.0 → 1.6.1-teste pelo **fluxo real do app**, com o GitHub simulado dentro da
  sandbox (hosts + certificado de teste + servidor local), incluindo um servidor MCP aberto durante a
  atualização; confere preservação de chave, configurações e biblioteca; grava relatório.
- **R7 — Revisão independente** do código e da spec por agente sem o contexto da implementação, com
  correções e nova rodada se houver achado grave.

## Fora do escopo

Baixar áudio de links (o agente faz isso; a skill ensina). Alterar PATH do sistema. Publicar no
GitHub sem autorização explícita.

## Critérios de aceite

1. `pace-cli transcribe <áudio WhatsApp .ogg/.opus | vídeo .mp4 | reunião longa>` gera transcrição com
   falantes; a gravação aparece na biblioteca do PACE.
2. Um Claude Code limpo, com o PACE conectado e **sem nenhum prompt sobre o PACE**, ao receber
   "transcreva este áudio <caminho>" usa o PACE sozinho.
3. Todos os testes unitários antigos e novos passam.
4. Laboratório: instalação limpa, 1.5.2 → 1.6.0 e 1.6.0 → 1.6.1-teste passam, inclusive com servidor
   MCP aberto; dados e chave preservados; rollback continua funcionando.
5. Revisão independente sem achados graves pendentes.
6. Publicação só após autorização do usuário; depois, verificação com o GitHub real.
