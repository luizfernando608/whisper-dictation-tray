"""Library shared by captured meetings and imported audio/video."""
from __future__ import annotations

import json
import os
import shutil
import time
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog

import customtkinter as ctk

from whisper_dictation.playback import WavePlayer, timestamp, transcript_text, speaker_label
from whisper_dictation.recording_store import RecordingStore
from whisper_dictation.settings import AppConfig, config_path, recordings_dir
from whisper_dictation.settings_window import COLOR_BG_ROOT, COLOR_TEXT_MAIN, _find_asset
from whisper_dictation.single_instance import MEETING_PAUSE, MEETING_TOGGLE, OPEN_SETTINGS, CAPABILITIES, IMPORT_MEDIA, request, send_command
from whisper_dictation.ui_utils import close_window, fit_window, window_work_area

STATUS = {"new": "Preparando gravação", "starting": "Iniciando gravação", "queued": "Na fila", "recording": "Gravando", "paused": "Pausada", "finalizing": "Salvando áudio",
          "processing": "Preparando", "importing": "Importando", "transcribing": "Transcrevendo",
          "diarizing": "Identificando falantes", "ready": "Pronta", "failed": "Falha",
          "cancelled": "Cancelada", "cancelling": "Cancelando", "interrupted": "Interrompida"}
BUSY = {"new", "starting", "queued", "recording", "paused", "finalizing", "processing", "importing", "transcribing", "diarizing", "cancelling"}

# A restrained extension of the PACE palette. Blue is reserved for the primary
# action; surfaces and secondary controls stay neutral.
SURFACE = "#11141C"
LINE = "#242A36"
HOVER = "#202633"
ACCENT = "#3978F6"
MUTED = "#98A2B5"


def short_time(seconds):
    value = timestamp(seconds)
    return value[3:] if value.startswith("00:") else value


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
        self._menu = None
        self._menus = {}
        self._title_value = ""
        self._compact = None
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
            for target in (self.root, self.library):
                target.drop_target_register(DND_FILES)
                target.dnd_bind("<<Drop>>", lambda event: self.import_files(self.root.tk.splitlist(event.data)))
        except Exception as exc:
            self.set_notice("Arrastar arquivos indisponível. Use o botão Importar.")
        self.root.after(100, self.refresh)

    def _build(self):
        self.root.grid_columnconfigure(1, weight=1)
        self.root.grid_rowconfigure(0, weight=1)
        sidebar = ctk.CTkFrame(self.root, width=288, corner_radius=0, fg_color=COLOR_BG_ROOT)
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_propagate(False)
        sidebar.grid_columnconfigure(0, weight=1)
        sidebar.grid_rowconfigure(3, weight=1)
        brand = ctk.CTkFrame(sidebar, fg_color="transparent")
        brand.grid(row=0, column=0, sticky="ew", padx=24, pady=(28, 26))
        ctk.CTkLabel(brand, text="PACE", font=ctk.CTkFont(size=23, weight="bold"), text_color=COLOR_TEXT_MAIN).pack(side="left")
        ctk.CTkLabel(brand, text="/  BIBLIOTECA", font=ctk.CTkFont(size=10, weight="bold"), text_color=MUTED).pack(side="left", padx=(12, 0), pady=(5, 0))
        self.search = ctk.CTkEntry(sidebar, placeholder_text="Buscar gravação", height=36, corner_radius=8,
                                 fg_color=SURFACE, border_width=1, border_color=LINE, font=ctk.CTkFont(size=13))
        self.search.grid(row=1, column=0, sticky="ew", padx=20)
        self.search.bind("<KeyRelease>", lambda _: self.refresh_list())
        self.search.bind("<Down>", lambda _: self.move_selection(1))
        self.count = ctk.CTkLabel(sidebar, text="GRAVAÇÕES", anchor="w", text_color=MUTED, font=ctk.CTkFont(size=10, weight="bold"))
        self.count.grid(row=2, column=0, sticky="ew", padx=24, pady=(26, 12))
        self.library = ctk.CTkScrollableFrame(sidebar, fg_color="transparent", corner_radius=0,
                                             scrollbar_button_color=LINE, scrollbar_button_hover_color=HOVER)
        self.library.grid(row=3, column=0, sticky="nsew", padx=(12, 4))
        self.library.grid_columnconfigure(0, weight=1)
        self.library._parent_canvas.configure(yscrollcommand=self.library_scroll)
        self.list_rows = {}
        self.list_empty = ctk.CTkLabel(self.library, text="", text_color=MUTED, justify="left", wraplength=220)
        footer = ctk.CTkFrame(sidebar, fg_color="transparent")
        footer.grid(row=4, column=0, sticky="ew", padx=20, pady=(16, 20))
        ctk.CTkLabel(footer, text="Arraste áudio ou vídeo\npara importar", anchor="w", justify="left", text_color="#758097", font=ctk.CTkFont(size=11)).pack(anchor="w", padx=4, pady=(0, 12))
        self.button(footer, "Configurações", lambda: send_command(OPEN_SETTINGS), width=150, anchor="w").pack(anchor="w")

        self.main = ctk.CTkFrame(self.root, corner_radius=0, fg_color=SURFACE)
        self.main.grid(row=0, column=1, sticky="nsew")
        top = ctk.CTkFrame(self.main, fg_color="transparent")
        self.top = top
        top.pack(fill="x", padx=32, pady=(26, 24))
        ctk.CTkLabel(top, text="Gravações", font=ctk.CTkFont(size=20, weight="bold")).pack(side="left")
        self.record_button = self.button(top, "Nova gravação", lambda: self.control(MEETING_TOGGLE), primary=True, width=138)
        self.record_button.pack(side="right")
        self.import_button = self.button(top, "Importar", self.choose_import, width=84)
        self.import_button.pack(side="right", padx=(12, 10))
        ctk.CTkFrame(self.main, height=1, fg_color=LINE).pack(fill="x")
        self.capture_bar = ctk.CTkFrame(self.main, fg_color="#192337", corner_radius=8)
        self.capture_label = ctk.CTkLabel(self.capture_bar, text="", text_color="#D4E2FF", anchor="w", font=ctk.CTkFont(size=12))
        self.capture_label.pack(side="left", padx=14, pady=8)
        self.pause_button = self.button(self.capture_bar, "Pausar", lambda: self.control(MEETING_PAUSE), width=80)
        self.pause_button.pack(side="right", padx=8, pady=6)
        self.notice = ctk.CTkLabel(self.main, text="", text_color="#F2BD84", anchor="w", justify="left", wraplength=460, font=ctk.CTkFont(size=12))

        self.body = ctk.CTkFrame(self.main, fg_color="transparent")
        self.body.pack(fill="both", expand=True, padx=32, pady=(26, 24))
        self.empty_state = ctk.CTkFrame(self.body, fg_color="transparent")
        self.empty_state.place(relx=.5, rely=.43, anchor="center")
        self.empty_title = ctk.CTkLabel(self.empty_state, text="Sua biblioteca está vazia", font=ctk.CTkFont(size=23, weight="bold"))
        self.empty_title.pack(pady=(0, 12))
        self.empty_hint = ctk.CTkLabel(self.empty_state, text="Grave uma reunião ou importe um arquivo.\nO áudio e a transcrição ficam juntos aqui.", text_color=MUTED, font=ctk.CTkFont(size=14), justify="center")
        self.empty_hint.pack()
        self.detail = ctk.CTkFrame(self.body, fg_color="transparent")
        self.title = ctk.CTkLabel(self.detail, text="", font=ctk.CTkFont(size=27, weight="bold"), anchor="w", justify="left", wraplength=640)
        self.title.pack(fill="x")
        self.meta = ctk.CTkLabel(self.detail, text="", anchor="w", text_color=MUTED, font=ctk.CTkFont(size=12))
        self.meta.pack(fill="x", pady=(6, 24))
        audio_row = ctk.CTkFrame(self.detail, fg_color="#1A1F2B", corner_radius=10)
        self.audio_row = audio_row
        audio_row.pack(fill="x", pady=(0, 28))
        self.play_button = self.button(audio_row, "▶", self.play, width=40, height=40, fg_color="#2A3344", font=ctk.CTkFont(size=16))
        self.play_button.pack(side="left", padx=(14, 12), pady=16)
        self.clock = ctk.CTkLabel(audio_row, text="00:00 / 00:00", text_color=MUTED, width=108, font=ctk.CTkFont(size=11))
        self.clock.pack(side="right", padx=(8, 14))
        self.seek = ctk.CTkSlider(audio_row, from_=0, to=1, command=self.player.seek, height=14,
                                fg_color="#394152", progress_color="#819BCB", button_color="#DDE6F6", button_hover_color="white", button_length=10)
        self.seek.set(0)
        self.seek.pack(side="left", fill="x", expand=True)
        toolbar = ctk.CTkFrame(self.detail, fg_color="transparent")
        self.toolbar = toolbar
        toolbar.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(toolbar, text="Transcrição", font=ctk.CTkFont(size=16, weight="bold")).pack(side="left")
        self.more_button = self.button(toolbar, "•••", lambda: self.open_menu("more"), width=36)
        self.more_button.pack(side="right")
        self.export_button = self.button(toolbar, "Exportar ↓", lambda: self.open_menu("export"), width=94)
        self.export_button.pack(side="right", padx=4)
        self.copy_button = self.button(toolbar, "Copiar", self.copy, width=64)
        self.copy_button.pack(side="right")
        self.find_row = ctk.CTkFrame(self.detail, fg_color="transparent")
        self.find_entry = ctk.CTkEntry(self.find_row, placeholder_text="Buscar na transcrição · Enter para localizar", height=34,
                                     fg_color=SURFACE, border_color=LINE, font=ctk.CTkFont(size=12))
        self.find_entry.pack(side="left", fill="x", expand=True)
        self.find_entry.bind("<Return>", lambda _: self.find())
        self.find_entry.bind("<Escape>", lambda _: self.toggle_find(False))
        self.button(self.find_row, "Fechar", lambda: self.toggle_find(False), width=60).pack(side="right", padx=(8, 0))
        self.document = ctk.CTkTextbox(self.detail, fg_color="transparent", corner_radius=0, border_width=0,
                                       font=ctk.CTkFont(size=15), text_color="#DEE3ED", wrap="word",
                                       scrollbar_button_color=LINE, scrollbar_button_hover_color=HOVER, activate_scrollbars=True)
        self.document.pack(fill="both", expand=True)
        self.text = self.document._textbox
        self.text.configure(state="disabled", spacing1=0, spacing3=8, padx=0, pady=8, cursor="arrow", takefocus=True)
        self.text.tag_configure("found", background="#304F86", foreground="white")
        self.text.tag_configure("speaker", foreground="#E4EAF6", spacing1=12, spacing3=7)
        self.text.tag_configure("time", foreground="#78869E")
        self.text.tag_configure("muted", foreground=MUTED)
        self.main.bind("<Configure>", self.resize_detail)
        self.root.bind("<Control-f>", lambda _: self.toggle_find(True))

    def button(self, parent, text, command, primary=False, **kwargs):
        options = dict(height=34, corner_radius=7, border_width=0, font=ctk.CTkFont(size=12),
                       fg_color=ACCENT if primary else "transparent", hover_color="#2864D6" if primary else HOVER,
                       text_color="white" if primary else "#BEC8DA", text_color_disabled="#586379")
        options.update(kwargs)
        button = ctk.CTkButton(parent, text=text, command=command, **options)
        button._canvas.configure(takefocus=True)
        button.bind("<Return>", lambda _: button.invoke())
        button.bind("<space>", lambda _: button.invoke())
        button.bind("<FocusIn>", lambda _: button.configure(border_width=1, border_color="#7E9BCB"))
        button.bind("<FocusOut>", lambda _: button.configure(border_width=0))
        return button

    def resize_detail(self, _=None):
        scale = self.main._get_widget_scaling()
        compact = self.main.winfo_height() / scale < 660
        if compact != self._compact:
            self._compact = compact
            # CTk caches pack arguments for DPI changes; pack_configure alone
            # would be overwritten the next time the monitor scale changes.
            self.top.pack(fill="x", padx=32, pady=(22, 16) if compact else (26, 24))
            self.body.pack(fill="both", expand=True, padx=32, pady=(18, 18) if compact else (26, 24))
            self.meta.pack(fill="x", pady=(4, 14) if compact else (6, 24))
            self.audio_row.pack(fill="x", pady=(0, 18) if compact else (0, 28))
            self.play_button.pack(side="left", padx=(14, 12), pady=10 if compact else 16)
            self.toolbar.pack(fill="x", pady=(0, 6) if compact else (0, 12))
            self.title.cget("font").configure(size=24 if compact else 27)
        width = max(200, int(self.main.winfo_width() / scale) - 64)
        # Titles are user supplied. Keep them to two measured lines so actions
        # and the document remain available even at the minimum window size.
        remaining = " ".join(self._title_value.split())
        lines = []
        font = self.title.cget("font")
        for line in range(2):
            if font.measure(remaining) <= width:
                lines.append(remaining)
                break
            cut = len(remaining)
            suffix = "…" if line else ""
            while cut and font.measure(remaining[:cut] + suffix) > width:
                cut -= 1
            boundary = remaining.rfind(" ", 0, cut)
            if not line and boundary > 0:
                cut = boundary
            lines.append(remaining[:cut].rstrip() + suffix)
            remaining = remaining[cut:].lstrip()
        self.title.configure(text="\n".join(lines), wraplength=0)
        self.notice.configure(wraplength=width)
        self.empty_hint.configure(wraplength=width)
        self.empty_title.configure(wraplength=width)
        self.text.tag_configure("speaker", font=("Segoe UI", -round(13 * scale), "bold"))
        self.text.tag_configure("time", font=("Segoe UI", -round(11 * scale)))

    def set_notice(self, message):
        self.notice.configure(text=message)
        self.notice.pack(fill="x", padx=32, pady=(12, 0), before=self.body)

    def toggle_find(self, visible=True):
        if visible and self.selected:
            self.find_row.pack(fill="x", pady=(0, 10), before=self.document)
            self.find_entry.focus_set()
        else:
            self.find_row.pack_forget()
            self.text.tag_remove("found", "1.0", "end")

    def menu_actions(self, kind):
        row = self.row()
        if not row:
            return []
        if kind == "export":
            return [("Transcrição · TXT, SRT ou JSON", self.export_text, bool(self.result)),
                    ("Áudio · WAV", self.export_audio, bool(self.player.path))]
        status = row["status"]
        return [("Buscar na transcrição    Ctrl+F", lambda: self.toggle_find(True), self.has_transcript()),
                ("Nomear falantes", self.rename_speakers, any(s.get("speaker") for s in self.result.get("segments", []))),
                ("Renomear gravação", self.rename, True),
                ("Processar novamente", self.retry, status not in BUSY),
                ("Cancelar processamento", self.cancel, status in BUSY - {"new", "starting", "recording", "paused", "finalizing"}),
                ("Detalhes do processamento", self.processing_details, True),
                ("Excluir gravação", self.delete, status not in BUSY)]

    def open_menu(self, kind):
        self.close_menu()
        actions = self.menu_actions(kind)
        if not actions:
            return
        button = self.export_button if kind == "export" else self.more_button
        if kind not in self._menus:
            menu = ctk.CTkToplevel(self.root)
            menu.withdraw()
            menu.overrideredirect(True)
            menu.transient(self.root)
            menu.configure(fg_color=LINE)
            surface = ctk.CTkFrame(menu, fg_color="#1B2130", corner_radius=8)
            surface.pack(fill="both", expand=True, padx=1, pady=1)
            buttons = []
            for label, _, _ in actions:
                if label == "Excluir gravação":
                    ctk.CTkFrame(surface, height=1, fg_color=LINE).pack(fill="x", padx=8, pady=4)
                item = self.button(surface, label, None, width=270, anchor="w",
                                   text_color="#F0A2A2" if label == "Excluir gravação" else "#D2DAE9")
                item.pack(fill="x", padx=6, pady=2)
                buttons.append(item)
            menu.bind("<FocusOut>", lambda _, owner=menu: owner.after_idle(lambda: self.dismiss_menu_if_unfocused(owner)))
            menu.bind("<Down>", lambda _: self.move_menu_focus(1))
            menu.bind("<Up>", lambda _: self.move_menu_focus(-1))
            self._menus[kind] = menu, buttons
        menu, buttons = self._menus[kind]
        self._menu, self._menu_buttons = menu, buttons
        for item, (_, command, enabled) in zip(buttons, actions):
            item.configure(command=lambda action=command: self.invoke_menu(action), state="normal" if enabled else "disabled")
            item._canvas.configure(takefocus=enabled)
        menu.update_idletasks()
        width, height = menu.winfo_reqwidth(), menu.winfo_reqheight()
        left, top, right, bottom = window_work_area(self.root)
        x = max(left, min(right - width, button.winfo_rootx() + button.winfo_width() - width))
        y = button.winfo_rooty() + button.winfo_height() + 4
        if y + height > bottom:
            y = max(top, button.winfo_rooty() - height - 4)
        scale = menu._get_window_scaling()
        menu.geometry(f"{round(width / scale)}x{round(height / scale)}+{x}+{y}")
        menu.bind("<Escape>", lambda _: self.close_menu(restore_focus=button))
        menu.deiconify()
        menu.lift()
        first = next((item for item in self._menu_buttons if item.cget("state") == "normal"), None)
        menu.after(50, lambda: first._canvas.focus_force() if first and self._menu == menu else None)

    def move_menu_focus(self, direction):
        enabled = [b for b in self._menu_buttons if b.cget("state") == "normal"]
        focus = self.root.focus_get()
        current = next((i for i, b in enumerate(enabled) if focus == b._canvas), -1)
        if enabled:
            enabled[(current + direction) % len(enabled)]._canvas.focus_set()
        return "break"

    def dismiss_menu_if_unfocused(self, owner):
        if self._menu and self._menu == owner:
            focus = self.root.focus_get()
            if focus is None or focus.winfo_toplevel() != self._menu:
                self.close_menu()

    def close_menu(self, restore_focus=None):
        if self._menu:
            menu, self._menu = self._menu, None
            # CTk buttons finish their click animation after the command. Keep
            # the two small menus alive so their pending callbacks remain valid.
            menu.withdraw()
        if restore_focus:
            self.root.focus_force()
            restore_focus._canvas.focus_set()

    def invoke_menu(self, action):
        self.close_menu()
        action()

    def processing_details(self):
        row = self.row()
        if row:
            details = [row["title"], STATUS.get(row["status"], row["status"])]
            details.extend(str(row[key]) for key in ("backend", "error", "fallback_reason", "transcription_warning") if row.get(key))
            messagebox.showinfo("Detalhes do processamento", "\n\n".join(details), parent=self.root)

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
        if imported in self.list_rows:
            self.select_recording(imported)

    def refresh_list(self):
        query = self.search.get().casefold().strip()
        rows = [r for r in self.store.list() if query in r["title"].casefold()]
        signature = (query, tuple((r["id"], r["title"], r["status"], round(r.get("duration", 0)) if r["status"] not in BUSY else 0) for r in rows))
        if signature == self._signature:
            return
        self._signature = signature
        focused = self.root.focus_get()
        restore_list_focus = any(focused == frame._canvas for frame in self.list_rows.values())
        scroll = self.library._parent_canvas.yview()[0]
        visible_ids = {r["id"] for r in rows}
        if self.selected and self.selected not in visible_ids:
            self.clear_selection()
        for frame in self.list_rows.values():
            frame.destroy()
        self.list_rows.clear()
        self.list_empty.grid_forget()
        self.count.configure(text=f"GRAVAÇÕES  /  {len(rows):02d}")
        for row in rows:
            date = datetime.fromisoformat(row["created_at"]).astimezone().strftime("%d %b · %H:%M")
            frame = ctk.CTkFrame(self.library, fg_color="transparent", corner_radius=8, border_width=1, border_color=COLOR_BG_ROOT)
            frame.grid(row=len(self.list_rows), column=0, sticky="ew", pady=(0, 6))
            title = row["title"]
            title = title if len(title) <= 31 else title[:30].rstrip() + "…"
            title_widget = ctk.CTkLabel(frame, text=title, anchor="w", font=ctk.CTkFont(size=13, weight="bold"), text_color="#D8DEEB")
            title_widget.pack(fill="x", padx=12, pady=(12, 2))
            caption = f"{date}   ·   {short_time(row.get('duration', 0))}"
            subtitle = ctk.CTkLabel(frame, text=caption, anchor="w", font=ctk.CTkFont(size=11), text_color="#8E9AB0")
            subtitle.pack(fill="x", padx=12, pady=(0, 3))
            color = "#7EB7A6" if row["status"] == "ready" else "#E2B184" if row["status"] in {"failed", "interrupted"} else "#9BB7EA"
            status = ctk.CTkLabel(frame, text=f"●  {STATUS.get(row['status'], row['status'])}", anchor="w", text_color=color, font=ctk.CTkFont(size=10))
            status.pack(fill="x", padx=12, pady=(0, 10))
            for widget in (frame, title_widget, subtitle, status):
                widget.configure(cursor="hand2")
                widget.bind("<Button-1>", lambda _, rid=row["id"]: self.select_recording(rid))
            # The underlying canvas participates in native Tab navigation.
            frame._canvas.configure(takefocus=True, highlightthickness=0)
            frame._canvas.bind("<Return>", lambda _, rid=row["id"]: self.select_recording(rid))
            frame._canvas.bind("<space>", lambda _, rid=row["id"]: self.select_recording(rid))
            frame._canvas.bind("<FocusIn>", lambda _, rid=row["id"]: self.select_recording(rid))
            frame._canvas.bind("<Down>", lambda _: self.move_selection(1))
            frame._canvas.bind("<Up>", lambda _: self.move_selection(-1))
            self.list_rows[row["id"]] = frame
        if self.selected in self.list_rows:
            self.highlight_selection()
        elif not self.selected and rows:
            self.select_recording(rows[0]["id"])
        else:
            self.clear_selection()
            self.list_empty.configure(text="Nenhuma gravação encontrada." if query else "Sua biblioteca começa aqui.")
            self.list_empty.grid(row=0, column=0, sticky="w", padx=12, pady=20)
            self.empty_hint.configure(text="Tente buscar por outro título." if query else "Grave uma reunião ou importe um arquivo.\nO áudio e a transcrição ficam juntos aqui.")
            self.empty_title.configure(text="Nenhum resultado" if query else "Sua biblioteca está vazia")
        if restore_list_focus and self.selected in self.list_rows:
            self.list_rows[self.selected]._canvas.focus_set()
        self.library._parent_canvas.yview_moveto(scroll)

    def highlight_selection(self):
        for rid, frame in self.list_rows.items():
            frame.configure(fg_color="#1B2537" if rid == self.selected else "transparent",
                            border_color="#2E405F" if rid == self.selected else COLOR_BG_ROOT)

    def library_scroll(self, first, last):
        self.library._scrollbar.set(first, last)
        overflow = float(last) - float(first) < .999
        self.library._scrollbar.configure(button_color=LINE if overflow else COLOR_BG_ROOT,
                                         button_hover_color=HOVER if overflow else COLOR_BG_ROOT)

    def move_selection(self, direction):
        ids = list(self.list_rows)
        if ids:
            index = ids.index(self.selected) if self.selected in ids else -1
            rid = ids[max(0, min(len(ids) - 1, index + direction))]
            self.select_recording(rid)
            self.list_rows[rid]._canvas.focus_set()
            # Keep keyboard selection visible even in a long library.
            self.root.update_idletasks()
            canvas = self.library._parent_canvas
            top, bottom = canvas.canvasy(0), canvas.canvasy(canvas.winfo_height())
            frame = self.list_rows[rid]
            y, height = frame.winfo_y(), frame.winfo_height()
            if y < top or y + height > bottom:
                total = max(1, self.library.winfo_height())
                canvas.yview_moveto(max(0, y if y < top else y + height - canvas.winfo_height()) / total)
        return "break"

    def clear_selection(self):
        self.close_menu()
        self.player.close()
        self.selected = None
        self._detail_signature = None
        self.result = {}
        self.title.configure(text="")
        self._title_value = ""
        self.meta.configure(text="")
        self.notice.pack_forget()
        self.seek.set(0)
        self.detail.pack_forget()
        self.empty_state.place(relx=.5, rely=.43, anchor="center")
        self.toggle_find(False)
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")

    def select_recording(self, recording_id):
        if recording_id not in self.list_rows:
            return
        if recording_id != self.selected:
            self.close_menu()
            self.player.close()
            self.selected = recording_id
            self._detail_signature = None
            self.find_entry.delete(0, "end")
            self.toggle_find(False)
        self.empty_state.place_forget()
        self.detail.pack(fill="both", expand=True)
        self.highlight_selection()
        self.refresh_detail()

    def refresh_detail(self):
        row = self.row()
        if not row:
            return
        date = datetime.fromisoformat(row["created_at"]).astimezone().strftime("%d/%m/%Y · %H:%M")
        origin = "Arquivo importado" if row["origin"] == "import" else "Reunião"
        self.meta.configure(text=f"{origin}   /   {date}   /   {short_time(row.get('duration', 0))}")
        result_path = Path(row.get("result_path") or row.get("partial_result_path") or self.store.directory(row["id"]) / "result.json")
        signature = (row["id"], row["title"], row["status"], result_path.stat().st_mtime_ns if result_path.exists() else 0, str(row.get("speaker_names")))
        if signature != self._detail_signature:
            self._detail_signature = signature
            self._title_value = row["title"]
            self.resize_detail()
            try:
                self.result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
            except (OSError, ValueError):
                self.result = {}
            self.text.configure(state="normal")
            self.text.delete("1.0", "end")
            names = row.get("speaker_names") or {}
            previous = None
            for index, segment in enumerate(self.result.get("segments", [])):
                speaker = segment.get("speaker")
                if not index or speaker != previous:
                    if index:
                        self.text.insert("end", "\n")
                    self.text.insert("end", names.get(speaker, speaker_label(speaker)), "speaker")
                    self.text.insert("end", f"    {short_time(segment.get('start', 0))}\n", "time")
                self.text.insert("end", segment.get("text", "").strip() + "\n")
                previous = speaker
            if not self.result.get("segments"):
                placeholder = "A transcrição aparecerá aqui quando estiver pronta."
                if row["status"] == "ready":
                    placeholder = "Nenhuma fala foi reconhecida neste áudio."
                if row["status"] in {"failed", "interrupted", "cancelled"}:
                    placeholder = "A transcrição não foi concluída.\nSeu áudio continua na biblioteca. Você pode processar novamente pelo menu •••."
                self.text.insert("end", self.result.get("text") or placeholder, "muted")
            self.text.configure(state="disabled")
            self.text.yview_moveto(0)
        audio = Path(row.get("audio_path") or self.store.directory(row["id"]) / "audio.wav")
        audio_error = None
        if audio.exists() and self.player.path != audio and row["status"] not in {"recording", "paused", "finalizing"}:
            try:
                self.player.load(audio)
                self.seek.configure(to=max(1, self.player.duration))
            except Exception as exc:
                audio_error = f"Não foi possível abrir o áudio: {exc}"
        warning = audio_error or row.get("error") or row.get("transcription_warning")
        if warning:
            summary = " ".join(str(warning).split())
            self.set_notice(summary if len(summary) <= 150 else summary[:147] + "… Veja os detalhes no menu •••.")
        elif row["status"] in BUSY - {"recording", "paused", "starting", "new", "finalizing"}:
            progress = float(row.get("progress", 0))
            self.set_notice(f"{STATUS.get(row['status'], row['status'])} · {round(progress * 100 if progress <= 1 else progress)}%")
        else:
            self.notice.pack_forget()
        self.copy_button.configure(state="normal" if self.has_transcript() else "disabled")
        self.export_button.configure(state="normal" if self.result or self.player.path else "disabled")
        self.seek.configure(state="normal" if self.player.path else "disabled")

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
            self.record_button.configure(text="Salvando…" if finalizing else ("Encerrar" if active else "Nova gravação"),
                                         state="disabled" if finalizing else "normal", fg_color="#9D3F4F" if active else ACCENT)
            self.pause_button.configure(text="Retomar" if state.get("paused") else "Pausar", state="normal" if active else "disabled")
            if active or finalizing:
                self.capture_bar.pack(fill="x", padx=32, pady=(16, 0), before=self.body)
                self.capture_label.configure(text=f"{'Pausada' if state.get('paused') else '● Gravando' if active else 'Salvando áudio'}   ·   {short_time(state.get('duration', 0))}")
            else:
                self.capture_bar.pack_forget()
            self.play_button.configure(text="Ⅱ" if self.player.playing else "▶", state="disabled" if active or not self.player.path else "normal")
            if active and self.player.playing:
                self.player.toggle()
            self.seek.set(self.player.position)
            self.clock.configure(text=f"{short_time(self.player.position)} / {short_time(self.player.duration)}")
        except Exception as exc:
            self.set_notice(f"Não foi possível atualizar a biblioteca: {exc}")
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
        if not row:
            return
        value = simpledialog.askstring("Renomear gravação", "Título", initialvalue=row["title"], parent=self.root)
        if value and value.strip():
            self.store.update(row["id"], title=value.strip())
            self.refresh_list()
            self.refresh_detail()

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
        if row and self.has_transcript():
            self.root.clipboard_clear()
            self.root.clipboard_append(transcript_text(self.result, row.get("speaker_names")))

    def has_transcript(self):
        return bool(self.result.get("text", "").strip() or any(segment.get("text", "").strip() for segment in self.result.get("segments", [])))

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
            self.clear_selection()
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
