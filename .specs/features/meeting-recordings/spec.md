# PACE — Reuniões, importação de mídia e atualização

Data: 2026-10-14. Status: implementação autorizada pelo usuário em 2026-10-14.

## Objetivo

Gravar reuniões com microfone e saída de áudio por um novo atalho, guardar o áudio,
transcrever e separar falantes. Permitir processar arquivos de áudio/vídeo externos
na mesma biblioteca. Corrigir o comportamento de gravação sem limite nas instalações
atualizadas e oferecer atualização com a ação “Reiniciar para atualizar”.

## Experiência proposta

- R01: Atalho de reunião configurável, distinto do ditado; sugestão Ctrl+Shift+R.
  Acionamento alterna iniciar/encerrar. Configuração valida conflitos.
- R02: Capturar microfone e saída selecionados em uma linha do tempo comum,
  preservando as fontes separadas internamente e oferecendo áudio combinado.
  A saída inclui os sons dos aplicativos reproduzidos naquele dispositivo.
- R03: Gravação longa sem prazo artificial por padrão no modo reunião. Salvar
  continuamente em disco com memória limitada; recuperar partes já persistidas
  após interrupção. Tratar falta de espaço, suspensão e desconexão de dispositivos.
- R04: Pausar/retomar pelo menu do ícone e pela cápsula opcional. Atalho de pausa
  configurável opcional. Pausa suspende as duas fontes; duração exibida é a gravada.
  Registrar pausas para manter timestamps, áudio e transcrição coerentes.
- R05: Modo discreto padrão usa apenas o ícone da bandeja e seu menu. Cápsula opcional
  exibe estado, duração, níveis separados e ações pausar/retomar/encerrar; pode ser
  ocultada. Não abre janela nem toma foco ao iniciar. Não garantir exclusão de
  interfaces de toda ferramenta de compartilhamento de tela.
- R06: Ao encerrar, finalizar o áudio antes de enfileirar transcrição e diarização.
  Manter áudio e resultados parciais caso processamento falhe. Não colar o texto de
  reuniões automaticamente no aplicativo em foco.
- R07: Janela “Gravações” compartilhada por reuniões e importações, com título,
  origem, data, duração, status, busca, player e transcrição completa por falante.
  Permitir renomear, excluir, copiar, exportar áudio e exportar TXT/SRT/JSON.
- R08: “Importar áudio/vídeo” por seletor e arrastar/soltar. Formatos iniciais:
  WAV, MP3, M4A, OGG, FLAC, MP4 e MKV, sujeitos aos codecs do pacote distribuído.
  Extrair somente áudio dos vídeos; não alterar o original. Incluir decoder no
  aplicativo para não depender de instalação manual de FFmpeg.
- R09: Fila persistente com progresso por etapa, cancelar e tentar novamente.
  Reabrir o app permite recuperar tarefas. Diarização local; transcrição segue
  provedor/idioma configurados. Preparar modelos antes do processamento, com
  download visível e cache reutilizável. Depois do preparo, caminho local offline.

## Diarização já nesta versão

- R10: Pipeline completo: detectar/segmentar fala, extrair representações de voz,
  agrupar falantes globalmente e reconciliar com timestamps da transcrição.
  IDs devem permanecer consistentes na gravação inteira, inclusive entre blocos.
  Preservar sobreposição de vozes; trecho ambíguo pode ficar sem atribuição.
- R11: Exibir “Falante 1”, “Falante 2” etc., com nomes editáveis por gravação.
  Quantidade automática e opção de informar quantidade/faixa de participantes.
  Não inferir nome real pela voz nem assumir que uma fonte contém uma só pessoa.
- R12: Transcrição precisa produzir timestamps. Se provedor/modelo retornar apenas
  texto, executar alinhamento compatível ou oferecer alternativa com timestamps;
  não fabricar marcações. Preservar tempo absoluto ao processar blocos.

### Regras de hardware

- R13: Base de compatibilidade: Windows x64 com execução em CPU. Detectar runtimes,
  GPUs, drivers e memória, e validar inferência real do modelo em cada candidato.
  GPU detectada sozinha não basta para declarar suporte.
- R14: Candidatos: CPU ONNX; Intel CPU/GPU via OpenVINO; NVIDIA via CUDA quando
  compatível; GPUs Intel/AMD/NVIDIA via DirectML quando compatíveis. A lista é uma
  meta de validação, não suporte comprovado a todos os modelos/adaptadores.
- R15: Modo Automático compara candidatos compatíveis em trecho curto representativo,
  considerando tempo, memória e consistência com resultado de referência. Escolher
  configuração mais eficiente que passe os critérios de qualidade, mesmo se CPU
  superar GPU. Cache por modelo, runtime e hardware/driver; invalidar ao mudar.
- R16: Driver incompatível, memória insuficiente, erro de inferência ou timeout:
  reduzir tamanho de lote quando apropriado; depois tentar outro backend/CPU.
  Retomar de checkpoint sem perder áudio ou duplicar trechos. Processar em worker
  separado, com limites de recursos, para manter interface e gravação responsivas.
- R17: Configurações mostram backend efetivamente usado e motivo de fallback;
  permitir Automático/CPU e opções aceleradas validadas. Manter runtimes opcionais
  isolados para evitar conflito de pacotes ONNX que compartilham o mesmo namespace.
- R18: Antes de escolher/distribuir modelos, validar licença, acesso, exportação
  ONNX/OpenVINO e equivalência com pipeline completo. Não usar segmentação isolada
  como diarização completa. Modelos com aceite/token exigem fluxo explícito de
  preparação; download por espelho não elimina condições do modelo original.

## Gravação sem limite e atualização

- R19: Reproduzir upgrade com configuração antiga finita e com zero (sem limite).
  Preservar valores explicitamente escolhidos; opção “Sem limite (manual pelo atalho)”
  deve aparecer e persistir em ambos os casos. Auditar versão, processo, atalho e
  caminho de instalação para detectar instância antiga. Não atribuir causa sem
  reproduzir a instalação afetada.
- R20: Verificar/baixar atualização em segundo plano, validar arquivo e versão,
  e só então mostrar “Reiniciar para atualizar”. Manter “Verificar atualizações”.
  Falha de rede deve aparecer como falha, não como confirmação de versão atual.
- R21: Ao reiniciar, persistir tarefas e encerrar com segurança; helper aplica
  atualização silenciosa na instalação correta e relança o PACE. Adiar enquanto
  houver gravação ativa. Separar dados/modelos dos binários substituídos. Confirmar
  versão executada e permitir recuperação/rollback se aplicação falhar.
- R22: Release deve ligar código, versão, build e checksum do pacote. Validar
  upgrade a partir de instalação anterior, preservando configuração, biblioteca,
  modelos e credenciais. Não substituir o executável enquanto estiver em uso.

## Revisão independente e ciclo de correção

- R23: Ao concluir cada etapa, um agente revisor diferente de quem implementou
  examina requisitos, código, testes e integração com o restante do PACE. Recebe
  a especificação e evidências de verificação; não depende apenas do resumo do
  implementador. O responsável pela implementação corrige os achados, e o revisor
  verifica as correções e possíveis regressões antes de encerrar a etapa.
- R24: Depois da integração completa, executar revisão independente final de toda
  a entrega, incluindo uso real e inspeção visual do app empacotado. Cobrir fluxos
  de gravação/pausa/retomada, fontes de áudio, importação, identificação de falantes,
  player, busca, exportação, fila, recuperação, seleção de hardware, fallback,
  instalação/atualização e regressões do ditado e das configurações existentes.
  Conferir janelas, foco, atalhos, escalas de tela, estados vazios/erro, feedback,
  travamentos, concorrência, integridade dos arquivos e consumo de recursos.
- R25: Registrar cada achado com severidade, evidência, passos para reprodução,
  resultado esperado, correção e resultado da revalidação. Repetir o ciclo
  implementação → revisão → correção → nova revisão até resolver os achados.
  Pendência só pode ser adiada com concordância explícita do usuário; nenhuma
  falha crítica ou importante conhecida pode permanecer para publicação.
- R26: A revisão final deve avaliar o mesmo build candidato à distribuição,
  incluindo upgrade de versão anterior. Mudança posterior invalida as verificações
  afetadas e exige nova revisão/regressão. Entregar relatório de requisitos
  atendidos, cenários testados, achados resolvidos e limitações não verificadas.
  Hardware indisponível e verificações não executadas devem ser explicitados;
  aprovação é restrita ao que foi verificado, sem promessa de ausência total de bugs.

## Achados que orientam o plano

### Decisão de modelo durante implementação (2026-10-14)

O usuário confirmou: “Eu quero um modelo local”. O padrão será Nemotron 3, sem
conta/token, com preparo inicial explícito e execução local em CPU/GPU Vulkan.
Seu modelo end-to-end mantém um estado único de identidades na gravação, substituindo
a exigência de embeddings/agrupamento externos de R10. Capacidade: até 8 falantes
automáticos; contagem exata/faixa R11 e backends OpenVINO/DirectML/CUDA R14 ficam
disponíveis na opção avançada Community-1, que exige aceite/token no preparo.
As duas opções são locais. A transcrição mantém o provedor escolhido pelo usuário;
o modo Local também evita enviar o áudio para transcrição. Implementação nativa
usa CAPI por blocos para manter memória limitada durante gravações longas.

- `src/whisper_dictation/audio.py:AudioRecorder` guarda buffers de microfone em RAM;
  não é a base adequada, sem adaptação, para gravação persistente de várias horas.
- `src/whisper_dictation/transcription.py:WhisperTranscriber` já divide áudio longo,
  mas retorna texto simples e descarta timestamps. Exige saída estruturada.
- `src/whisper_dictation/settings.py:AppConfig` usa padrão de 1800 segundos; `_sanitize`
  aceita zero e preserva valores antigos. `app.py:_schedule_auto_stop` não agenda
  parada quando zero. `settings_window.py:RECORD_LIMIT_OPTIONS` contém “Sem limite”.
- API pública consultada em 2026-10-14: latest v1.4.0, asset publicado em 2026-09-27.
  Auditoria comparou checksum do instalador local e asset publicado: iguais. Isso
  não confirma qual executável ou configuração rodava na instalação relatada.
- `updater.py:launch_installer` abre instalação visível por padrão. `installer.iss`
  usa `skipifsilent` no relançamento: apenas ativar instalação silenciosa não basta.
- Projeto reutilizável: `C:/Users/ziull/Documents/local-transcribe`. Extração,
  transcrição com timestamps, associação de turnos e exportação são referências.
  `src/ltranscribe/diarize.py:diarize_pyannote` força CPU. A rota ONNX não seleciona
  provider; runtime instalado expõe CPU/Azure, sem DirectML/OpenVINO/CUDA.
  A biblioteca instalada alinha canais entre janelas, sem agrupamento
  global por voz. Não comprova aceleração Intel nem identidade global robusta.

## Ordem proposta e critérios de conclusão

Cada etapa abaixo inclui verificação pelo implementador e o ciclo de revisão
independente definido em R23–R26. Revisão intermediária não substitui a final.

1. Corrigir/auditar gravação sem limite e cadeia de release. Verificar upgrade real,
   opção visível, persistência de zero e ausência de auto-stop configurado.
2. Validar pipeline completo de diarização e matriz de hardware. Usar áudio anotado
   de 2–5 participantes, pausas longas, retorno do mesmo falante e sobreposição;
   medir erro de diarização, tempo e memória. Definir critérios antes de habilitar
   aceleração; não aceitar backend rápido que prejudique identidade dos falantes.
3. Capturar reunião em disco com pausa e recuperação. Ensaio de 2 horas com ambas
   as fontes, sincronização, memória limitada e interrupção controlada.
4. Integrar importação, fila, timestamps, diarização e biblioteca. Verificar mídia
   real nos formatos distribuídos, arquivo inválido, retomada e exportações.
5. Integrar controles discretos e “Reiniciar para atualizar”. Verificar conflito de
   atalhos, nenhum foco roubado, gravação bloqueando reinício e atualização silenciosa
   com relançamento e dados preservados.
6. Revisão independente completa do app integrado e empacotado. Executar cenários
   de ponta a ponta, inspecionar telas e validar regressões. Corrigir os achados e
   repetir a revisão das correções e áreas afetadas. Só então considerar a entrega
   pronta, com relatório final e eventuais limitações explicitadas.

## Fontes técnicas

- https://learn.microsoft.com/en-us/windows/win32/coreaudio/loopback-recording
- https://onnxruntime.ai/docs/execution-providers/
- https://onnxruntime.ai/docs/reference/execution-providers/OpenVINO-ExecutionProvider.html
- https://onnxruntime.ai/docs/execution-providers/DirectML-ExecutionProvider.html
- https://docs.openvino.ai/2026/openvino-workflow/running-inference/inference-devices-and-modes/auto-device-selection.html
- https://huggingface.co/pyannote/segmentation-3.0
- https://huggingface.co/pyannote/speaker-diarization-community-1
- https://github.com/luizfernando608/whisper-dictation-tray/releases/tag/v1.4.0

## Fora do escopo inicial

Resumo/ata automática, sincronização em nuvem, reconhecimento de pessoas entre
gravações, captura de vídeo e garantia de suporte a todo hardware/driver existente.
