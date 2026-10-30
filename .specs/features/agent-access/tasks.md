# Plano técnico — acesso por agentes

## Decisões

- **Dois executáveis, um runtime.** O build passa a usar `Pace.spec` (PyInstaller) com dois `EXE`
  sobre o mesmo `Analysis`/`COLLECT`: `Pace.exe` (janela, como hoje) e `pace-cli.exe` (console).
  Ambos entram por `main.py`; `pace-cli` despacha para `whisper_dictation.cli`.
  Motivo: um exe de janela não tem stdout confiável em terminal e o PowerShell não espera por ele.
- **Trabalho = gravação da biblioteca.** `cli.transcribe` cria a gravação (`origin=import`,
  `source_path`, `requested_by=agent`, `source_sha256`), grava o snapshot da configuração e inicia
  `Pace.exe --process-recording` destacado, salvando `worker_pid`. Mesmo código das importações.
- **Coexistência com o PACE aberto.** `MeetingService._recover` ignora gravações cujo `worker_pid`
  ainda é um `Pace.exe` vivo; `MeetingService.busy` conta esses trabalhos (bloqueia "Reiniciar para
  atualizar"). Se o processo do agente morrer, o PACE aberto retoma pela fila (checkpoints).
- **MCP sem SDK.** Implementação própria de stdio JSON-RPC (initialize, ping, tools/list,
  tools/call, notificações). Versões de protocolo aceitas: 2025-06-18, 2025-03-26, 2024-11-05.
- **Conexão com agentes.** Claude Code: `claude mcp add-json --scope user` quando o `claude` existir;
  senão edição atômica de `~/.claude.json`. Demais: JSON (`mcpServers`) ou bloco TOML
  `[mcp_servers.pace]` no Codex. Estado em `PaceData\agents\state.json`; backups em
  `PaceData\agents\backups`.
- **Atualização.** `update_helper` (1.6+) faz backup/rollback de `pace-cli.exe` e encerra processos
  com imagem dentro da instalação antes do instalador. Instalador: `pace-cli.exe` em `InstallDelete`
  e `CloseApplicationsFilter`; `UninstallRun` desconecta agentes.
- **Laboratório.** GitHub simulado dentro da Windows Sandbox (hosts + certificado de teste + servidor
  HTTPS em PowerShell) para exercitar o fluxo real de atualização das versões publicadas, sem alterar
  o atualizador de produção.

## Tarefas

1. `cli.py`: transcribe/status/result/wait/list/cancel/doctor + formatação texto/json/srt + testes.
2. Store: campos `requested_by`, `source_sha256`; `meeting_service` com proteção de trabalhos externos + testes.
3. `mcp_server.py` + testes de protocolo (subprocesso real em stdio).
4. `agent_skill.md` + `agents.py` (detecção, connect/disconnect, refresh) + testes em perfil falso.
5. Botão em Configurações → Sobre; refresh pós-atualização no início do app.
6. `update_helper`: pace-cli no backup/rollback; encerrar processos da instalação + testes.
7. Build: `Pace.spec` com dois EXE; `build.ps1` usa a spec; `installer.iss`; versão 1.6.0.
8. Laboratório: `scripts/lab/` (wsb, servidor GitHub falso, roteiro) + relatório.
9. Revisão independente → correções → nova rodada.
10. Release notes, PR, publicação (com autorização) e verificação pós-publicação.
