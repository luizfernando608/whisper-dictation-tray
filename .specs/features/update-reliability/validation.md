# Evidências — incidente de atualização em 21/10/2026

Resultado técnico: todos os cinco gates abaixo foram aprovados e revisados independentemente. O candidato é local e a publicação continua em 1.5.0; a causa específica no outro PC ainda não foi confirmada. Os ensaios usam isolamento de processos/pastas/dados neste Windows x64, sem VM.

## Diagnóstico confirmado e limites

O endpoint público continua em `v1.5.0`. O asset foi baixado novamente: 327.443.171 bytes, SHA256 `c1e373aeb939893d6459262e9685c708260c4a2f794e65e9a71b86ec1c7bad89`, igual ao pacote publicado. A base pública 1.4.0 também foi baixada: 84.489.954 bytes, SHA256 `16fe92ca5e0b4a909dc76098f2e24c07d194532932ad4e8b6b98e7783366f7ea`.

Nas tags 1.3.2 e 1.4.0, o downloader é idêntico: grava diretamente no mesmo arquivo temporário e aceita EOF sem comparar tamanho/hash. Uma resposta HTTP real da biblioteca padrão com Content-Length de 327.443.171 e corpo de 100 bytes termina sem exceção quando lida em blocos; o código antigo avançou para o lançamento. Revisão independente reproduziu inclusive corpo vazio. Dois downloads antigos concorrentes também compartilham o mesmo destino. Esses mecanismos são compatíveis com o relato; sem mensagem exata/arquivo do outro computador, não se deve afirmar que a causa remota está comprovada. A [FAQ oficial do Inno](https://jrsoftware.org/isfaq.php) relaciona a mensagem de corrupção a tamanho/CRC incorretos.

A proteção da 1.5.0 não está presente no cliente antigo antes do primeiro salto. Alterar o aplicativo novo não atualiza retroativamente o downloader antigo. O ensaio anterior de instalação havia iniciado diretamente o helper novo, e o download público anterior não havia sido executado: esses resultados não comprovavam o fluxo completo do cliente antigo.

## Correções do candidato

Commit de produção `cf06b9d`: até três tentativas novas para falhas transitórias de download/tamanho/hash, mantendo arquivo parcial único e publicação atômica; erros permanentes de HTTP/certificado não são repetidos. O auxiliar valida o pacote e publica um receipt autenticado pelo token/PIDs/pasta/versão antes de o aplicativo poder encerrar. Falha ou prazo de bootstrap mantém o aplicativo aberto e encerra somente o auxiliar iniciado; diagnóstico em `bootstrap.log`.

Reprodução adicional em Windows com `LongPathsEnabled=0`: caminhos de staging/snapshot maiores que 300 caracteres falharam com WinError 3/206. Cópia, restauração e limpeza agora usam aliases Win32 estendidos somente para I/O, depois das guardas de contenção/reparse. Planos, comandos e receipts continuam com caminhos lógicos normais. Não se alterou a política do Windows. Quatro testes reais de arquivo longo passaram.

O instalador declara `ArchitecturesAllowed=x64compatible` e Windows 10 mínimo, coerentes com o pacote AMD64 e requisitos das dependências. Não se mudou o modo de registro da instalação. Referências: [Inno/arquitetura](https://jrsoftware.org/ishelp/topic_setup_architecturesallowed.htm), [Inno/MinVersion](https://jrsoftware.org/ishelp/topic_setup_minversion.htm), [PyTorch](https://github.com/pytorch/pytorch/blob/main/RELEASE.md), [OpenVINO](https://docs.openvino.ai/2026/about-openvino/release-notes-openvino/system-requirements.html).

## Ciclo de revisão

Testes vermelhos antes da implementação; 44 testes de updater passaram após as correções. Integração: 12 aprovados e um skip de symlink por privilégio Windows. Revisão independente da implementação inicial e da correção de caminhos longos: sem achados de produção pendentes. Um teste adicional confirma que uma exceção de bootstrap não chama `_shutdown_app`, libera `_restarting` e mantém o aplicativo disponível.

O revisor apontou que o harness legado com `/SILENT` não reproduz os argumentos do botão real, que não usa esse parâmetro. Esse ensaio não será apresentado como validação do botão. O primeiro ensaio silencioso expirou no prazo de 240 s do próprio validador durante um download lento; nenhum instalador foi iniciado e o host/dados foram restaurados. A repetição teve prazo compatível com o tamanho do pacote e usou o botão/assistente reais.

Suíte completa após as correções: 252 testes executados em 75,121 s, 251 aprovados e um skip de symlink Windows. Os quatro smokes ONNX CPU/OpenVINO CPU/OpenVINO Intel GPU/DirectML passaram; erro máximo 4,77e-7. Evidência: `temp/update-incident-20261021/full-tests.log`.

## Escopo inicial dos gates

Suíte completa, novo pacote congelado e comparação das fontes; botão real legado com o asset público; staging/handshake do helper congelado; instalação/upgrade/rollback do candidato e caminhos distintos. Não há Windows Sandbox/VM disponível: são processos/pastas/dados separados no mesmo Windows x64, e não provas em outros sistemas operacionais. Nenhum asset público foi substituído.


## Segunda rodada de correção

O ensaio nativo chegou ao fim do assistente com o instalador público íntegro, argumentos vazios e processo filho do PACE 1.4 real. A versão 1.5.0 abriu e os canários foram preservados. A limpeza do harness encontrou uma corrida de `psutil.NoSuchProcess` ao enumerar uma segunda instância que encerrava; por isso o relatório geral desse ensaio permanece reprovado. A prévia original 1.5.1 foi restaurada explicitamente, com hashes dos 20 WAVs/configuração/credenciais iguais (`legacy-ui-host-recovery.json`). Foram corrigidas a classificação de processos já encerrados, a preservação da identidade dos objetos de processo e a persistência do resultado final mesmo quando reabrir a prévia falha. Dois testes de ownership passaram, com RED→GREEN para a regressão apontada pelo revisor.

O primeiro bootstrap congelado no cache longo falhou antes do READY: a entrada global de `main.py` carregava `audio.py`, que carregava PortAudio, cujo caminho de DLL excedia MAX_PATH (erro 0xce). O import de listagem de áudio foi movido para o ramo `--list-devices`. Um teste que torna áudio indisponível reproduziu o problema antes da alteração e passou depois. Revisão independente aprovou a correção. O primeiro pacote dessa rodada foi descartado; o executável final foi reconstruído a partir do commit de produção `5745ed0`.

Após a correção da entrada, a suíte completa teve 253 casos, 252 aprovados e um skip de symlink, mais os dois casos focados de ownership adicionados depois. Os quatro smokes de inferência passaram. Naquele ponto a segunda execução nativa e o novo bootstrap real ainda precisavam concluir; os resultados finais estão abaixo.


O bootstrap congelado após a correção passou no cache longo: helper real copiado, receipt antes de sair o pai, contexto frozen herdado reiniciado e falha precoce real sem aceitar prontidão. Evidência: `C:/Users/ziull/Documents/pace-bootstrap-20261021/Perfil João/cache-redirecionado-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb/report.json`. Esse teste declara explicitamente pai venv com flags frozen simulados e pacote público semeado no cache; o helper/EXE/DLLs são reais e o instalador não é executado. ChildJob mantém os helpers contidos.

Executável final `4f1ca0bc1c77cb6e2fccf1d606f00400a58c7ac3be8d03a460d930744c606a4f`: 32 módulos e main iguais às fontes compiladas. Runtimes congelados ONNX/OpenVINO CPU/Intel GPU/DirectML passaram, erro máximo 4,77e-7. Evidências: `Output/v1.5.1-updatefix/source-verification.json` e `temp/update-incident-20261021/final-frozen-runtime.log`.


## Repetição nativa aprovada

O segundo ensaio do botão real terminou aprovado, inclusive a limpeza: `C:/Users/ziull/Documents/pace-update-20261021/Computador João repetição/report-legacy-ui.json`. Instalou a base pública 1.4.0, clicou Sobre → Verificar atualizações → Atualizar agora e concluiu o assistente nativo 1.5.0 com argumentos vazios. O processo observado era filho do PACE antigo; o download real terminou com 327.443.171 bytes e SHA público correto. A versão/registro novos e canários foram conferidos. A prévia original foi reaberta, 20 WAVs/config/env intactos e `cleanup_errors=[]`. Revisão independente dos relatórios e identidades: sem achados.

O instalador candidato tem 327.395.783 bytes, SHA256 `412b84b17252cf565cf2d490a6e9c158b76f6ff8adc860016cf49f07aa554fca`, em `Output/v1.5.1-updatefix/Pace_Installer.exe`. Ainda não publicado.

## Reproduzir os gates reais

Os scripts são opt-in e serializados: salvam/alteram temporariamente registro e atalhos do usuário, isolam dados de teste, param a prévia somente se ociosa e a reabrem após restaurar integração/dados. Usar sempre uma pasta de saída nova. Não executar dois ensaios de instalação ao mesmo tempo por causa do IPC global e do registro Inno.

1. `validate_windows_installation.py --phase legacy-ui --old-version 1.4.0 --version 1.5.0 --old-installer <instalador público 1.4.0> --output <pasta nova> --preview <prévia>`; o driver nativo clica botão e assistente, preserva a identidade de `legacy-ui-ready.json` no marcador final e o harness confere independentemente download/processos/dados.
2. `validate_update_bootstrap.py --executable dist/Pace/Pace.exe --installer <instalador público íntegro> --version 1.5.0 --output <cache longo novo>`; pai venv/flags simulados, helper real e nenhuma execução de instalador.
3. `validate_windows_installation.py --single-window --installer <candidato> --old-installer <público 1.5.0> --version 1.5.1 --old-version 1.5.0 --output <pasta nova> --preview <prévia>`; instalação limpa, base antiga e upgrade confirmado.
4. Mesmos argumentos, `--phase rollback --helper-source <clean do passo 3>`; falha intencional de confirmação (versão esperada 9.9.9) após instalação real, exige restaurar runtime/integração/dados antigos.
5. Compilar `scripts/fixtures/update_abort.iss` com Inno (sobrescrever OutputDir para uma pasta de teste). Mesmo rollback, `--expected-failure installer --installer <fixture compilada>`; exige erro nativo do instalador, mantém todas as verificações comuns. A fixture retorna False em InitializeSetup e não altera arquivos/registro: valida falha antes de instalar, não falha na metade da extração.


## Gates do candidato concluídos até esta etapa

- Instalação limpa e atualização real 1.5.0 → 1.5.1 aprovadas: `C:/Users/ziull/Documents/pace-updatefix-install-20261021/report-install.json`, processo terminou com código 0. Configurações e biblioteca compartilham um HWND. Helper aguardou o pai antigo real; receipt/result confirmou 1.5.1 e dados canários permaneceram iguais. EXEs clean/upgrade/dist têm SHA `4f1ca0bc1c77cb6e2fccf1d606f00400a58c7ac3be8d03a460d930744c606a4f`. Host restaurado, 20 WAVs/config/env intactos, prévia reaberta, nenhuma falha de limpeza. Revisão independente: sem achados.
- Falha intencional de confirmação após instalar o candidato aprovada: `C:/Users/ziull/Documents/pace-updatefix-confirm-rollback-20261021/report-rollback.json`, código 0. Exigiu o erro de confirmação esperado, `rollback_error=null`, EXE público antigo SHA `299aad8a06801f72fcc9bca31a07216f057e7d366b20622b393ada709f66e9ab` e todos os 11.187 arquivos de runtime antigos iguais. Registro 1.5.0, atalhos, desinstaladores e canários restaurados. Host/dados/prévia também restaurados sem erros. Revisão independente conferiu relatório e hash do EXE.

- Falha nativa do instalador aprovada: `C:/Users/ziull/Documents/pace-updatefix-installer-rollback-20261021/report-rollback.json`, código 0. A fixture retornou código 1 antes de instalar, o helper registrou o erro esperado e `rollback_error=null`. EXE/runtime (11.187 arquivos), registro 1.5.0, atalhos, desinstaladores e dados canários conferidos. Host, 20 WAVs/config/env e prévia restaurados sem erros de limpeza. Revisão independente conferiu relatório e SHA antigo. O novo modo do harness e a fonte reproduzível da fixture estão no commit `5f46485`.

## Matriz final de verificação

| Gate | Resultado | Evidência |
| --- | --- | --- |
| Botão real do PACE público 1.4.0 → público 1.5.0 | Aprovado; rede real, asset íntegro, assistente nativo | `pace-update-20261021/Computador João repetição/report-legacy-ui.json` |
| Bootstrap congelado em cache longo | Aprovado; readiness e morte precoce | `pace-bootstrap-20261021/Perfil João/cache-redirecionado-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb/report.json` |
| Instalação limpa + upgrade real 1.5.0 → candidato 1.5.1 | Aprovado; confirmação real e janela única | `pace-updatefix-install-20261021/report-install.json` |
| Falha de confirmação após instalar | Aprovado; runtime/integração/dados restaurados | `pace-updatefix-confirm-rollback-20261021/report-rollback.json` |
| Recusa nativa antes de instalar | Aprovado; rollback completo e host restaurado | `pace-updatefix-installer-rollback-20261021/report-rollback.json` |

Todos os caminhos da tabela ficam sob `C:/Users/ziull/Documents`. Nos ensaios de instalação de candidato, o helper congelado é iniciado pelo harness; o gate separado de bootstrap cobre o staging e handshake reais de `restart_to_update`, com pai venv/flags frozen simulados e cache semeado explicitamente. Não se apresenta essa composição como clique no botão de uma release 1.5.1 pública, que ainda não existe. O ensaio público 1.4 → 1.5.0 usa efetivamente o botão congelado original e o asset da rede.

Os três gates finais terminaram com exit 0, `passed=true` e `cleanup_errors=[]`. Não restam achados de produção na revisão independente. Foram preservadas também as falhas iniciais de harness/bootstrap e suas correções, sem transformar retrospectivamente tentativas reprovadas em aprovação. A suíte completa final teve 253 casos (252 aprovados, um skip por privilégio de symlink), mais dois novos casos focados de ownership aprovados. O executável congelado passou nos quatro smokes de backend; fontes compiladas conferidas.

Consulta final ao GitHub confirmou `v1.5.0`, asset 327.443.171 bytes, digest `c1e373aeb939893d6459262e9685c708260c4a2f794e65e9a71b86ec1c7bad89`. `Output/Pace_Installer.exe` continua com esse mesmo SHA. Nenhum asset/release público foi criado, substituído ou apagado durante esta investigação.


## Ativação local após os gates

A prévia corrigida foi ativada em `C:/Users/ziull/AppData/Local/Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/PACE-Preview-1.5.1-updatefix`, copiando somente EXE/runtime validados e configuração existente. Não foi executado outro instalador nem alterado o registro de instalação. Cinco atalhos que apontavam à prévia anterior foram salvos e retargetados; nenhum outro atalho foi selecionado. O atalho principal do menu Iniciar abriu a biblioteca; abertura repetida e alternância configurações/biblioteca usaram o mesmo HWND. Relatório `C:/Users/ziull/Documents/pace-updatefix-preview-20261021/report.json`: `status=passed`, exit 0, 20 WAVs/config/env originais intactos, registro inalterado. A prévia original continua preservada como recuperação.

A revisão final da documentação corrigiu duas imprecisões no README: iniciar update durante gravação/processamento exige concluir a operação e tentar novamente, sem fila automática; as janelas são fechadas antes do bootstrap do helper, enquanto o app/tray só encerra depois do READY. Nenhuma alteração de produção foi necessária nessa última rodada.
