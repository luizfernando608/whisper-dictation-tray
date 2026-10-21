# Processamento de reuniões

`meeting_processing.py:process_recording()` seleciona processamento por fontes apenas para capturas de reunião verificadas e sem contagem global guiada. `meeting_sources.py:prepare_sources()` valida geometria, cria derivado AEC do microfone e mantém computador original. `pack_speech()`/`restore_speech()` removem silêncio e restauram tempos por mapa de amostras. AEC já roda dentro do worker de processamento isolado da UI.

`meeting_processing.py:_process_sources()` mantém ASR/checkpoints/diarização por fonte e publica IDs globais. Nunca associar palavras contra turnos das duas fontes juntos: sobreposição verdadeira vira falsa ambiguidade. `_write_result()` preserva nomes só quando turnos e namespace continuam iguais; caso contrário arquiva os nomes junto ao resultado anterior.

Validação e limitações: `.specs/features/meeting-recordings/overlap-validation.md`. As duas fontes não equivalem a duas pessoas. Modelos maiores não substituem referência humana; melhorar captura por fontes não resolve duas vozes dentro da mesma faixa. Cache de modelos de prévia deve ficar em dados permanentes do usuário.

`recordings_window.py:refresh_detail()` vincula cada segmento renderizado aos seus tempos com tags Tk. `transcript_release()` navega apenas em cliques sem arrasto/seleção e preserva reprodução ou pausa. `update_playback()` destaca todos os segmentos ativos, incluindo sobreposição; a tag `sel` deve ter prioridade acima de `playing` e `found`. Sem rolagem automática para preservar leitura e busca.


Em 21/10/2026, incidente remoto com texto sem falantes e status Falha revelou lacunas de primeiro uso: Nemotron/ASR exigiam preparo anterior e CPU era bloqueada pela ausência de Vulkan. Correção 1.5.2 em d83465f: preparo/reparo automático no worker, CPU obrigatório/Vulkan opcional, lock OS entre processos, downloads SHA/tamanho/retry e stages visíveis. `prepare_asr_models` usa mesmo lock; priorizar checkpoints antes de preparar. `diarize` mantém erro acionável quando inferência completa vazia contradiz probes com fala; pipeline_revision 2 invalida esse checkpoint antigo. Não inventar IDs.

Gate novo `scripts/validate_fresh_speaker_setup.py` exige cache de modelos e HF_HOME novos, não copia modelos pessoais, executa worker normal com AMI público e uma segunda gravação sem checkpoints em ambiente HF/proxy offline, solicitando auto com runtime só CPU. Fonte e frozen passaram; fontes/limites/evidências em `.specs/features/automatic-speaker-setup/validation.md`. CPU oficial NVIDIA v0.2.0 exige AVX2/x86-64-v3; SDK faster-whisper síncrono cancela via supervisor existente após timeout, sem callback de transferência. Nunca concluir causa exata do PC remoto sem erro/log.
