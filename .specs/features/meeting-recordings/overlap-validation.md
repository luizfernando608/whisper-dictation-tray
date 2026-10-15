# Processamento por fontes e teste de sobreposição — 15/10/2026

## Mudança

Capturas com microfone e computador verificadas como alinhadas passam a ser processadas separadamente. Um cancelador WebRTC usa a reprodução como referência para reduzir o eco do microfone. O derivado compensa 128 amostras de latência (8 ms), mantém a duração original e preserva o microfone original quando a referência está silenciosa após a cauda de eco. Os arquivos capturados não são alterados.

Silero VAD, já incluído no faster-whisper, remove silêncio do bloco de microfone antes do ASR. O mapa de amostras removidas permite devolver os timestamps à linha do tempo original. A resposta bruta, o mapa e a duração compactada ficam nos checkpoints; retomada não precisa refazer chamadas concluídas. O silêncio não gera chamada ASR.

Cada fonte mantém seu diarizador completo e pode conter várias pessoas. IDs locais são convertidos para IDs globais, conservando a origem. Palavras de uma fonte são associadas somente às vozes daquela fonte; sobreposição entre fontes não cria ambiguidade artificial. A transcrição combinada é ordenada por palavra. Importações mono e capturas não verificadas mantêm o caminho anterior. Contagem guiada global mantém processamento misturado com aviso, pois esse total não pode ser imposto a cada canal.

Dependência nova: `aec-audio-processing==1.0.1`, wrapper terceiro de WebRTC, BSD-3-Clause, wheel Windows x64 Python 3.12 de aproximadamente 921 KB. AEC ligado; AGC, redução de ruído e VAD do wrapper desligados. Não são usados os getters VAD com problema no wrapper nem o modo inteiramente desativado que devolveu zeros no experimento. Falha de carregamento/processamento retorna ao áudio misturado preservado com aviso. A DLL é incluída pelo build.

## Experimentos

### Gravação original, 155,125 s

- Computador aproximadamente 14,91 dB acima do microfone; sem clipping e sem grandes descontinuidades de captura. Vazamento do playback no microfone apresenta atraso correlacionado de aproximadamente 44 ms.
- ASR bruto por fontes produziria 463 palavras de microfone e 428 de computador: o vídeo é reconhecido duas vezes. Portanto, processamento separado ingênuo foi rejeitado.
- FIR próprio conservador não trouxe ganho suficiente e ficou apenas no experimento.
- WebRTC sem compactação de silêncio reduziu a reprodução, mas o ASR produziu frases em regiões de pouco sinal. Essa saída intermediária também foi rejeitada.
- WebRTC + VAD + ASR por fontes: 47 palavras locais e 428 do computador, quatro identidades com texto, 34 segmentos, duração preservada. A redução de nível do microfone foi 9,13 dB; isso mede supressão de sinal, não precisão de diarização.
- A execução de produção foi repetida em biblioteca isolada e concluiu `ready`; integridade dos arquivos originais verificada por SHA-256.

### Sobreposição controlada, 18 s

Foram sobrepostos o microfone original de 0–18 s e o computador original de 63–81 s, ambos no instante zero, com os ganhos originais. O computador está digitalmente silencioso durante o trecho original do microfone, evitando introduzir playback já duplicado. As referências de texto foram obtidas transcrevendo as faixas limpas separadamente, pelo mesmo ASR; não são anotações humanas.

| Medida | Mistura anterior | Processamento por fontes |
| --- | ---: | ---: |
| Texto do computador versus referência de 59 palavras | Exatamente igual | Exatamente igual |
| Texto local versus referência de 38 palavras | Mistura reconheceu apenas o texto do computador | Exatamente igual às 38 palavras locais |
| Trigramas locais preservados | 1/36 | 36/36 |
| Trigramas do computador preservados | 57/57 | 57/57 |
| Duração | 18,000 s | 18,000 s |

O resultado novo preserva 13,196 s de sobreposição entre intervalos de segmentos das fontes, distribuídos em três regiões. O microfone manteve uma identidade; o computador teve duas identidades e seis segmentos sem atribuição. A recuperação das fontes melhorou neste teste, enquanto a identificação de falas simultâneas dentro da própria saída do computador continua limitada.

### Comparação independente com modelo maior

MOSS-Transcribe-Diarize 0.9B foi executado localmente na mistura original completa, CPU float32, SDPA, oito threads, em ambiente isolado. Checkpoint `704aa4a9c304e8520be88901e0d1960158ef5b15`; código `e3bdd93f8ad58418e04659818efd5fd87a0bc486`. Aproximadamente 909 milhões de elementos armazenados, versus aproximadamente 100 milhões no Nemotron.

O ensaio terminou por EOS: 253,98 s totais, 1.348 tokens gerados de um teto de 4.096, 38 segmentos, 467 palavras, quatro IDs, último fim em 155,04 s e nenhum intervalo inválido. Não marcou sobreposição explícita; um ID aparece por apenas 0,66 s. O tamanho maior não demonstrou solução para sobreposição. Sua saída não foi tratada como verdade humana nem usada para alegar DER/precisão do PACE. Esse tempo inclui ASR e diarização e não deve ser comparado ao tempo do Nemotron isoladamente.

Nenhum áudio foi enviado a um novo serviço externo. O comparador maior rodou localmente; os testes ASR usaram o Groq já configurado. Conteúdo privado, faixas e resultados permanecem fora do Git.

## Verificação e revisão

- Testes de geometria, duração/tail, cancelamento, cache/derivado corrompido, fallback, preservação de silêncio, compactação e remapeamento de palavras/blocos coarse.
- Testes de fontes simultâneas com múltiplas pessoas por canal, IDs sem colisão, cronologia, checkpoint/replay sem nova chamada, importação mono, contagem guiada, falha parcial e cancelamento.
- Revisão independente reproduziu 12 tamanhos de bloco de 1 a 4.001 amostras com referência não nula, confirmando compensação e flush exatos.
- Dois achados corrigidos: nomes de pessoas não podem ser reutilizados sobre IDs que mudaram; nomes/resultados anteriores são arquivados antes de limpar o mapa ativo. Metadados capture/prepared que não sejam objetos JSON recebem fallback/reconstrução.
- Re-revisão independente: 60 testes de reunião passaram, sem achados P2/P3 abertos. Compilação Python e validação do binário são gates finais.

Reprodução: `scripts/benchmark_meeting_sources.py RECORDING OUTPUT CONFIG [--executable EXE]` cria uma biblioteca isolada e verifica a integridade da entrada. Usa o provedor ASR configurado, inclusive suas chamadas de rede. Os modelos desta prévia devem ficar em pasta permanente de dados, não em scratch do projeto.

## Limites

Não há referência humana suficiente para declarar DER ou WER neste áudio. A melhoria verificada é recuperar as duas fontes na sobreposição controlada, com preservação de timestamps e identidade por origem. Duas pessoas falando simultaneamente dentro da mesma faixa continuam dependendo das limitações de ASR/diarização; separação de vozes ou ASR condicionado por falante exigem outra avaliação. AEC pode atenuar a voz local e precisa de ensaios em outros dispositivos antes de publicação ampla.

Escopo de entrega: prévia local; sem publicação automática.

## Entrega local validada

- Fonte de produção: commit `5c613f1`.
- Suíte final: 157 testes, 156 aprovados e um smoke opcional preexistente não habilitado; compileall e diff check aprovados.
- PyInstaller concluído; os code objects de `meeting_processing` e `meeting_sources` dentro do executável correspondem exatamente às fontes compiladas.
- SHA-256 de `dist/Pace/Pace.exe` e da cópia portátil: `EAA93579D5A51F80EAD084123E900A38ADE576A21D3883CB2B6B00421E89A311`.
- DLL AEC empacotada: SHA-256 `4322A9E1DAC3C1557E4609CD17DD054269E3F672D8461D609AB129628C3431F1`.
- Ensaio congelado forçou reconstrução do derivado AEC e concluiu `ready`, verificando carregamento real da DLL, duração e integridade das entradas. Replay com checkpoints levou 9 s; não é medição de processamento frio.
- A gravação original foi reprocessada pelo worker congelado: `ready`, 34 segmentos e quatro identidades com texto; respostas ASR salvas reutilizadas. Os três WAVs originais permaneceram idênticos por SHA-256.
- Executável, configuração, resultado e transcrição anteriores preservados em backup sob `PACE-Preview-Data/backups/overlap-20261015`.
- Prévia portátil atualizada em `PACE-Preview-1.5.0`; modelos passaram à pasta permanente `PACE-Preview-Data/models`. IPC confirmou versão 1.5.0 e diretório da prévia; biblioteca reaberta.
- Artefatos privados recentes e ensaio controlado persistidos em `PACE-Preview-Data/benchmarks/overlap-20261015`.

O instalador local pode ser regenerado pelo build, mas nenhuma versão foi publicada nem instalada sobre outra distribuição do usuário.
