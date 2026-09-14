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

from whisper_dictation.app import DictationApp
from whisper_dictation.audio import list_input_devices
from whisper_dictation.single_instance import OPEN_SETTINGS, SingleInstance, probe, send_command


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
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.list_devices:
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

    instance = SingleInstance()
    if instance.try_acquire():
        app = DictationApp(
            project_root=PROJECT_ROOT,
            instance=instance,
            open_settings_on_start=args.settings,
        )
        app.run()
        return 0

    # Não conseguiu ligar o canal de controle. É a nossa instância mesmo?
    if probe():
        # Já existe uma instância rodando: encaminha o pedido e sai (sem 2ª bandeja).
        if args.settings:
            send_command(OPEN_SETTINGS)
        return 0

    # Porta ocupada por um processo não relacionado: sobe assim mesmo, sem o guard.
    app = DictationApp(
        project_root=PROJECT_ROOT,
        instance=None,
        open_settings_on_start=args.settings,
    )
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

