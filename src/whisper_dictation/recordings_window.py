"""Library shared by captured meetings and imported audio/video."""
from __future__ import annotations

import json
import os
import shutil
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

import customtkinter as ctk

from whisper_dictation.playback import WavePlayer, timestamp, transcript_text, speaker_label
from whisper_dictation.recording_store import RecordingStore
from whisper_dictation.settings import AppConfig, config_path, recordings_dir
from whisper_dictation.settings_window import COLOR_BG_ROOT, COLOR_BG_CARD, COLOR_TEXT_MAIN, COLOR_TEXT_MUTED, _find_asset
from whisper_dictation.single_instance import MEETING_PAUSE, MEETING_TOGGLE, OPEN_SETTINGS, CAPABILITIES, IMPORT_MEDIA, request, send_command
from whisper_dictation.ui_utils import close_window, fit_window

STATUS = {"new": "Preparando gravação", "starting": "Iniciando gravação", "queued": "Na fila", "recording": "Gravando", "paused": "Pausada", "finalizing": "Salvando áudio",
          "processing": "Preparando", "importing": "Importando", "transcribing": "Transcrevendo",
          "diarizing": "Identificando falantes", "ready": "Pronta", "failed": "Falha",
          "cancelled": "Cancelada", "cancelling": "Cancelando", "interrupted": "Interrompida"}
BUSY = {"new", "starting", "queued", "recording", "paused", "finalizing", "processing", "importing", "transcribing", "diarizing", "cancelling"}


def read_runtime(root: Path) -> dict:
    try:
        state = json.loads((root / "runtime.json").read_text(encoding="utf-8"))
        return state if time.time() - state.get("updated_at", 0) < 5 else {}
    except (OSError, ValueError):
        return {}


class RecordingsWindow:
    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.config = AppConfig.load(config_path(project_root))
        self.store_root = recordings_dir(self.config)
        self.store = RecordingStore(self.store_root)
        self.player = WavePlayer()
        self.selected: str | None = None
        self.result: dict = {}
        self._signature = None
        self._detail_signature = None
        self._closing = False
        ctk.set_appearance_mode("dark")
        self.root = ctk.CTk()
        self.root.title("PACE — Gravações")
        self.root.configure(fg_color=COLOR_BG_ROOT)
        icon = _find_asset("assets/branding/pace.ico")
        if icon:
            self.root.iconbitmap(str(icon))
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._build()
        fit_window(self.root)
        try:
            from tkinterdnd2 import DND_FILES, TkinterDnD
            TkinterDnD.require(self.root)
            self.tree.drop_target_register(DND_FILES)
            self.tree.dnd_bind("<<Drop>>", lambda event: self.import_files(self.root.tk.splitlist(event.data)))
        except Exception as exc:
            self.notice.configure(text=f"Arrastar arquivos indisponível; use Importar arquivo. {exc}")
        self.root.after(100, self.refresh)

    def _build(self):
        top = ctk.CTkFrame(self.root, fg_color="transparent")
        top.pack(fill="x", padx=20, pady=(18, 10))
        ctk.CTkLabel(top, text="Gravações", font=ctk.CTkFont(size=24, weight="bold")).pack(side="left")
        ctk.CTkButton(top, text="Configurações", width=120, command=lambda: send_command(OPEN_SETTINGS)).pack(side="right", padx=(8, 0))
        ctk.CTkButton(top, text="Importar arquivo", width=140, command=self.choose_import).pack(side="right")
        self.record_button = ctk.CTkButton(top, text="Gravar reunião", command=lambda: self.control(MEETING_TOGGLE))
        self.record_button.pack(side="right", padx=8)
        self.pause_button = ctk.CTkButton(top, text="Pausar", width=85, command=lambda: self.control(MEETING_PAUSE))
        self.pause_button.pack(side="right")
        self.notice = ctk.CTkLabel(self.root, text="Importe ou arraste áudio/vídeo para a lista. Reuniões são salvas automaticamente.", text_color=COLOR_TEXT_MUTED, anchor="w")
        self.notice.pack(fill="x", padx=20, pady=(0, 8))
        self.search = ctk.CTkEntry(self.root, placeholder_text="Buscar gravações pelo título...")
        self.search.pack(fill="x", padx=20, pady=(0, 10))
        self.search.bind("<KeyRelease>", lambda _: self.refresh_list())
        panes = tk.PanedWindow(self.root, orient="horizontal", bg=COLOR_BG_ROOT, sashwidth=8, bd=0)
        panes.pack(fill="both", expand=True, padx=20, pady=(0, 20))
        left = ctk.CTkFrame(panes, fg_color=COLOR_BG_CARD)
        right = ctk.CTkFrame(panes, fg_color=COLOR_BG_CARD)
        panes.add(left, minsize=270, width=335)
        panes.add(right, minsize=470)
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("PACE.Treeview", background=COLOR_BG_CARD, foreground=COLOR_TEXT_MAIN, fieldbackground=COLOR_BG_CARD,
                        rowheight=42, borderwidth=0, font=("Segoe UI", 10))
        style.map("PACE.Treeview", background=[("selected", "#2563EB")])
        style.configure("PACE.Treeview.Heading", background="#171B28", foreground=COLOR_TEXT_MAIN)
        self.tree = ttk.Treeview(left, columns=("status",), show="tree headings", style="PACE.Treeview", selectmode="browse")
        self.tree.heading("#0", text="Título / data")
        self.tree.heading("status", text="Estado")
        self.tree.column("#0", width=220, minwidth=130)
        self.tree.column("status", width=110, minwidth=85)
        scrollbar = ttk.Scrollbar(left, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.select)
        self.title = ctk.CTkEntry(right, placeholder_text="Selecione uma gravação")
        self.title.pack(fill="x", padx=16, pady=(16, 6))
        self.title.bind("<Return>", lambda _: self.rename())
        self.meta = ctk.CTkLabel(right, text="Áudio e transcrição aparecerão aqui.", anchor="w", wraplength=530, justify="left")
        self.meta.pack(fill="x", padx=16, pady=(0, 8))
        audio_row = ctk.CTkFrame(right, fg_color="transparent")
        audio_row.pack(fill="x", padx=16)
        self.play_button = ctk.CTkButton(audio_row, text="Reproduzir", width=100, command=self.play)
        self.play_button.pack(side="left")
        self.clock = ctk.CTkLabel(audio_row, text="00:00:00 / 00:00:00")
        self.clock.pack(side="right")
        self.seek = ctk.CTkSlider(right, from_=0, to=1, command=self.player.seek)
        self.seek.set(0)
        self.seek.pack(fill="x", padx=16, pady=10)
        find_row = ctk.CTkFrame(right, fg_color="transparent")
        find_row.pack(fill="x", padx=16, pady=(0, 8))
        self.find_entry = ctk.CTkEntry(find_row, placeholder_text="Buscar na transcrição...")
        self.find_entry.pack(side="left", fill="x", expand=True)
        self.find_entry.bind("<Return>", lambda _: self.find())
        ctk.CTkButton(find_row, text="Buscar", width=65, command=self.find).pack(side="left", padx=(8, 0))
        ctk.CTkButton(find_row, text="Falantes", width=85, command=self.rename_speakers).pack(side="left", padx=(8, 0))
        self.text = tk.Text(right, bg="#121520", fg=COLOR_TEXT_MAIN, insertbackground=COLOR_TEXT_MAIN,
                            font=("Segoe UI", 11), height=4, wrap="word", relief="flat", padx=12, pady=12, state="disabled")
        self.text.tag_configure("found", background="#2563EB", foreground="white")
        self.text.pack(fill="both", expand=True, padx=16)
        actions = ctk.CTkFrame(right, fg_color="transparent")
        actions.pack(fill="x", padx=16, pady=12)
        for label, command in (("Copiar", self.copy), ("Exportar texto", self.export_text), ("Exportar áudio", self.export_audio)):
            ctk.CTkButton(actions, text=label, width=90, command=command).pack(side="left", padx=(0, 6))
        more = ctk.CTkFrame(right, fg_color="transparent")
        more.pack(fill="x", padx=16, pady=(0, 16))
        for label, command in (("Renomear", self.rename), ("Tentar novamente", self.retry), ("Cancelar tarefa", self.cancel), ("Excluir", self.delete)):
            ctk.CTkButton(more, text=label, width=80, fg_color="#23283A", command=command).pack(side="left", padx=(0, 6))

    def row(self):
        try:
            return self.store.get(self.selected) if self.selected else None
        except KeyError:
            self.selected = None
            self.player.close()
            self.result = {}
            return None

    def control(self, command):
        try:
            self.ensure_owner()
            if not send_command(command):
                raise RuntimeError("Abra o PACE na bandeja para controlar uma reunião.")
        except Exception as exc:
            messagebox.showerror("PACE", str(exc), parent=self.root)

    def ensure_owner(self):
        try:
            capabilities = request(CAPABILITIES, timeout=3)
        except Exception as exc:
            raise RuntimeError("Abra a versão atual do PACE na bandeja antes de usar reuniões ou importar arquivos.") from exc
        if not capabilities.get("meetings") or Path(capabilities.get("project_root", "")).resolve() != self.project_root.resolve():
            raise RuntimeError("Há outra instalação ou versão antiga do PACE aberta. Reinicie o PACE desta pasta.")
        return capabilities

    def choose_import(self):
        self.import_files(filedialog.askopenfilenames(parent=self.root, title="Importar áudio ou vídeo",
            filetypes=[("Áudio e vídeo", "*.wav *.mp3 *.m4a *.ogg *.flac *.mp4 *.mkv *.webm *.opus *.aac"), ("Todos", "*.*")]))

    def import_files(self, paths):
        if not paths:
            return
        self.refresh_library_location()
        try:
            if not self.ensure_owner().get("import_media"):
                raise RuntimeError("A versão do PACE aberta não permite importar arquivos.")
            response = request({"command": IMPORT_MEDIA, "paths": [str(Path(value).resolve()) for value in paths]})
            if not response.get("ok"):
                raise RuntimeError(response.get("error", "Não foi possível importar os arquivos."))
            self.refresh_library_location()
        except Exception as exc:
            messagebox.showerror("PACE", str(exc), parent=self.root)
            return
        imported = response.get("ids", [None])[-1]
        self.refresh_list()
        if imported and self.tree.exists(imported):
            self.tree.selection_set(imported)

    def refresh_list(self):
        query = self.search.get().casefold().strip()
        rows = [r for r in self.store.list() if query in r["title"].casefold()]
        signature = [(r["id"], r["title"], r["status"]) for r in rows]
        if signature == self._signature:
            return
        self._signature = signature
        visible_ids = {r["id"] for r in rows}
        if self.selected and self.selected not in visible_ids:
            self.clear_selection()
        self.tree.delete(*self.tree.get_children())
        for row in rows:
            date = datetime.fromisoformat(row["created_at"]).astimezone().strftime("%d/%m/%Y %H:%M")
            self.tree.insert("", "end", iid=row["id"], text=f"{row['title']}\n{date}", values=(STATUS.get(row["status"], row["status"]),))
        if self.selected and self.tree.exists(self.selected):
            self.tree.selection_set(self.selected)
        elif not self.selected and rows:
            self.tree.selection_set(rows[0]["id"])

    def clear_selection(self):
        self.player.close()
        self.selected = None
        self._detail_signature = None
        self.result = {}
        self.title.delete(0, "end")
        self.meta.configure(text="Selecione uma gravação.")
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")

    def select(self, _=None):
        selection = self.tree.selection()
        if selection and selection[0] != self.selected:
            self.player.close()
            self.selected = selection[0]
            self._detail_signature = None
        self.refresh_detail()

    def refresh_detail(self):
        row = self.row()
        if not row:
            return
        self.meta.configure(text=f"{STATUS.get(row['status'], row['status'])} · {timestamp(row.get('duration', 0))} · "
            f"{round(float(row.get('progress', 0)) * 100) if float(row.get('progress', 0)) <= 1 else round(float(row.get('progress', 0)))}%\n"
            f"{row.get('error') or row.get('backend') or ''}\n{row.get('fallback_reason') or row.get('transcription_warning') or ''}")
        result_path = Path(row.get("result_path") or row.get("partial_result_path") or self.store.directory(row["id"]) / "result.json")
        signature = (row["id"], row["title"], result_path.stat().st_mtime_ns if result_path.exists() else 0, str(row.get("speaker_names")))
        if signature != self._detail_signature:
            self._detail_signature = signature
            if self.root.focus_get() != self.title._entry:
                self.title.delete(0, "end")
                self.title.insert(0, row["title"])
            try:
                self.result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
            except (OSError, ValueError):
                self.result = {}
            self.text.configure(state="normal")
            self.text.delete("1.0", "end")
            self.text.insert("1.0", transcript_text(self.result, row.get("speaker_names")) or "A transcrição aparecerá após o processamento. O áudio permanece salvo se houver falha.")
            self.text.configure(state="disabled")
        audio = Path(row.get("audio_path") or self.store.directory(row["id"]) / "audio.wav")
        if audio.exists() and self.player.path != audio and row["status"] not in {"recording", "paused", "finalizing"}:
            try:
                self.player.load(audio)
                self.seek.configure(to=max(1, self.player.duration))
            except Exception as exc:
                self.notice.configure(text=f"Não foi possível abrir o áudio: {exc}")

    def refresh(self):
        if self._closing:
            return
        try:
            self.refresh_library_location()
            self.refresh_list()
            self.refresh_detail()
            state = read_runtime(self.store_root)
            active = state.get("active", False)
            finalizing = state.get("finalizing", False)
            self.record_button.configure(text="Salvando áudio…" if finalizing else ("Encerrar reunião" if active else "Gravar reunião"),
                                         state="disabled" if finalizing else "normal")
            self.pause_button.configure(text="Retomar" if state.get("paused") else "Pausar", state="normal" if active else "disabled")
            self.play_button.configure(text="Pausar áudio" if self.player.playing else "Reproduzir", state="disabled" if active or not self.player.path else "normal")
            if active and self.player.playing:
                self.player.toggle()
            self.seek.set(self.player.position)
            self.clock.configure(text=f"{timestamp(self.player.position)} / {timestamp(self.player.duration)}")
        except Exception as exc:
            self.notice.configure(text=f"Não foi possível atualizar a biblioteca: {exc}")
        self.root.after(600, self.refresh)

    def refresh_library_location(self):
        config = AppConfig.load(config_path(self.project_root))
        root = recordings_dir(config).resolve()
        if root != self.store_root.resolve():
            self.clear_selection()
            self.config = config
            self.store_root = root
            self.store = RecordingStore(root)
            self._signature = None

    def play(self):
        try:
            self.player.toggle()
        except Exception as exc:
            messagebox.showerror("Áudio", str(exc), parent=self.root)

    def find(self):
        self.text.tag_remove("found", "1.0", "end")
        needle = self.find_entry.get()
        if not needle:
            return
        start, first = "1.0", None
        while True:
            hit = self.text.search(needle, start, stopindex="end", nocase=True)
            if not hit:
                break
            end = f"{hit}+{len(needle)}c"
            self.text.tag_add("found", hit, end)
            first = first or hit
            start = end
        if first:
            self.text.see(first)

    def rename(self):
        row = self.row()
        value = self.title.get().strip()
        if row and value:
            self.store.update(row["id"], title=value)
            self.refresh_list()

    def rename_speakers(self):
        row = self.row()
        if not row:
            return
        names = dict(row.get("speaker_names") or {})
        for speaker in sorted({s.get("speaker") for s in self.result.get("segments", []) if s.get("speaker")}):
            value = simpledialog.askstring("Nome do falante", speaker_label(speaker), initialvalue=names.get(speaker, speaker_label(speaker)), parent=self.root)
            if value is None:
                break
            names[speaker] = value.strip() or speaker_label(speaker)
        self.store.update(row["id"], speaker_names=names)
        self.refresh_detail()

    def copy(self):
        row = self.row()
        if row and self.result:
            self.root.clipboard_clear()
            self.root.clipboard_append(transcript_text(self.result, row.get("speaker_names")))

    def destination(self, extension, filetypes):
        row = self.row()
        if not row:
            return None
        value = filedialog.asksaveasfilename(parent=self.root, defaultextension=extension, filetypes=filetypes,
                                            initialfile="transcricao" + extension if extension != ".wav" else "gravacao.wav")
        if not value:
            return None
        destination = Path(value).resolve()
        if destination.is_relative_to(self.store_root.resolve()):
            messagebox.showerror("Exportar", "Escolha uma pasta fora da biblioteca para preservar os arquivos originais.", parent=self.root)
            return None
        return destination

    def export_text(self):
        row = self.row()
        if not row or not self.result:
            return
        destination = self.destination(".txt", [("Texto", "*.txt"), ("Legenda", "*.srt"), ("JSON", "*.json")])
        if destination:
            try:
                payload = json.dumps({**self.result, "speaker_names": row.get("speaker_names", {})}, ensure_ascii=False, indent=2) if destination.suffix.lower() == ".json" else transcript_text(self.result, row.get("speaker_names"), destination.suffix.lower() == ".srt")
                destination.write_text(payload, encoding="utf-8")
            except Exception as exc:
                messagebox.showerror("Exportar", str(exc), parent=self.root)

    def export_audio(self):
        if not self.player.path:
            return
        destination = self.destination(".wav", [("Áudio WAV", "*.wav")])
        if destination:
            try:
                shutil.copyfile(self.player.path, destination)
            except Exception as exc:
                messagebox.showerror("Exportar", str(exc), parent=self.root)

    def retry(self):
        row = self.row()
        if not row or row["status"] in BUSY:
            return
        directory = self.store.directory(row["id"])
        if row["status"] == "interrupted" and not (directory / "audio.wav").exists():
            try:
                from whisper_dictation.meeting_audio import recover_recording
                path = recover_recording(directory)
                self.store.update(row["id"], audio_path=str(path))
            except Exception as exc:
                messagebox.showerror("Recuperar áudio", str(exc), parent=self.root)
                return
        (directory / "cancel.request").unlink(missing_ok=True)
        self.store.transition(row["id"], {"failed", "interrupted", "cancelled", "ready"}, "queued", error="", progress=0)
        self.refresh_list()

    def cancel(self):
        row = self.row()
        if not row or row["status"] not in BUSY - {"new", "starting", "recording", "paused", "finalizing"}:
            return
        (self.store.directory(row["id"]) / "cancel.request").touch()
        if row["status"] == "queued":
            self.store.transition(row["id"], "queued", "cancelled", error="Processamento cancelado.")

    def delete(self):
        row = self.row()
        if not row:
            return
        if row["status"] in BUSY:
            messagebox.showinfo("Excluir", "Encerre a gravação ou cancele a tarefa antes de excluir.", parent=self.root)
            return
        if not messagebox.askyesno("Excluir gravação", "Excluir o áudio e a transcrição desta gravação? O arquivo importado original será preservado.", parent=self.root):
            return
        try:
            self.player.close()
            self.store.delete(row["id"])
            self.selected = None
            self._detail_signature = None
            self.result = {}
            self.text.configure(state="normal")
            self.text.delete("1.0", "end")
            self.text.configure(state="disabled")
            self.refresh_list()
        except Exception as exc:
            messagebox.showerror("Excluir", str(exc), parent=self.root)

    def close(self):
        self._closing = True
        self.player.close()
        close_window(self.root)


def run_recordings_window(project_root: Path):
    RecordingsWindow(project_root).root.mainloop()


def run_meeting_overlay(project_root: Path):
    config = AppConfig.load(config_path(project_root))
    root_dir = recordings_dir(config)
    root = ctk.CTk()
    root.withdraw()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.configure(fg_color="#121520")
    width, height = 340, 90
    root.geometry(f"{width}x{height}+{root.winfo_screenwidth()-width-24}+{root.winfo_screenheight()-height-76}")
    label = ctk.CTkLabel(root, text="PACE · Gravando", font=ctk.CTkFont(size=13, weight="bold"))
    label.pack(pady=(8, 0))
    controls = ctk.CTkFrame(root, fg_color="transparent")
    controls.pack(pady=5)
    pause = ctk.CTkButton(controls, text="Pausar", width=80, command=lambda: send_command(MEETING_PAUSE))
    pause.pack(side="left", padx=3)
    ctk.CTkButton(controls, text="Encerrar", width=80, command=lambda: send_command(MEETING_TOGGLE)).pack(side="left", padx=3)
    ctk.CTkButton(controls, text="Ocultar", width=70, command=root.destroy).pack(side="left", padx=3)
    root.update_idletasks()
    if os.name == "nt":
        import ctypes
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id()) or root.winfo_id()
        style = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
        ctypes.windll.user32.SetWindowLongW(hwnd, -20, style | 0x08000000 | 0x00000080)
    root.deiconify()

    def tick():
        state = read_runtime(root_dir)
        if not state.get("active"):
            root.destroy()
            return
        levels = state.get("levels", [0, 0])
        label.configure(text=f"{'Pausada' if state.get('paused') else '● Gravando'} · {timestamp(state.get('duration', 0))} · Mic {int(levels[0]*100)}% / PC {int(levels[1]*100)}%")
        pause.configure(text="Retomar" if state.get("paused") else "Pausar")
        root.after(500, tick)
    root.after(100, tick)
    root.mainloop()
