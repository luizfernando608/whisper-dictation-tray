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

## Revisão independente (rodada 2)

Sobre o commit 5cb7e0b: nenhum achado crítico ou alto. Corrigidos: linha reivindicada pelo próprio
processo (PACE aberto ou servidor MCP) não conta como viva se a gravação final no banco falhar, e as
escritas de estado do PACE aberto passaram a comparar o dono; o laboratório registra relatório quando
roda fora do ambiente esperado. Fica registrado, sem correção: se o `pace-cli` morrer exatamente entre
iniciar o worker e gravar o PID dele, uma retomada pode iniciar um segundo worker (janela de
milissegundos, já existente antes; o resultado é o mesmo, apenas com custo de processamento dobrado).
Um teste de interface (`test_ctrl_f_can_be_captured`) falhou uma vez na suíte completa e passa sozinho
(instabilidade de atalho/GUI, não relacionada).

## Laboratório (Windows Sandbox) — 25/10/2026

Candidato 1.6.0 do commit c3f0e7f (árvore limpa); 1.6.1 de teste do mesmo código (não publicar); base
1.5.2 = instalador publicado (`Output/v1.5.2`). GitHub simulado dentro da sandbox; sem chaves reais.
Comando: `scripts/lab/run_lab.ps1 -Candidate Output/Pace_Installer.exe -Next Output/lab/1.6.1/Pace_Installer.exe`.

Resultado final (execução 5): **16/16**.

| Verificação | Resultado |
|---|---|
| Instala 1.5.2 do zero e abre na bandeja | ok |
| 1.5.2 → 1.6.0 pelo fluxo real do app (download, auxiliar, confirmação) | ok |
| Configurações, `.env` com chave e biblioteca preservados | ok |
| `pace-cli` (`--version`, `doctor` com chave detectada) | ok |
| Conectar aos agentes (Claude Code, Claude Desktop, Codex em perfis falsos) | ok |
| Servidor MCP: handshake, ferramentas, `pace_status` | ok |
| Transcrição offline de áudio .ogg pelo `pace-cli` e item na biblioteca | ok (25 s) |
| 1.6.0 → 1.6.1 com servidor MCP de agente aberto (encerrado pelo auxiliar) | ok |
| Dados preservados 1.6.0 → 1.6.1 | ok |
| Skill e MCP dos agentes atualizados para 1.6.1 sozinhos | ok |
| Instalador com falha: rollback restaura binários, 1.6.1 reabre, `pace-cli` e dados intactos | ok |
| Desinstalação: código 0, remove MCP/skill, preserva o resto das configs | ok |

O que as execuções anteriores revelaram:

- **Bug real corrigido (c3f0e7f):** num Windows limpo, clientes .NET enviam a primeira mensagem MCP com
  BOM UTF-8; o servidor respondia "Parse error". Agora o BOM é ignorado (teste de regressão).
- **Comportamento da 1.5.2 publicada:** "Reiniciar para atualizar" pedido enquanto o download da
  verificação de inicialização ainda corre é ignorado sem aviso (trava interna); a atualização acontece no
  próximo pedido. Não afeta quem clica após o aviso "pronta"; não é possível mudar a 1.5.2. Melhoria
  possível numa 1.6.x: enfileirar o pedido em vez de descartá-lo.
- **Sobra de download:** se o PACE fecha no meio do download, o `.partial` (≈400 MB) fica no cache de
  atualizações (comportamento já existente). Melhoria possível: limpar `.partial` antigos.
- Correções no próprio laboratório: alias `cli` do PowerShell, espera pelo `result.json` do auxiliar,
  pedido de atualização só após o download, `stderr` do `pace-cli`/MCP guardados como evidência.

Limites: um único Windows (o do host, na sandbox); não cobre Windows 10, CPU sem AVX2 nem usuário sem
privilégio de administrador (o usuário da sandbox é administrador; o instalador não pede elevação).
