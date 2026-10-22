# Publicação 1.5.2 — 21/10/2026

Release pública `v1.5.2` latest desde 22/10/2026 00:00:16 UTC (21/10/2026 21:00:16 Fortaleza), tag anotada apontando para `f7ed27c37d0e3c47aac744b80fa8223d7aa57f0d`. Código de produção no commit `d83465f`; commits posteriores anteriores à tag contêm harnesses/evidências/documentação.

- Asset único `Pace_Installer.exe`, 327.415.822 bytes, SHA256 `b0573e13d606c835e0be6b75dfd5d1f30f25a1c575b682711f6920ded713b18f`.
- EXE SHA256 `2af16b5adb5790aabf12741fd4fb7dca67776ad1ecdd460d295a1f7066055274`. Code objects dos 32 módulos e main correspondem à fonte da tag, verificados antes da publicação.
- Asset do draft conferido pela API por ID antes de publicar; endpoint de tag não retorna esse draft. Tag remota conferida até o commit. Releases 1.5.0/1.5.1 preservadas.
- `Output/v1.5.2/public-download-verification.json`: download público real pelo downloader atual, com SHA/tamanho conferidos. Apenas versão de comparação alterada para 1.5.1 no processo da fonte; nenhum código injetado no aplicativo público. Não equivale ao botão da instalação antiga.

Gates de fonte/frozen/instalado/instalação e limites em [validation.md](validation.md). Revisão independente sem bloqueios antes da publicação.

## Botão público real

`C:/Users/ziull/Documents/pace-published152-ui-20261021/report-restart-ui.json`: aprovado, exit0. Instalou baseline pública 1.5.1 (EXE `4f1ca0bc1c77cb6e2fccf1d606f00400a58c7ac3be8d03a460d930744c606a4f`), abriu Sobre e clicou fisicamente em Verificar atualizações e Reiniciar para atualizar via @oai/sky com screenshot. Observou Atualização pronta: 1.5.2 antes do segundo clique. Não houve código injetado, transporte simulado ou candidato semeado nesse gate.

O harness observou o helper original 1.5.1 e validou seu SHA/PID/pai/plano, download real com SHA/tamanho públicos, receipt token/target/PID/versão, IPC da nova versão e registro do target. Resultado updated1.5.2; canários de áudio/config/env/modelo/falante preservados. Finally restaurou registro/atalhos, reabriu a prévia e conferiu hashes de configuração/env e dos 20 WAVs originais, cleanup_errors vazio. Não equivale a validação em outro hardware/VM.

## Ativação neste computador

`C:/Users/ziull/Documents/pace-speaker-152-preview-20261021/report.json`: status passed, processo exit0. Nova prévia física `C:/Users/ziull/AppData/Local/Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/PACE-Preview-1.5.2`, com EXE/runtime idênticos à instalação validada. Configuração/env copiados da prévia anterior, mantendo os mesmos caminhos de biblioteca e modelos. Cinco atalhos existentes foram retargetados com backup; lançamento real pelo atalho do menu Iniciar abriu 1.5.2, repetição não duplicou janela e configurações/biblioteca alternaram no mesmo HWND. Os 20 WAVs/config/env originais tiveram hashes reconferidos e o registro permaneceu intacto. Prévia anterior preservada. A cópia e hashes completos do runtime levaram cerca de cinco minutos; não houve gate omitido por demora.

Para a gravação com status Falha no outro computador, atualizar pelo PACE e escolher **••• → Processar novamente**. Os modelos faltantes são preparados no worker; a primeira preparação exige acesso aos downloads públicos. O áudio/transcrição parcial/checkpoints existentes são preservados. A identificação não foi reexecutada remotamente pelo agente.
