from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from whisper_dictation.app import DictationApp
from whisper_dictation.audio import list_input_devices


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Whisper Dictation Tray para Windows.")
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="Lista os microfones de entrada detectados pelo PortAudio.",
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

    app = DictationApp(project_root=PROJECT_ROOT)
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

