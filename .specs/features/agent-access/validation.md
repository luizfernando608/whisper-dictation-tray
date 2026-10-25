# Validação — acesso por agentes (1.6.0)

## Revisão independente (rodada 1)

Revisor sem o contexto da implementação, somente leitura, sobre o commit 5bfe9fc. 12 achados, todos
corrigidos com testes de regressão em `tests/test_agent_access.py` (`ReviewFindingTests`,
`McpRobustnessTests`, `UpdateSafetyTests`):

| # | Gravidade | Achado | Correção |
|---|---|---|---|
| 1 | alta | Retomada pelo PACE aberto reescrevia o snapshot e ignorava `local_only`/idioma | o worker reaplica `job_options` (`apply_job_options`) qualquer que seja o snapshot |
| 2 | média | Dois workers no mesmo trabalho (janela sem dono) | dono vivo gravado na mesma transição (`processes.owner`), `transition(expect=...)` compara o dono; `_recover` usa compare-and-set |
| 3 | média | Rollback falhava se um agente reabrisse `pace-cli.exe` durante a instalação | encerra processos da instalação antes de restaurar e repete a restauração uma vez |
| 4 | média | `in_sandbox.ps1` podia rodar fora da sandbox | exige usuário `WDAGUtilityAccount` e o marcador `pace-lab.marker` criado por `run_lab.ps1` |
| 5 | baixa | PID reutilizado parecia worker vivo | `worker_started` + horário de criação do processo (GetProcessTimes) |
| 6 | baixa | Rollback feito pelo auxiliar da 1.5.2 deixa `pace-cli.exe` da 1.6.0 na pasta | **conhecido e inofensivo** — ver abaixo |
| 7 | baixa | Trabalho preso em `resuming` se o pace-cli morresse | `resuming` com dono morto é retomável |
| 8 | baixa | `cancel` alterava gravação em andamento no PACE | cancelamento só para fila/processamento |
| 9 | baixa | `params` inválido derrubava o servidor MCP | valida objeto (-32602) e isola erro por mensagem |
| 10 | baixa | Saída do `claude mcp` decodificada em cp1252 | `encoding="utf-8", errors="replace"` e fallback para o arquivo |
| 11 | baixa | Laboratório repetia a última linha e podia abortar lendo `report.json` pela metade | corrigido |
| 12 | baixa | Verificações do laboratório podiam passar sem testar o que dizem | rollback exige "Binários anteriores restaurados" no log; desinstalação confere código 0 e preservação de `numStartups` e da config do Claude Desktop |

### Limitação conhecida (achado 6)

O salto 1.5.2 → 1.6.0 é aplicado pelo auxiliar da **1.5.2**, já publicado, que só faz backup e
restauração de `Pace.exe` e `_internal`. Se esse salto falhar e for revertido, o `pace-cli.exe` da 1.6.0
fica ao lado do runtime da 1.5.2. Nada aponta para ele (agentes só são conectados a partir da 1.6.0, pelo
botão), a próxima instalação o substitui (`InstallDelete`) e a desinstalação da 1.6+ o remove. Não há
como mudar o comportamento do auxiliar já publicado; a partir da 1.6.0 o backup inclui `pace-cli.exe`.

## Laboratório (Windows Sandbox)

Pendente: requer o recurso Windows Sandbox ativado (administrador + reinício).
