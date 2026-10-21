# Execução e verificação

Premissa: “corrompido” pode ser erro de integridade do loader Inno; falta o texto exato do computador afetado. Não ler chaves nem transcrições. Alterar somente os componentes necessários após reproduzir o defeito.

- [x] T1 — Auditoria e reprodução: updater legado/atual, instalador público, falhas de download e lacunas de cobertura. Verificar SHA público real e reproduzir EOF prematuro sem executar conteúdo arbitrário. Arquivos de evidência em `temp/update-incident-20261021`, relatório nesta spec.
- [x] T2 — Download robusto e inicialização confirmada do auxiliar (R2/R3). Arquivos: `updater.py`, `update_helper.py`, testes específicos. Primeiro testes vermelhos para retry limitado/sem execução inválida e auxiliar que morre antes de ready; depois implementação mínima. Reutilizar tamanho/hash, cache, isolamento e rollback existentes.
- [x] T3 — Regressão real da distribuição e primeiro salto legado (R1/R4). Arquivos: harness opt-in em `scripts`, testes isolados e `installer.iss` apenas para restrições comprovadas de plataforma. Executar pacote realmente baixado e staging real do helper congelado, sem copiar/helper novo por atalho do teste. Injetar falhas de bootstrap/instalação/confirmação e comparar dados/integração restaurados.
- [x] T4 — Revisão independente → correções → repetição dos cenários afetados (R5). Gerar candidato separado e relatório rastreável; manter distinta a validação local, o problema remoto ainda sem logs e a publicação.
