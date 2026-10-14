"""Render the actual UI with synthetic data; never reads user recordings or keys."""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import tkinter as tk
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import customtkinter as ctk
import numpy as np
from PIL import ImageGrab

from whisper_dictation.media import PCM16WaveWriter
from whisper_dictation.recording_store import RecordingStore
from whisper_dictation.recordings_window import RecordingsWindow
from whisper_dictation.settings import AppConfig
from whisper_dictation.settings_window import SettingsContext, _run_window
from whisper_dictation.ui_utils import close_window


def screenshot(root, destination):
    root.update_idletasks()
    root.lift()
    root.update()
    x, y = root.winfo_rootx(), root.winfo_rooty()
    ImageGrab.grab(bbox=(x, y, x + root.winfo_width(), y + root.winfo_height())).save(destination)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", type=Path)
    parser.add_argument("--settings", action="store_true")
    parser.add_argument("--scroll", type=float, default=0)
    args = parser.parse_args()
    args.destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pace-ui-preview-") as temporary:
        directory = Path(temporary)
        config = AppConfig(meeting_storage_dir=str(directory / "library"))
        if args.settings:
            root = ctk.CTk()
            def capture():
                def scroll_frames(widget):
                    if isinstance(widget, ctk.CTkScrollableFrame):
                        widget._parent_canvas.yview_moveto(args.scroll)
                    for child in widget.winfo_children():
                        scroll_frames(child)
                for widget in root.winfo_children():
                    if isinstance(widget, ctk.CTkTabview):
                        widget.set("Reuniões")
                root.update_idletasks()
                scroll_frames(root)
                screenshot(root, args.destination)
                close_window(root)
            root.after(1400, capture)
            with patch("whisper_dictation.settings_window.ctk.CTk", return_value=root), \
                 patch("whisper_dictation.settings_window.secrets_store.get_api_key", return_value=None), \
                 patch.object(root, "mainloop", side_effect=lambda: tk.Tk.mainloop(root)):
                _run_window(config, lambda _: False, SettingsContext())
        else:
            store = RecordingStore(directory / "library")
            for title, status in (("Planejamento da equipe", "ready"), ("Entrevista — áudio importado", "failed"), ("Alinhamento semanal", "ready")):
                row = store.create(title, "import" if status == "failed" else "meeting")
                folder = store.directory(row["id"])
                with PCM16WaveWriter(folder / "audio.wav") as writer:
                    writer.writeframesraw(np.zeros(16000, dtype="<i2").tobytes())
                result = {"segments": [
                    {"start": 0, "end": 12, "speaker": "SPEAKER_00", "text": "Vamos começar pelo andamento do projeto e pelas entregas desta semana."},
                    {"start": 14, "end": 26, "speaker": "SPEAKER_01", "text": "A captura de áudio está pronta. Vou conferir a sincronização e testar os arquivos importados."},
                    {"start": 28, "end": 40, "speaker": "SPEAKER_00", "text": "Combinado. Depois fazemos a revisão independente antes de distribuir a atualização."}]}
                (folder / "result.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
                store.update(row["id"], status=status, duration=1842, progress=1,
                             audio_path=str(folder / "audio.wav"), result_path=str(folder / "result.json"),
                             error="Modelos ainda não preparados. O áudio foi preservado." if status == "failed" else "",
                             speaker_names={"SPEAKER_00": "Maria", "SPEAKER_01": "João"})
            with patch("whisper_dictation.recordings_window.AppConfig.load", return_value=config):
                window = RecordingsWindow(directory)
                window.root.after(1200, lambda: (screenshot(window.root, args.destination), window.close()))
                window.root.mainloop()
    print(args.destination.resolve())


if __name__ == "__main__":
    main()
