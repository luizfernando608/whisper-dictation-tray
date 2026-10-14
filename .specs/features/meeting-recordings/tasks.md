# Implementação

Premissas: Windows x64; dados persistentes fora da pasta substituída pelo instalador;
diarização real com identidades globais; dependências/modelos validados antes de
declarar aceleração. Não publicar release sem concluir os gates de revisão.

## Contratos e tarefas

- T1 — Captura/storage/importação (R01–R09): `recording_store.py`, `meeting_audio.py`,
  `media.py`, testes correspondentes. `RecordingStore(root)` fornece `create(title,
  origin)`, `get(id)`, `list()`, `update(id, **fields)`, `delete(id)`, `directory(id)`.
  Registros dict com id/title/origin/created_at/duration/status/progress/error,
  audio_path/result_path/backend/speaker_names. `MeetingRecorder(directory,
  input_device=None, output_device=None, logger=None)` fornece start/pause/resume/
  stop (retorna Path do áudio), active/paused/duration/levels. Dispositivos são
  identificadores/nome PyAudioWPatch, não índices sounddevice. `list_meeting_devices()`
  retorna dict com listas inputs/outputs de dicts id/name; padrão None.
  `decode_media(source, destination, cancel_event=None, progress=None)` grava WAV mono
  16k PCM16 e retorna duração. Dados gerados em diretório do registro.
- T2 — Diarização/processamento (R09–R18): `diarization.py`, `meeting_processing.py`,
  testes. `process_recording(store_root, recording_id, config_path)` executa em
  subprocesso, escreve result.json com text/segments [{start,end,text,speaker}],
  turns, backend e atualiza store. Respeita marcador cancel.request no diretório.
  Preparação de modelos explícita via `prepare_models(config, model_root, progress)`.
  Status: queued/transcribing/diarizing/ready/failed/cancelled/interrupted; não
  apresentar ready se diarização falhar. Retentativa aproveita checkpoints válidos.
- T3 — Atualização segura (R19–R22): `updater.py`, `update_helper.py`, testes.
  Manter APIs existentes; adicionar `prepare_update(update)` para cache/download
  validado e `restart_to_update(update, project_root)` iniciando helper externo
  que aguarda fechamento, instala silenciosamente e relança. Integração app/UI
  feita pela raiz. Exceção explícita em check sem rede, pacote exato/digest.
- T4 — Integração/UI: raiz implementa `meeting_service.py`, `recordings_window.py`,
  settings.py/settings_window.py/settings_ui.py, app.py, main.py, IPC, build e deps.
  Queue worker separado; UI própria no thread principal; mesmos dados no tray/UI.
- T5 — Gates: testes unittest sem hardware para armazenamento, importação, pausa,
  recovery/fallback/merge/update; smoke real com mídia sintética; compileall;
  build PyInstaller/Inno; revisão independente por módulo e final. Hardware/modelos
  não disponíveis devem ser registrados; não fingir verificações executadas.

Cada tarefa segue implementação → testes → revisão independente → correções →
revisão. Não encerrar entrega com falhas relevantes abertas.

## Progresso

- T1: em andamento.
- T2: em andamento.
- T3: em andamento.
- T4: em andamento.
- T5: pendente.
