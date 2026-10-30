# PACE 1.6.0 — agentes de IA usam o PACE sozinhos

Claude, Codex e Cursor deste computador agora transcrevem e identificam falantes com o PACE sem
nenhuma instrução manual: reuniões, vídeos e áudios do WhatsApp.

- **Conectar aos agentes de IA** (Configurações → Sobre): um clique registra o PACE no Claude Code,
  Claude Desktop, Codex e Cursor instalados e instala a skill `pace-transcricao`. A chave continua só
  no PACE. "Desconectar" e a desinstalação removem apenas o que o PACE adicionou; cada arquivo alterado
  ganha um backup antes.
- **Servidor MCP** (`pace-cli mcp`): os agentes veem as ferramentas `transcribe_audio`,
  `get_transcription`, `list_transcriptions`, `cancel_transcription` e `pace_status`, com instruções de
  quando usar o PACE e o que fazer com o resultado (nomes dos falantes, correção de termos, resumo).
- **pace-cli** na pasta do PACE, para terminal e agentes: `transcribe`, `wait`, `result` (texto, JSON,
  SRT), `status`, `list`, `cancel`, `doctor`. Cada trabalho aparece na biblioteca de Gravações, roda em
  segundo plano, continua se o agente ou o terminal fechar e reaproveita o resultado do mesmo arquivo.
  `--local` transcreve sem enviar o áudio para a nuvem.
- Atualização automática: o PACE aguarda transcrições de agentes em andamento, fecha servidores MCP
  abertos antes de instalar e inclui o `pace-cli` no backup e na recuperação após falha. Depois de
  atualizar, a skill e o caminho registrados nos agentes conectados são atualizados sozinhos.

O instalador fica maior (o `pace-cli` tem o próprio executável sobre o mesmo runtime).

Validação: testes automatizados, duas rodadas de revisão independente, agentes reais (Claude Code e
Codex) usando o PACE sem menção a ele no pedido, e laboratório local em Windows Sandbox com instalação
limpa, atualização automática real 1.5.2 → 1.6.0 e 1.6.0 → versão seguinte com servidor MCP aberto,
recuperação após instalador com falha e desinstalação. A publicação é uma etapa separada.
