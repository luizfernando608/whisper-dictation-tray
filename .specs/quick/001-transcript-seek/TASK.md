# Clique na transcrição para navegar no áudio

Data: 2026-10-15

Escopo: clicar no texto, nome ou horário de um segmento posiciona o player no início desse segmento, mantendo reprodução ou pausa. O trecho correspondente à posição atual recebe destaque discreto, incluindo segmentos simultâneos.

Implementação: `src/whisper_dictation/recordings_window.py`. Verificação: `tests/test_recordings_ui.py` com widgets Tk reais e áudio sintético.

- Clique usa o início exato do segmento, inclusive sem identificação de falante e sem novo cabeçalho.
- Arrastar para selecionar texto e clicar em área vazia preservam a posição.
- Seleção de texto permanece visível acima do destaque de reprodução e de busca.
- Captura ativa ou finalização bloqueiam navegação pela transcrição.
- Trocar gravação ou limpar a biblioteca remove os vínculos anteriores.
- Intervalos de silêncio removem o destaque; sobreposição destaca ambos os segmentos.

Revisão independente encontrou prioridade visual incorreta entre destaque e seleção; corrigida elevando a seleção. Nenhum outro problema acionável identificado.

Verificado: 20 testes da interface e 6 testes de playback/configuração passaram; compilação e `git diff --check` passaram. Inspeção visual com biblioteca sintética confirmou destaque discreto e posição imediata no player.
