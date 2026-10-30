# Diarização local para o PACE: pesquisa e diagnóstico

Data da consulta: **15 de outubro de 2026**. Pesquisa de fontes primárias: cartões oficiais, repositórios dos autores, artigos e relatórios dos responsáveis pelos benchmarks. Os números abaixo são publicados por essas fontes; não executei esses corpora neste computador nesta investigação.

## Conclusão para este computador

**Nemotron 3 Diarization continua sendo a escolha inicial mais defensável para o PACE neste hardware.** É uma recomendação de engenharia baseada no conjunto das evidências, não uma afirmação de superioridade em todo domínio. A melhoria prioritária para conversas simultâneas é avaliar a cadeia completa: captura, transcrição das vozes, identificação de falantes e associação das palavras.

DiariZen, SUPlime/SUPlime-L, Community-1 e sistemas conjuntos merecem comparação controlada. Nenhuma fonte encontrada estabelece um vencedor universal em reuniões em português neste Intel. Ainda falta uma referência humana representativa para medir essa situação.

## O que efetivamente rodou aqui

Consulta ao hardware: Intel Core Ultra 7 255H, 16 núcleos, aproximadamente 32 GB de RAM, Intel Arc 140T integrada. A memória compartilhada anunciada pelo Windows não equivale a 16 GB de VRAM dedicada. Não há GPU NVIDIA neste computador.

Consulta ao resultado técnico da reunião de 155,125 segundos:

| Item | Evidência local |
| --- | --- |
| Modelo | `nvidia/Nemotron-3-Diarization` |
| Artefato | GGUF Q8_0, 107.012.128 bytes |
| Runtime | NVIDIA NeMo-Speech.cpp 0.2.0, API C nativa |
| Dispositivo efetivo | **CPU**, `backend=nemotron-cpu`, `device_index=-1` |
| Tempo registrado de diarização | 4,563 segundos; aproximadamente 34 vezes a duração em tempo real |
| Pico do processo que hospeda o diarizador | 199.626.752 bytes, aproximadamente 190 MiB |
| Configuração | `v3-offline`, fluxo persistente, até oito identidades |

Esse tempo não inclui toda a transcrição nem representa consumo total do aplicativo. O número de identidades produzido pelo modelo não comprova o número real de participantes. Não há DER medido nesta reunião porque não existe anotação humana de referência.

Os metadados de `transcript.json` identificam a transcrição desta reunião como Groq `whisper-large-v3`. A identificação de falantes é local; a transcrição desta execução usou esse provedor remoto. São etapas independentes.

A seleção automática tinha uma decisão de CPU em cache. O código, nesse caso, não repete o teste Vulkan. O resultado desta reunião contém somente tempo de benchmark de CPU: **não demonstra que a GPU foi comparada e perdeu nesta execução**.

Em validação anterior neste mesmo computador, com um trecho público de 60 segundos do AMI, a API C levou 1,937 s em CPU e 1,219 s em Vulkan Intel. A divergência entre saídas foi 0,022%. Isso comprova uma rota Intel funcional naquele teste; não estabelece desempenho ou qualidade para qualquer gravação. O DER do trecho foi 23,731% sem tolerância e 13,574% com tolerância de 250 ms; a quarta pessoa, com participação curta, não foi separada. Um minuto não substitui um corpus.

Fontes locais: [implementação nativa](C:/Users/ziull/Documents/whisper-dictation-tray/src/whisper_dictation/nemotron_diarization.py), [processamento e associação](C:/Users/ziull/Documents/whisper-dictation-tray/src/whisper_dictation/meeting_processing.py), [validação anterior](C:/Users/ziull/Documents/whisper-dictation-tray/.specs/features/meeting-recordings/nemotron-validation.md).

## Como interpretar os números

DER mede tempo de fala perdido, inventado ou atribuído ao falante errado. Não mede diretamente quantas palavras corretas chegaram à transcrição de cada pessoa. Para isso, também precisamos de cpWER/tcpWER ou métrica equivalente de transcrição atribuída.

Um *collar* de 250 ms perdoa diferenças nas bordas das falas; zero não perdoa. Pontuações com tolerâncias distintas não formam um ranking único. Também mudam o resultado: incluir sobreposição, fornecer previamente o número de pessoas, usar detector de fala de referência, alterar microfones, recortes, anotações, pós-processamento e runtime. A [metodologia do VoiceArena](https://voicearena.com/diarization-bench/methodology) explicita essas escolhas.

As tabelas são blocos separados. Não calculei uma média entre benchmarks incompatíveis.

## Comparações externas recentes

### VoiceArena: conversas com sobreposição

139 conversas em inglês, 1.317,9 minutos, DER sem tolerância, sobreposição incluída, detecção de fala feita pelo sistema:

| Sistema | DER % |
| --- | ---: |
| Nemotron 3 | **14,72** |
| DiariZen Large s80 MD v2 | 19,34 |
| Precision-3, serviço hospedado | 20,56 |
| Precision-2, serviço hospedado | 23,41 |
| Sortformer streaming 4spk v2 | 24,61 |
| Community-1 | 30,61 |

Fonte: [leaderboard dos responsáveis pelo benchmark](https://voicearena.com/diarization-bench). É evidência externa aos fabricantes desses modelos, mas de um benchmark recente, com domínio e idioma próprios.

O recorte mais relacionado à reclamação do usuário compara Nemotron e DiariZen:

| Recorte | Nemotron | DiariZen |
| --- | ---: | ---: |
| Presencial, sobreposição acima de 30%, 39 sessões | **17,89** | 26,00 |
| Duas pessoas, 37 sessões | 10,31 | **7,29** |
| Cinco a oito pessoas, 17 sessões | **22,18** | 33,75 |

São DERs nos grupos de sessões, não DER calculado exclusivamente nos instantes sobrepostos. Fonte: [comparação por condição](https://voicearena.com/diarization-bench/compare). O resultado favorece Nemotron no agregado e sob muita sobreposição, mas mostra uma vantagem de DiariZen no subconjunto de duas pessoas.

### OpenBench: onze corpora e outro runtime

Resultados Argmax SpeakerKit 3.1.6 em M2 Ultra. A fonte publica frações; abaixo, convertidas para porcentagens:

| Corpus | Nemotron 3 | Community-1 | Sortformer* |
| --- | ---: | ---: | ---: |
| AISHELL-4 | 11 | 12 | 32 |
| AMI-IHM | 9 | 36 | 18 |
| AMI-SDM | 11 | 38 | 23 |
| AVA-AVD | 45 | 48 | 60 |
| AliMeeting | 18 | 23 | 21 |
| CALLHOME | 15 | 30 | 19 |
| DIHARD-III | 13 | 22 | 21 |
| EGO4D | 41 | 48 | 50 |
| Earnings-21 | 20 | **10** | 45 |
| MSDWild | 23 | 33 | 33 |
| VoxConverse | 8 | 11 | 22 |
| Média macro | **19** | 28 | 31 |

Fonte: [OpenBench, configurações e resultados](https://github.com/argmaxinc/OpenBench/blob/main/BENCHMARKS.md). AMI usa anotações por alinhamento forçado, incompatíveis com AMI clássico. São conversões Argmax; não nosso GGUF. A tabela DER nomeia Sortformer v2, enquanto a configuração identifica v2.1*. A [configuração padrão do avaliador](https://github.com/argmaxinc/OpenBench/blob/main/config/benchmark_config/metrics/der.yaml) confirma tolerância zero e sobreposição incluída. Community vence Earnings-21, contrariando uma superioridade universal de Nemotron. Tempos de Mac não predizem tempos neste Intel.

### Consultas clínicas: outro domínio

15 consultas simuladas, 2,4152 horas, número de falantes automático, DER com tolerância de 250 ms:

| Sistema | DER % |
| --- | ---: |
| Precision-3 API | **2,891** |
| Nemotron 3 | **4,803** |
| Community-1 | 6,620 |
| Sortformer v1 | 6,778 |
| Sortformer v2.1 | 7,974 |
| VibeVoice-ASR | 8,233 |

Fonte: [Omi Health, benchmark e metodologia](https://github.com/Omi-Health/clinical-diarization-benchmark). NVIDIA rodou em L4; VibeVoice inclui servidor de ASR conjunto. É uma amostra pequena, de consultas, não reuniões com vários participantes. As linhas com otimizações Omi são configurações distintas e não foram misturadas com os baselines acima.

### Converse: um contraexemplo importante

Relatório de 13/10/2026, quatro conversas americanas de duas pessoas, sem tolerância, sobreposição incluída:

| Sistema | DER % |
| --- | ---: |
| Community-1 | **27,98** |
| DiariZen v2 | 28,16 |
| Nemotron 3 | 28,21 |

A diferença de 0,23 ponto entre primeiro e terceiro é pequena demais para uma conclusão geral nessa amostra. As referências derivam de tempos de palavras e incluem eventos vocais; os autores documentam limitações. Fonte: [relatório técnico Ocular Converse](https://www.useocular.com/converse/diarization/report). Esse resultado deve ser preservado na análise, mesmo contrariando o favorito dos outros testes.

## Benchmarks dos autores e fronteira acadêmica

### NVIDIA: Nemotron 3 versus geração anterior

DER na configuração de contexto alto, segundo o cartão oficial:

| Corpus | Nemotron 3 | Sortformer v2.1 |
| --- | ---: | ---: |
| DIHARD-III completo | **12,73** | 19,09 |
| CALLHOME part2 | **9,10** | 10,32 |
| AliMeeting próximo | **6,40** | 11,57 |
| AliMeeting distante | **10,47** | 13,69 |
| AMI MHM | **9,25** | 15,81 |
| AMI SDM | **11,14** | 21,42 |
| NOTSOFAR-1 MHM | **6,77** | 21,77 |
| NOTSOFAR-1 SDM | **11,00** | 30,49 |

Sobreposição incluída; tolerância zero, exceto CALLHOME com 250 ms. AMI/AliMeeting/NOTSOFAR usam referências por alinhamento forçado. O modelo tem aproximadamente 100 milhões de parâmetros e até oito identidades. Em DIHARD, seu DER sobe de 9,13% no grupo de uma a quatro pessoas para 27,58% no grupo de cinco a nove. Não há promessa de perfeição em grupos grandes. Fonte: [cartão oficial](https://huggingface.co/nvidia/Nemotron-3-Diarization) e [protocolo de avaliação](https://huggingface.co/nvidia/Nemotron-3-Diarization/blob/main/diarization_evaluation.md).

### DiariZen: forte alternativa híbrida

DiariZen combina representação WavLM, segmentação e agrupamento de identidades. Resultados publicados, sem tolerância, com sobreposição:

| Corpus | Base s80 | Large s80 | Large s80 v2 |
| --- | ---: | ---: | ---: |
| AMI-SDM | 15,8 | 14,0 | **13,9** |
| AISHELL-4 | 10,7 | **9,8** | 10,1 |
| AliMeeting distante | 14,1 | 12,5 | **10,8** |
| NOTSOFAR-1 | 20,3 | 17,9 | **16,7** |
| MSDWild | 17,4 | **15,6** | 15,8 |
| DIHARD-III | 15,9 | **14,5** | **14,5** |
| RAMC | 11,4 | **11,0** | **11,0** |
| VoxConverse | 9,7 | 9,2 | **9,1** |

O código é MIT, mas os pesos publicados são CC BY-NC 4.0, com restrição não comercial. Esse ponto impede tratá-lo como substituição automática num produto comercial. Viabilidade e velocidade em CPU/Intel precisam ser medidas aqui. Fonte: [repositório oficial DiariZen](https://github.com/BUTSpeechFIT/DiariZen). Os 13,9% de AMI não podem ser diretamente ranqueados contra os 11,14% de AMI da NVIDIA com outra referência.

### SUPlime e SUPlime-L

A alternativa menor tem 114 milhões de parâmetros na segmentação; a maior, 349 milhões. DER publicado, sem tolerância e com sobreposição:

| Corpus | SUPlime | SUPlime-L |
| --- | ---: | ---: |
| AISHELL-4 | 11,55 | 11,21 |
| AliMeeting distante | 14,45 | 14,55 |
| AMI-IHM | 12,59 | 11,91 |
| AMI-SDM | 15,14 | 14,85 |
| AVA-AVD | 38,09 | 36,83 |
| MSDWild | 17,81 | 17,48 |
| RAMC | 10,86 | 11,10 |
| VoxConverse | 9,21 | 8,93 |
| Média de oito corpora | 16,21 | **15,86** |
| NOTSOFAR-1, 80 sessões | 20,04 | 22,70 |
| ICSI | 22,97 | 22,77 |
| CHiME-6 | 48,11 | 48,98 |
| DiPCo | 30,44 | 30,92 |
| Média de doze corpora | **20,94** | 21,02 |

Pesos CC BY-NC 4.0. A saída modela no máximo duas pessoas simultâneas por quadro, limitando sobreposições de três vozes. A comparação com outros sistemas mistura resultados publicados e reexecuções; NOTSOFAR usa divisão diferente da tabela DiariZen. Nemotron não está nessa comparação. Fonte: [cartão e resultados SUPlime](https://huggingface.co/rewayai/suplime).

### Community-1 e os nomes comerciais

Community-1 é local e funciona offline depois do preparo. A conta, aceitação e token do Hugging Face servem para obter os arquivos; não transformam sua inferência em serviço remoto. Precision-2/3 nas tabelas de APIs são outros produtos/configurações.

O cartão Community-1 publica DER de 17,0% em AMI-IHM, 19,9% em AMI-SDM, 20,2% em DIHARD, 20,3% em AliMeeting e 11,2% em VoxConverse, sem tolerância e incluindo sobreposição. Permite informar quantidade ou intervalo de falantes. O exemplo oficial de GPU usa CUDA; isso não comprova aceleração integral na Arc Intel. Fonte: [cartão e instruções oficiais](https://huggingface.co/pyannote/speaker-diarization-community-1). É um comparador útil, não um vencedor demonstrado para este caso.

### EEND-TA e Hyper-SD/TellWhisper

EEND-TA publica, sem ajuste específico de dataset, DER de 14,76% em DIHARD, 15,16% em AMI-SDM e 12,65% em AliMeeting distante; com ajuste por dataset, DIHARD chega a 14,49%. Não misturar o checkpoint ajustado para cada corpus com um único modelo geral. A inferência de GPU apresentada no artigo não equivale ao custo completo em CPU. Fonte: [artigo EEND-TA](https://arxiv.org/html/2509.14737v1).

Hyper-SD publica DER de 13,62% em AMI, 9,52% em AISHELL-4, 10,76% em AliMeeting, 12,28% em MSDWild, 10,94% em RAMC e 8,75% em VoxConverse, sem tolerância. TellWhisper aborda transcrição conjunta com informação de falante. Não validei uma distribuição instalável com pesos, licença e aceleração Intel que justifique substituir o backend do PACE. Fonte: [artigo ACL 2026 e tabelas](https://aclanthology.org/2026.acl-long.861.pdf). São concorrentes científicos relevantes; resultado em artigo e implantação portátil são critérios diferentes.

### Benchmark multilíngue de 2025

O estudo *Benchmarking Diarization Models* usa 196,6 horas, cinco idiomas, RTX A6000, tolerância de 250 ms e sobreposição incluída. Testa DiariZen, Sortformer e pyannote 3.1, entre outros. Não inclui Nemotron 3 nem estabelece a qualidade atual de Community-1. Também não inclui português. Portanto, serve para compreender variações por idioma, não para coroar o melhor backend em outubro de 2026. Fonte: [estudo e metodologia](https://arxiv.org/html/2509.26177v1).

## Modelos conjuntos: não confundir ASR com DER

Uma opção leve também investigada foi `notch-up/diarize`: publica aproximadamente 4,8% DER em VoxConverse dev, mas usa tolerância de 250 ms, **exclui sobreposição** e admite rotular apenas uma voz simultânea. A tabela reaproveita números pyannote de outro protocolo. Esse resultado não comprova superioridade para a necessidade relatada. Fonte: [benchmark e limitações do próprio projeto](https://github.com/notch-up/diarize/blob/main/docs/benchmarks.md).

| Opção | Evidência publicada | Consequência para o PACE |
| --- | --- | --- |
| MOSS-Transcribe-Diarize 0.9B | Modelo conjunto, 50+ idiomas; tabela principal usa CER, cpCER e delta, não DER | Candidato a transcrição atribuída; não é possível colocá-lo na mesma coluna de DER de Nemotron |
| VibeVoice-ASR | No teste clínico acima, DER 8,233% | Não demonstra vantagem sobre Nemotron naquele domínio |
| VibeVoice-ASR-BitNet | 1,58 GB; CPU; tabela oficial prioriza WER | Viabilidade interessante, sem comprovação de melhor diarização por essa tabela |
| DiCoW/SE-DiCoW | Whisper condicionado por informação do falante para transcrição por voz | Ataca diretamente parte do problema de recuperar falas sobrepostas |

MOSS publica, por exemplo, cpCER de 15,83% em AISHELL-4; esse número não deve ser comparado a DER. A versão Pro hospedada também não representa os pesos locais 0.9B. Fonte: [repositório oficial MOSS](https://github.com/OpenMOSS/MOSS-Transcribe-Diarize).

BitNet publica WER de 24,87% no conjunto MLC-PT e desempenho de CPU medido em AMD EPYC, com áudios de 20 s. Não é benchmark de DER nem medição no Ultra 7 deste computador. Fonte: [cartão Microsoft BitNet](https://huggingface.co/microsoft/VibeVoice-ASR-BitNet).

DiCoW tem pesos próprios CC BY 4.0, mas seu exemplo integra DiariZen com pesos não comerciais. Uma integração de produto precisa avaliar os componentes efetivamente usados. Fonte: [repositório oficial DiCoW](https://github.com/BUTSpeechFIT/DiCoW).

Uma investigação acadêmica recente de SpeechLMs encontrou forte dificuldade de consistência das identidades e fala simultânea: na configuração analisada, DER de 12,26% nos trechos de uma voz e 46,01% nos sobrepostos. Isso não é resultado de todos os modelos conjuntos; é evidência contra presumir que um modelo maior resolve a questão. Fonte: [Speech Language Models for Full-Meeting Speaker Diarization](https://arxiv.org/html/2609.23114v1).

## Por que a implementação atual perde qualidade na sobreposição

O capturador preserva `microphone.wav` e `computer.wav`, mas a cadeia atual transcreve a mistura mono `audio.wav`. Depois executa a diarização e associa cada palavra ao falante de maior interseção temporal; quando há ambiguidade relevante, deixa sem atribuição.

Isso cria dois problemas distintos: o diarizador pode detectar duas vozes, enquanto o reconhecedor omite palavras de uma delas; ou as palavras reconhecidas podem coincidir com dois falantes sem evidência suficiente para escolher. Alterar o diarizador não recupera automaticamente palavras ausentes da transcrição. Cada palavra atual recebe no máximo uma identidade.

O uso de faixas separadas pode preservar informações já capturadas. Entretanto, microfone não é necessariamente uma pessoa, e áudio do computador pode conter várias; também é preciso tratar vazamento/eco e duplicação. Sobreposição entre dois participantes remotos continua existindo na mesma faixa.

O preset `v3-offline` já está ativo: contexto alto, fluxo com estado para a reunião inteira, sem reiniciar identidades a cada leitura de cinco segundos. Ele não significa atenção completa sobre o arquivo. O runtime distingue atenção completa, limitada a gravações curtas, de streaming com contexto alto. Os padrões de segmentação da API C são ajustados a CALLHOME, oferecendo uma hipótese concreta de calibração. Fontes: [documentação do runtime](https://github.com/NVIDIA/NeMo-Speech.cpp/blob/main/docs/cli.md) e [API C da versão utilizada](https://github.com/NVIDIA/NeMo-Speech.cpp/blob/v0.2.0/include/nemo_speech/diar.h).

## Próxima avaliação recomendada

1. Montar amostras locais em português com referências humanas: turnos simples, interrupções, duas/três vozes simultâneas, pessoas com vozes parecidas e reuniões longas. Separar desenvolvimento de avaliação.
2. Medir o PACE atual antes de mudar: DER zero/250 ms, componentes de erro, DER nas regiões sobrepostas, contagem de pessoas e cpWER/tcpWER. Incluir omissões de palavras, não só troca de rótulos.
3. Comparar Nemotron Q8 versus F32 e CPU versus Vulkan, com os mesmos parâmetros e arquivos. Registrar inicialização, tempo total, memória e versões. Não supor que GPU melhora precisão.
4. Calibrar limiares de segmentação e duração mínima em desenvolvimento; avaliar depois em arquivos separados. Em gravações curtas, atenção completa é uma ablação possível, não solução assumida para reuniões longas.
5. Comparar transcrição das faixas preservadas com a mistura atual. Medir vazamento, duplicação e recuperação das duas falas. Avaliar transcrição por falante/separação nos trechos que continuam misturados.
6. Rodar Community-1 como comparador local e, quando adequados ao uso e aos termos dos pesos, DiariZen/SUPlime. Só promover uma alternativa se melhorar transcrição atribuída e custo real neste computador.
7. Revisar o cache de seleção de hardware: uma decisão anterior por CPU não deve impedir indefinidamente nova avaliação representativa da GPU.

Não houve troca de modelo, alteração da configuração do aplicativo, download de novos pesos, envio de gravação para benchmark externo nem reprocessamento da reunião nesta pesquisa. O artefato novo é este relatório.
