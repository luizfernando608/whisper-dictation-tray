# Execução

- [x] T1 — Reproduzir lacunas no cache vazio e separar erro remoto de causas confirmadas no código (R1/R4).
- [x] T2 — Auto-preparo Nemotron, CPU independente de Vulkan, downloads/lock/cancelamento e regressões RED→GREEN (R1/R2/R3). Ownership: inspect_diarizer, nemotron_diarization.py/test_nemotron_diarization.py.
- [x] T3 — ASR local/fallback em cache vazio e etapas na biblioteca, preservando transcrição/checkpoints (R4). Root: meeting_processing.py/test_meeting_processing.py; embedded_settings: recordings_window.py/test_recordings_ui.py.
- [x] T4 — Cache/HF_HOME realmente novos: downloads e pipeline real com áudio público; segunda execução offline sem download; falha recuperável preservando dados (R5). Fonte e frozen aprovados; guardas de falha/cancelamento/regressão aprovadas.
- [x] T5 — Revisão independente, correções e repetição proporcional dos gates afetados (R3/R5). review_next_release: fonte, UI, harnesses, smokes e resultado frozen sem bloqueios; revisão instalada/publicação acompanha T6.
- [ ] T6 — Build 1.5.2, fonte congelada conferida, teste instalado e atualização/publicação/verificação de asset. Root, serializar testes que compartilham IPC/registro; preservar a prévia e os 20 WAVs.
