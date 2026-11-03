# PACE 1.6.1 — aba Agentes de IA e suporte ao Antigravity

- **Nova aba "Agentes de IA"** em Configurações: mostra, para cada agente do computador, se está
  conectado, disponível ou não instalado, e traz os botões Conectar e Desconectar. O que era uma seção
  dentro de "Sobre" agora tem lugar próprio.
- **Google Antigravity** entra na lista (Claude Code, Claude Desktop, Codex, Cursor e Antigravity): o
  PACE registra o servidor MCP e a skill `pace-transcricao` e pede para reiniciar o Antigravity. Nunca
  sobrescreve um servidor `pace` que não seja do PACE; arquivos alterados ganham backup.
- `pace-cli agents --target antigravity` para conectar ou desconectar só ele.
