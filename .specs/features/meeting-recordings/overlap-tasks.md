# Melhorar processamento de falas sobrepostas

Autorizado em 15/10/2026: usar a reunião de teste existente (usuário + vídeo com duas vozes) para experimentar, melhorar processamento e comparar com modelo independente maior. Acesso a APIs somente pelas credenciais PACE existentes; nenhum cadastro ou compra. Conteúdo privado e resultados completos ficam em `temp/` ignorado e na biblioteca local, nunca nos documentos versionados.

## Requisitos e hipóteses

- O1: preservar fala simultânea de microfone e saída do computador, aproveitando as faixas capturadas quando verificadas como alinhadas.
- O2: não presumir que canal é pessoa; não criar identidades artificiais para eco nem excluir sobreposição indiscriminadamente.
- O3: manter compatibilidade de importações mono, checkpoints, cancelamento, exportação e biblioteca.
- O4: comparar com referência independente maior; saída de outro modelo não é verdade humana e divergência não é DER real.
- O5: medir no áudio autorizado, revisar independentemente, construir e validar o preview local sem publicar.

Hipótese a verificar: o predomínio da saída do computador na mistura e a associação temporal na mistura perdem/atribuem incorretamente a fala do microfone. Auditoria inicial identifica vazamento do playback no microfone: processamento separado ingênuo pode duplicar falantes. A abordagem será escolhida após a comparação experimental.

## Tarefas

- [x] T1 — Baseline e experimento isolado: medir níveis/alinhamento/vazamento, ASR e diarização das faixas; preservar arquivos originais. Gate: resultados reproduzíveis em `temp/` e métricas sem conteúdo privado no relatório versionado.
- [x] T2 — Referência maior: preparar ambiente isolado para MOSS0.9B CPU (modelo independente, nove vezes os parâmetros nominais de Nemotron100M), executar no mesmo áudio e verificar saída completa. Gate: registrar revisão/artefato/tempo/limitações; não declarar maior como superior sem evidência.
- [x] T3 — Implementar abordagem validada: alterar somente cadeia de reunião, helpers necessários e testes relacionados. Gate: testes de sobreposição, eco/duplicação, IDs, mono/import, checkpoints e cancelamento; nenhuma dependência pesada nova no produto.
- [x] T4 — Comparação antes/depois: repetir cadeia real em biblioteca de teste e avaliar recuperação de falas, atribuição e duração. Gate: não confundir mais palavras/falantes com maior precisão; manter referência independente como hipótese auditável.
- [x] T5 — Revisão independente: revisar mudanças e corrigir achados. Gate: testes completos, compilação e revisão resolvida.
- [x] T6 — Preview local: construir binário, executar worker congelado na amostra, preservar backup do resultado anterior e disponibilizar gravação reprocessada se aprovada pelos testes. Gate: resultado `ready` no binário e integridade dos áudios originais; sem publicação.

Arquivos esperados: `meeting_processing.py`, eventual helper de processamento por fonte, testes específicos, script de benchmark/reprodução, este plano e relatório de validação. Revisar seleção de GPU somente se necessária ao experimento; não ampliar escopo para outras telas.
