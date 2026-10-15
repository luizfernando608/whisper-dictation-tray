# Processamento de reuniões

`meeting_processing.py:process_recording()` seleciona processamento por fontes apenas para capturas de reunião verificadas e sem contagem global guiada. `meeting_sources.py:prepare_sources()` valida geometria, cria derivado AEC do microfone e mantém computador original. `pack_speech()`/`restore_speech()` removem silêncio e restauram tempos por mapa de amostras. AEC já roda dentro do worker de processamento isolado da UI.

`meeting_processing.py:_process_sources()` mantém ASR/checkpoints/diarização por fonte e publica IDs globais. Nunca associar palavras contra turnos das duas fontes juntos: sobreposição verdadeira vira falsa ambiguidade. `_write_result()` preserva nomes só quando turnos e namespace continuam iguais; caso contrário arquiva os nomes junto ao resultado anterior.

Validação e limitações: `.specs/features/meeting-recordings/overlap-validation.md`. As duas fontes não equivalem a duas pessoas. Modelos maiores não substituem referência humana; melhorar captura por fontes não resolve duas vozes dentro da mesma faixa. Cache de modelos de prévia deve ficar em dados permanentes do usuário.
