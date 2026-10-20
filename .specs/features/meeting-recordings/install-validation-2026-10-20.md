# Instalação e atualização reais — 20/10/2026

## Escopo

Validação autorizada pelo usuário neste computador Windows x64. Uso pessoal; limitações de diarização aceitas. Sem publicação externa. Windows Sandbox não está instalado: os ensaios usam instalações e bibliotecas separadas no Windows do usuário, com snapshot/restauração dos atalhos e da chave HKCU do instalador.

Os scripts manuais em `scripts/validate_windows_installation.py`, `validate_installed_pipeline.py` e `validate_installed_capture.py` são opt-in e não fazem parte da suíte automática. O primeiro executa instaladores reais e fecha/reabre a prévia; o terceiro usa microfone e reprodução reais. Requerem o ambiente de desenvolvimento e `psutil==7.2.2` para supervisionar somente os processos de teste.

## Falhas encontradas e corrigidas

1. O pacote continha fixtures ONNX e cabeçalhos de compilação Torch. Uma instalação em caminho longo abortou com código 5 ao copiar fixture cujo nome completo excedia o limite de caminho do Windows. `installer.iss` agora exclui precisamente esses dois diretórios, não usados para inferência. Repetição da instalação abriu biblioteca e configurações; processamento local e os quatro runtimes empacotados passaram sem esses arquivos.
2. Ao provocar falha de confirmação após uma instalação real bem-sucedida, o helper antigo restaurou o EXE 1.4.0 e seus 2.430 arquivos de runtime, mas deixou o registro como 1.5.0. Os atalhos novos também são incompatíveis com o CLI antigo. `update_integration.py` agora salva/restaura registro, atalhos conhecidos e desinstaladores junto aos binários. Revisão independente identificou e corrigiu ainda a retomada do aplicativo caso a restauração de algum recurso de integração falhe: os erros ficam registrados e os backups são preservados.

As falhas e recuperações foram exercitadas com dados de teste. Os 20 WAVs existentes na biblioteca do usuário foram conferidos por SHA-256 e permaneceram idênticos. A configuração e `.env` da prévia também foram preservadas.

## Evidências funcionais anteriores ao pacote final

- Instalação limpa real: código 0, biblioteca e configurações visíveis, limite novo de ditado 0.
- Upgrade real 1.4.0 → 1.5.0 com helper congelado separado: `confirmed.json`, `result.json=updated`, preservação byte a byte de configuração, `.env`, canário de modelos e arquivos da biblioteca; nomes de falantes preservados no SQLite. O limite explícito anterior de 1.800 segundos foi preservado, conforme política de configuração.
- A base 1.4.0 foi reconstruída para o ensaio com o script da tag `v1.4.0` e os binários reais da instalação antiga deste computador. Não inclui configuração nem credenciais do usuário. Sua compressão foi desativada para o ensaio; não é o arquivo byte a byte do release público.
- Worker instalado offline: importação WAV/vídeo de 18 s de AMI, igualdade das amostras do vídeo lossless, erro de mídia inválida, retentativa e cancelamento passaram. Transcrição tiny local e Nemotron CPU reais. Exports e seek verificam APIs das mesmas fontes arquivadas no executável; não são automação visual de todos os botões.
- Groq real: áudio público AMI de 18 s, `whisper-large-v3`, worker instalado em `ready`, 3 segmentos/8 turnos. A chave existente foi usada apenas no ambiente do subprocesso e não foi gravada nos relatórios.
- Runtimes no pacote instalado: ONNX CPU, OpenVINO CPU, OpenVINO Intel GPU e DirectML passaram no modelo sintético de compatibilidade; maior diferença absoluta 4,77e-7. Não mede qualidade de diarização.
- Microfone físico pelo AudioRecorder: 1,983 s, 31.736 amostras a 16 kHz, sinal não nulo.
- Captura pelo aplicativo instalado: 128,469 s ativos, continuou após 120 s, pausa de 4 s com deriva 0; microfone, computador e mistura com 2.055.504 amostras alinhadas. Processamento local por fontes em `ready`, timestamps, exportações e integridade dos três WAVs passaram.
- Suíte final das fontes com `PACE_RUN_MODEL_SMOKE=1`: 179 testes, 178 aprovados e um skip por falta de privilégio para criar symlink real. Casos simulados de reparse/junction passaram.
- Revisão independente da correção de integração e re-revisão após correção P2: sem achados restantes no escopo.

## Pacote final

Fonte de produção `d97ce1d`; as 32 unidades Python do aplicativo e `main.py` no executável instalado correspondem exatamente às fontes compiladas. PyInstaller e Inno Setup concluídos com sucesso. Pacote final: `Output/Pace_Installer.exe`, versão 1.5.0, 327.443.171 bytes.

- SHA-256 do instalador: `c1e373aeb939893d6459262e9685c708260c4a2f794e65e9a71b86ec1c7bad89`.
- SHA-256 do executável: `299aad8a06801f72fcc9bca31a07216f057e7d366b20622b393ada709f66e9ab`.

Todos os ensaios seguintes usaram esse pacote final e foram aprovados. Caminhos relativos à pasta `C:/Users/ziull/Documents`:

| Ensaio | Evidência | Resultado |
| --- | --- | --- |
| Instalação limpa e upgrade 1.4 → 1.5 | `pace-final-install-validation-20261020/report-install.json` | `passed=true`, UI/configurações abertas, receipt 1.5.0, dados preservados, restauração do ambiente sem erros |
| Falha de confirmação e rollback | `pace-final-rollback-validation-20261020/report-rollback.json` | Falha esperada de confirmação; EXE e 2.430 arquivos antigos idênticos, registro 1.4.0, atalhos e desinstaladores idênticos; `rollback_error=null`, ambiente restaurado |
| Pipeline instalado offline | `pace-final-pipeline-validation-20261020/run-20261020T163407Z-c5118c4a/report.json` | Seis verificações: WAV, vídeo, equivalência lossless, mídia inválida, retentativa e cancelamento; TXT/SRT/JSON e seek aprovados |
| Groq com áudio público | `pace-final-groq-validation-20261020/report.json` | `whisper-large-v3`, 3 segmentos/8 turnos, áudio intacto, nenhuma credencial no relatório |
| Captura física e processamento | `pace-final-hardware-validation-20261020/capture-20261020T163619Z-3c0c18bd/report.json` | 128,734 s ativos, continuou após 120 s, pausa de 4 s com deriva zero, microfone/computador/mistura com 2.059.744 amostras cada; processamento por fontes em `ready`, 22 segmentos/16 turnos, exports/seek e WAVs intactos |
| Prévia e atalho Windows | `pace-final-preview-validation-20261020.json` | Prévia 1.5.0 atualizada, aberta pelo `.lnk` real do menu Iniciar; biblioteca visível, configuração/`.env` e 20 áudios intactos |

O teste sintético no executável instalado repetiu ONNX CPU, OpenVINO CPU, OpenVINO Intel GPU e DirectML com aprovação; maior diferença absoluta 4,77e-7 (`temp/final-installed-runtimes.log`). A suíte final permanece em 178 aprovados/um skip, sem mudanças posteriores nas fontes de produção. Não há achados de revisão pendentes no escopo.

A prévia em `%LOCALAPPDATA%/PACE-Preview-1.5.0` usa o mesmo EXE validado. O EXE/runtime anteriores foram guardados em `%LOCALAPPDATA%/PACE-Preview-Data/backups/validated-install-20261020-final`. A instalação principal antiga e seus dados não foram migrados. Os testes usam IPC para controlar gravação; a biblioteca abriu pelo atalho real, mas não houve automação da caixa de pesquisa do Windows nem pressão física das teclas globais. Compatibilidade de backend não mede qualidade de diarização, e os testes deste computador não equivalem a ensaios em outros hardwares.

Resultado: candidato aprovado para a distribuição pessoal pretendida, sem falhas bloqueantes restantes nos ensaios executados. Publicação não realizada: outros computadores só verão a atualização automática após um release.

Relatórios privados (sem transcrição nos resumos): diretórios `PACE-Install-Validation-20261020` em dados locais e `pace-*-validation-20261020`/`pace-rollback-before-fix` em Documents. Logs de compilação/testes em `temp/`, ignorados pelo Git. Áudio e credenciais não são incluídos no repositório nem no instalador.
