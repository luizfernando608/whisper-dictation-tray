# Validação do candidato 1.5.1

## Escopo

1.5.0 publicada e verificada online antes deste trabalho. O candidato novo corrige adaptadores de ditado Gemini/OpenAI, atualiza sugestões de modelos e centraliza biblioteca/configurações; o clique principal da bandeja abre a UI. Não modifica o arquivo da 1.5.0 publicado. O usuário escolheu explicitamente testes sem chaves Gemini/OpenAI: contratos com SDK real/HTTP em memória, sem chamadas reais nesses provedores.

## Implementação e revisão

Pesquisa em fontes oficiais e assinaturas locais registrada em `research.md`. T2: 27 casos de contratos, formatos, idiomas, respostas inválidas, payloads/encoder, upload/delete e recursos; regressão Groq inclui reutilização do cliente em chamadas consecutivas. T3: 11 testes de frame embutido, credenciais transacionais, preparo, cleanup e layout em 100/125/150%. T4: 9 testes de roteamento/Tk, abertura concorrente, seleção/player/rascunho e ação padrão sem captura. Caso combinado usa formulário real para capturar/salvar Ctrl+F.

A revisão independente encontrou dois P2, ambos corrigidos: campo Gemini `errors` e conflito do binding específico Ctrl+F do root com a captura de atalhos. Revisor reexecutou seus casos e as suítes focadas; sem achados pendentes. A política de reuniões com provedor de timestamps permanece separada dos adaptadores de ditado.

Suíte final: 226 executados, 225 aprovados e um skip por privilégio de symlink; `PACE_RUN_MODEL_SMOKE=1`, `PYTHONPATH=src`. Os quatro smokes ONNX/OpenVINO CPU/OpenVINO Intel GPU/DirectML passaram, tanto na suíte quanto no executável empacotado (`scripts/smoke_frozen_runtime.py`). Erro máximo observado: 4,77e-7. Evidências locais: `temp/1.5.1-final-tests.log` e `temp/1.5.1-frozen-runtime.log`.

Um teste de escala expôs geometria Tk ainda pendente quando timers são agrupados sob carga. O fixture passou a executar `update_idletasks()` antes das mesmas asserções; atraso controlado reproduziu e confirmou a causa, sem alteração de produção. A correção e a parametrização do harness de instalação foram revisadas independentemente. Uma tentativa da suíte iniciada sem `PYTHONPATH` foi descartada e repetida com a configuração correta.

Verificação visual das duas páginas na mesma janela, com dados sintéticos e sem chaves, em `temp/unified-ui-review-final/recordings.png` e `settings.png`. Capturas foram feitas pelo HWND da própria janela, sem incluir o desktop. Layout de configurações permanece dentro do painel nos tamanhos/escalas testados.

## Pacote e validação local

EXE 1.5.1 comparado às fontes: os 32 módulos e `main.py` são iguais aos objetos de código compilados das fontes revisadas. SHA256 do EXE: `3dd6a69114809d91a1737c68a16145640e0e12bb753fbd4fa327945d72eafc72`. Código de produção no commit `e9f76eb`; ajustes posteriores somente em testes/harness/documentação. Evidência: `Output/v1.5.1/source-verification.json`.

Instalador em `Output/v1.5.1/Pace_Installer.exe`, 327.408.839 bytes, SHA256 `6466c58cabf1c27f47781ee29e1ac89fb82b29b0dafdd991c7b8ce1b237d3fe0`. O instalador público 1.5.0 permanece com o hash original `c1e373aeb939893d6459262e9685c708260c4a2f794e65e9a71b86ec1c7bad89`.

Instalação limpa e atualização real 1.5.0 → 1.5.1 aprovadas. A navegação Gravações/Configurações manteve o mesmo HWND e apenas uma janela. O auxiliar aguardou o processo antigo, executou o instalador e recebeu confirmação da versão nova. Configuração, credenciais-canário, modelo e biblioteca de teste preservados; os 20 WAVs e a configuração/credenciais originais mantiveram os mesmos hashes. Integração do host restaurada sem erros. Evidência: `C:/Users/ziull/Documents/pace-151-install-validation-20261020/report-install.json`.

Pipeline offline instalado aprovado em seis verificações: áudio/vídeo, equivalência lossless, erro recuperável, nova tentativa e cancelamento; transcrição local, diarização Nemotron, exportação e seek exercitados. Evidência: `C:/Users/ziull/Documents/pace-151-pipeline-validation-20261020/run-20261020T234539Z-82b3c313/report.json`.

Cenário negativo aprovado: confirmação de versão deliberadamente inválida levou à reversão completa, sem erro de rollback. Os 11.187 arquivos do runtime, EXE antigo, registro 1.5.0, atalhos e desinstaladores foram comparados; dados de teste e os 20 WAVs/configuração/credenciais originais preservados. Host restaurado sem erros. Evidência: `C:/Users/ziull/Documents/pace-151-rollback-validation-20261020/report-rollback.json`.

Captura física instalada aprovada: 128,188 s com microfone + WASAPI loopback em faixas separadas e alinhadas, níveis e amostras não nulos, gravação continuando após 120 s, pausa de quatro segundos sem avanço de duração, retomada e processamento local completo. Áudios originais da captura mantiveram os hashes. A prévia anterior foi reaberta e configuração/fixture restaurados. Evidência: `C:/Users/ziull/Documents/pace-151-capture-validation-20261020/capture-20261021T000506Z-420c1890/report.json`.

O primeiro ensaio físico foi inconclusivo: o leitor de telemetria convertia qualquer erro de leitura em `{}`, interpretado como parada antes da limpeza. O validador passou a distinguir snapshots válidos de telemetria indisponível, registrar exceções e tentar novamente por até dois segundos apenas no segundo caso; parada válida/ID diferente continuam falhando imediatamente. Oito testes específicos passaram (incluindo lock real Windows), também executados pelo revisor independente. O ensaio completo seguinte observou um `PermissionError` transitório aos 125,515 s, recuperou o próximo snapshot ativo e concluiu todas as asserções. Nenhuma alteração de produção foi necessária; não reclassificar o primeiro relatório como aprovado.

Prévia local 1.5.1 ativada em 21/10/2026, em pasta separada. Os cinco atalhos que apontavam para a prévia anterior foram atualizados, preservando argumentos e mantendo backup. Abertura real pelo menu Iniciar e abertura repetida aprovadas, sem duplicar janela; navegação Configurações/Gravações preservou o mesmo HWND. As 20 gravações, configuração e credenciais mantiveram os hashes; registro de instalação permaneceu inalterado e a pasta 1.5.0 foi preservada. O caminho físico MSIX de AppData foi resolvido antes de criar os atalhos. Evidência: `C:/Users/ziull/Documents/pace-151-preview-validation-20261020/report.json`.

Resultado: candidato local 1.5.1 validado; T1–T6 concluídas. Sem chamadas reais Gemini/OpenAI, conforme escolha do usuário, e sem publicação da 1.5.1. A release pública permanece 1.5.0.
