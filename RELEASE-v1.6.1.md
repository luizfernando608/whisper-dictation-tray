# PACE 1.6.1 — aba Agentes de IA e suporte ao Antigravity

- **Nova aba "Agentes de IA"** em Configurações: mostra, para cada agente do computador, se está
  conectado, disponível ou não instalado, e traz os botões Conectar e Desconectar. O que era uma seção
  dentro de "Sobre" agora tem lugar próprio.
- **Google Antigravity** entra na lista (Claude Code, Claude Desktop, Codex, Cursor e Antigravity): o
  PACE registra o servidor MCP e a skill `pace-transcricao` e pede para reiniciar o Antigravity. Nunca
  sobrescreve um servidor `pace` que não seja do PACE; arquivos alterados ganham backup.
- `pace-cli agents --target antigravity` para conectar ou desconectar só ele.
- **Reuniões gravam a voz dos outros participantes em qualquer saída**: fone com fio, headset
  Bluetooth (inclusive no modo viva-voz), alto-falante ou monitor. Antes só a saída padrão do Windows
  era gravada, e quem ouvia a reunião pelo fone perdia o outro lado. Se um fone conectar ou desconectar
  no meio da reunião, a captura se ajusta sozinha, sem parar a gravação.
- **Ditado sem limite de tempo também em instalações antigas**: quem veio de versões anteriores à 1.5
  ainda tinha o limite antigo salvo (2 min, ou 30 min na 1.4), e o ditado parava sozinho. Esses limites
  padrão antigos agora viram "Sem limite". Um limite escolhido nesta versão continua respeitado.
