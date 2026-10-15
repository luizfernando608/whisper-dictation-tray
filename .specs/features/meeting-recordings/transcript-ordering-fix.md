# Correção de ordenação da transcrição — 2026-10-15

## Problema

Uma reunião falhou com `Provedor retornou palavras fora de ordem`. O provedor
pode devolver palavras fora de ordem; além disso, a projeção de cada palavra
global em todos os segmentos compatíveis duplicava palavras em sobreposições e
descartava as que não pertenciam a nenhum segmento. O validador estrito rejeitava
o resultado depois da chamada, antes de qualquer checkpoint ser salvo.

## Correção

- `_transcribe_chunk` mantém a lista global original, sem a projeção com perdas.
- `_normalise_transcript` valida cada intervalo antes de filtrar; ordena
  estavelmente pelo início, preservando empates e repetições legítimas. Cada
  palavra aparece uma vez; não há deduplicação heurística nem timestamps criados.
- Segmentos sem nenhuma palavra temporalmente sobreposta permanecem como blocos
  coarse, intercalados à sequência de palavras. A mesma regra preserva respostas
  locais/legadas com uma mistura de blocos e alinhamentos reais.
- `merge_transcript` ordena globalmente antes de avançar o cursor de falantes;
  continua preservando lacunas e atribuições ambíguas sem inventar identidades.
- Resposta original serializada em `transcript-XXXXX.raw.json` antes de normalizar.
  O fingerprint depende do áudio/parâmetros ASR, não da versão do normalizador.
  Uma resposta válida pode ser reinterpretada sem chamar o provedor novamente.
  Snapshot temporalmente inválido permite nova chamada na tentativa seguinte.
  Checkpoints antigos válidos permanecem reutilizáveis.

Limite: respostas descartadas antes desta correção não podem ser recuperadas do
cache inexistente. Checkpoints legados já projetados não permitem reconstruir
palavras que foram removidas nem distinguir duplicações de repetições legítimas.
Nenhuma promessa de reparo heurístico desses casos; o raw evita a perda futura.

## Verificação

- 127 testes na suíte geral: 126 aprovados e um smoke de runtimes opcional não
  habilitado; 17 testes de processamento e oito regressões independentes aprovados.
- Casos: segmentos sobrepostos, ordem invertida, empates com fins diferentes,
  repetições idênticas, palavras órfãs, blocos coarse sem palavras globais,
  NaN/Inf/intervalo invertido/fora de faixa, retomada/legado e troca de modelo ASR.
- Revisão independente concluída sem achados abertos no escopo da correção.
- Gravação reportada de 155,125 s recuperada com o provedor já configurado e
  diarização local. Status `ready`, transcrição e resultado completos; SHA-256 do
  áudio antes/depois idêntico. Nenhum conteúdo da reunião incluído no repositório.
- A nova resposta real tinha 465 palavras, 51 segmentos, oito retrocessos de
  timestamp e nove palavras que a projeção antiga perderia. Todas as 465 foram
  preservadas, sem alterar o áudio. Snapshot e resultado mantidos na biblioteca
  privada; métricas sem falas em `temp/recover-word-order.log` e no ensaio
  `temp/audit_word_order_recovery.py`.

Escopo de distribuição: atualizar a prévia portátil local; não publicar. O
instalador precisa ser regenerado antes da publicação, como registrado na
validação da feature.
