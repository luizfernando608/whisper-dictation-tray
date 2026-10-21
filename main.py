from __future__ import annotations

import argparse
import sys
from pathlib import Path

if getattr(sys, 'frozen', False):
    PROJECT_ROOT = Path(sys.executable).parent
else:
    PROJECT_ROOT = Path(__file__).resolve().parent

SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from whisper_dictation.single_instance import OPEN_SETTINGS, OPEN_RECORDINGS, CAPABILITIES, SingleInstance, probe, send_command, request


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PACE — Voice Dictation para Windows.")
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="Lista os microfones de entrada detectados pelo PortAudio.",
    )
    parser.add_argument(
        "--settings",
        action="store_true",
        help="Abre a janela de configurações (na instância já em execução, se houver).",
    )
    parser.add_argument(
        "--settings-ui",
        action="store_true",
        help="Interno: roda apenas a janela de configurações (processo dedicado).",
    )
    parser.add_argument("--recordings", action="store_true", help="Abre a biblioteca de gravações.")
    parser.add_argument("--recordings-ui", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--ui-page", choices=("recordings", "settings"), default="recordings", help=argparse.SUPPRESS)
    parser.add_argument("--meeting-overlay", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--process-recording", help=argparse.SUPPRESS)
    parser.add_argument("--store-root", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--config-path", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--prepare-models", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--progress-file", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--apply-update", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--update-plan", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--diarization-runtime", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--runtime-model", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--runtime-backend", help=argparse.SUPPRESS)
    parser.add_argument("--nemotron-runtime", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--native-root", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--native-device", help=argparse.SUPPRESS)
    parser.add_argument("--native-audio", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--native-result", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--diagnostics", action="store_true", help="Mostra versão, instalação e limite efetivo, sem chaves de API.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.nemotron_runtime:
        if not all((args.native_root, args.native_device, args.native_audio, args.native_result)):
            raise ValueError("Modelo, dispositivo, áudio e saída obrigatórios.")
        from whisper_dictation.nemotron_diarization import native_stream_worker
        return native_stream_worker(args.native_root, args.native_audio, args.native_device, args.native_result)
    if args.diarization_runtime:
        if not args.runtime_model or not args.runtime_backend:
            raise ValueError("Modelo e backend obrigatórios.")
        from whisper_dictation.diarization import runtime_server
        return runtime_server(args.runtime_model, args.runtime_backend)
    if args.apply_update:
        if not args.update_plan:
            raise ValueError("Plano de atualização obrigatório.")
        from whisper_dictation.update_helper import main as update_main
        return update_main(["--update-plan", str(args.update_plan)])
    if args.process_recording:
        if not args.store_root or not args.config_path:
            raise ValueError("Biblioteca e configurações obrigatórias.")
        from whisper_dictation.meeting_processing import process_recording
        process_recording(args.store_root, args.process_recording, args.config_path)
        return 0
    if args.prepare_models:
        import json
        import os
        from whisper_dictation.diarization import prepare_models
        from whisper_dictation.settings import AppConfig, config_path, data_dir
        config = AppConfig.load(args.config_path or config_path(PROJECT_ROOT))
        def progress(message, fraction=None):
            if args.progress_file:
                temporary = args.progress_file.with_suffix(".tmp")
                try:
                    temporary.write_text(json.dumps({"message": str(message), "progress": fraction}, ensure_ascii=False), encoding="utf-8")
                    os.replace(temporary, args.progress_file)
                except OSError:
                    # Progress is advisory; a Windows reader can briefly prevent
                    # replacement. The next callback refreshes it without stopping
                    # a valid download or inference.
                    temporary.unlink(missing_ok=True)
        try:
            prepare_models(config, Path(config.diarization_model_dir) if config.diarization_model_dir else data_dir() / "models", progress)
            progress("Modelos preparados. Pronto para identificar falantes.")
        except Exception as exc:
            progress(f"Falha no preparo: {exc}")
            return 1
        return 0
    if args.diagnostics:
        import json
        from whisper_dictation._version import __version__
        from whisper_dictation.settings import AppConfig, config_path, recordings_dir
        config = AppConfig.load(config_path(PROJECT_ROOT))
        print(json.dumps({"version": __version__, "executable": sys.executable, "project_root": str(PROJECT_ROOT),
                          "config_path": str(config_path(PROJECT_ROOT)), "record_limit_seconds": config.max_record_seconds,
                          "recordings": str(recordings_dir(config))}, indent=2, ensure_ascii=False))
        return 0
    if args.list_devices:
        from whisper_dictation.audio import list_input_devices

        devices = list_input_devices()
        if not devices:
            print("Nenhum dispositivo de entrada encontrado.")
            return 0

        for index, name, sample_rate in devices:
            print(f"[{index}] {name} | default_sample_rate={sample_rate}")
        return 0

    if args.settings_ui:
        from whisper_dictation.settings_ui import run_settings_ui

        run_settings_ui(PROJECT_ROOT)
        return 0

    if args.recordings_ui:
        from whisper_dictation.recordings_window import run_recordings_window
        run_recordings_window(PROJECT_ROOT, initial_page=args.ui_page)
        return 0
    if args.meeting_overlay:
        from whisper_dictation.recordings_window import run_meeting_overlay
        run_meeting_overlay(PROJECT_ROOT)
        return 0

    from whisper_dictation.app import DictationApp
    from whisper_dictation import updater
    instance = SingleInstance()
    if instance.try_acquire():
        app = DictationApp(
            project_root=PROJECT_ROOT,
            instance=instance,
            open_settings_on_start=args.settings,
        )
        if args.recordings:
            app._open_recordings_now()
        app.run()
        return 0

    # Não conseguiu ligar o canal de controle. É a nossa instância mesmo?
    if probe():
        from whisper_dictation._version import __version__
        try:
            owner = request(CAPABILITIES, timeout=3)
            matching = (owner.get("version") == __version__ and
                        Path(owner.get("project_root", "")).resolve() == PROJECT_ROOT.resolve())
        except Exception:
            matching = False
        if not matching:
            from tkinter import messagebox
            messagebox.showerror("PACE", "Há outra instalação ou versão antiga do PACE aberta.\nFeche o PACE pelo ícone da bandeja e abra novamente o aplicativo desta pasta.")
            return 1
        # Já existe uma instância rodando: encaminha o pedido e sai (sem 2ª bandeja).
        if args.settings:
            send_command(OPEN_SETTINGS)
        if args.recordings:
            send_command(OPEN_RECORDINGS)
        return 0

    # Porta ocupada por um processo não relacionado: sobe assim mesmo, sem o guard.
    app = DictationApp(
        project_root=PROJECT_ROOT,
        instance=None,
        open_settings_on_start=args.settings,
    )
    if args.recordings:
        app._open_recordings_now()
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

