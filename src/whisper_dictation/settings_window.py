from __future__ import annotations

import ctypes
import json
import queue
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
import webbrowser
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from tkinter import messagebox, filedialog
from typing import Callable

import customtkinter as ctk
from PIL import Image

from whisper_dictation import secrets_store
from whisper_dictation._version import __version__
from whisper_dictation.audio import list_input_devices
from whisper_dictation.providers import CLOUD_PROVIDERS, PROVIDER_ORDER, PROVIDERS
from whisper_dictation.settings import AppConfig, data_dir, validate_hotkeys
from whisper_dictation.win32_hotkey import parse_hotkey

SaveCallback = Callable[[AppConfig], bool]

# --- PACE Dark Obsidian Design System ---
COLOR_BG_ROOT = "#0C0E14"          # Deep obsidian background
COLOR_BG_CARD = "#121520"          # Refined dark card
COLOR_BORDER = "#23283A"           # Crisp subtle boundary
COLOR_CONTROL_BG = "#171B28"       # Input / dropdown background
COLOR_CONTROL_HOVER = "#212738"    # Control hover state
COLOR_ACCENT = "#2563EB"           # Electric PACE Blue
COLOR_ACCENT_HOVER = "#1D4ED8"     # Electric PACE Blue hover
COLOR_TEXT_MAIN = "#F8FAFC"        # Off-white crisp text
COLOR_TEXT_MUTED = "#94A3B8"       # Slate muted label
COLOR_DANGER = "#EF4444"           # Red warning

LANGUAGE_OPTIONS = [
    ("Automático", "auto"),
    ("Português", "pt"),
    ("Inglês", "en"),
]

RECORD_LIMIT_OPTIONS = [
    ("Sem limite (manual pelo atalho)", 0),
    ("120 minutos (2 horas)", 7200),
    ("60 minutos (1 hora)", 3600),
    ("30 minutos", 1800),
    ("15 minutos", 900),
    ("10 minutos", 600),
    ("5 minutos", 300),
    ("2 minutos (original)", 120),
]

_DEFAULT_MIC = "Padrão do sistema"


def _find_asset(rel_path: str) -> Path | None:
    """Resolve asset paths reliably in development, PyInstaller, or installed environments."""
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).parent
        candidates.extend([
            exe_dir / rel_path,
            exe_dir / "_internal" / rel_path,
            Path(getattr(sys, "_MEIPASS", exe_dir)) / rel_path,
            Path(getattr(sys, "_MEIPASS", exe_dir)) / "_internal" / rel_path,
        ])
    curr = Path(__file__).resolve()
    for parent in [curr.parent, curr.parent.parent, curr.parent.parent.parent, curr.parent.parent.parent.parent]:
        candidates.append(parent / rel_path)
        candidates.append(parent / "_internal" / rel_path)
    for c in candidates:
        if c.exists():
            return c
    return None

# Maps tkinter modifier keysyms to the tokens understood by parse_hotkey().
_MOD_KEYSYMS = {
    "Control_L": "Ctrl",
    "Control_R": "Ctrl",
    "Shift_L": "Shift",
    "Shift_R": "Shift",
    "Alt_L": "Alt",
    "Alt_R": "Alt",
    "Super_L": "Win",
    "Super_R": "Win",
}
_MOD_ORDER = ("Ctrl", "Alt", "Shift", "Win")

# Maps tkinter keysyms of non-modifier keys to parse_hotkey() tokens.
_KEYSYM_MAP = {
    "Return": "enter",
    "Escape": "esc",
    "Tab": "tab",
    "space": "space",
    "Prior": "pageup",
    "Next": "pagedown",
    "Home": "home",
    "End": "end",
    "Insert": "insert",
    "Delete": "delete",
    "BackSpace": "backspace",
    "Up": "up",
    "Down": "down",
    "Left": "left",
    "Right": "right",
}


@dataclass
class SettingsContext:
    """Optional hooks the tray app injects so the "Sobre" tab can act on it."""

    version: str = __version__
    open_logs: Callable[[], None] | None = None
    open_project_folder: Callable[[], None] | None = None
    copy_last_transcript: Callable[[], None] | None = None
    has_last_transcript: Callable[[], bool] | None = None
    check_update: Callable[[], object | None] | None = None
    run_update: Callable[[object], None] | None = None
    can_save: Callable[[], bool] | None = None
    commit_settings: Callable[[AppConfig, dict[str, str]], bool] | None = None


@dataclass(frozen=True, slots=True)
class _MicrophoneOption:
    label: str
    value: int | str | None


def run_settings_window(
    config: AppConfig,
    on_save: SaveCallback,
    context: SettingsContext | None = None,
) -> None:
    """Run the settings window on the CURRENT thread (blocking).

    CustomTkinter is not thread-safe: its dropdown popups deadlock when the Tk
    loop runs off the main thread. The dedicated settings process therefore
    calls this so CTk owns a clean main thread.
    """
    _run_window(replace(config), on_save, context or SettingsContext())


def _run_window(config: AppConfig, on_save: SaveCallback, context: SettingsContext) -> None:
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("dark-blue")

    root = ctk.CTk()
    root.title("PACE — Configurações")
    root.resizable(False, False)
    root.configure(fg_color=COLOR_BG_ROOT)

    ico_path = _find_asset("assets/branding/pace.ico") or _find_asset("pace.ico")
    if ico_path:
        try:
            root.iconbitmap(str(ico_path))
        except Exception:
            pass

    # Windows 10/11 Immersive Dark Mode on titlebar & hardware icon registration
    try:
        root.update_idletasks()
        hwnd = root.winfo_id()
        user32 = ctypes.windll.user32
        parent_hwnd = user32.GetParent(hwnd)
        actual_hwnd = parent_hwnd if parent_hwnd else hwnd

        # DWMWA_USE_IMMERSIVE_DARK_MODE (20 on Win11 / modern Win10, 19 on older Win10)
        dwmapi = ctypes.windll.dwmapi
        dark_mode = ctypes.c_int(1)
        for attr in (20, 19):
            if dwmapi.DwmSetWindowAttribute(actual_hwnd, attr, ctypes.byref(dark_mode), 4) == 0:
                break

        if ico_path:
            IMAGE_ICON = 1
            LR_LOADFROMFILE = 0x00000010
            hicon_sm = user32.LoadImageW(0, str(ico_path), IMAGE_ICON, 16, 16, LR_LOADFROMFILE)
            hicon_lg = user32.LoadImageW(0, str(ico_path), IMAGE_ICON, 32, 32, LR_LOADFROMFILE)
            WM_SETICON = 0x0080
            if hicon_sm:
                user32.SendMessageW(actual_hwnd, WM_SETICON, 0, hicon_sm)
            if hicon_lg:
                user32.SendMessageW(actual_hwnd, WM_SETICON, 1, hicon_lg)
    except Exception:
        pass

    heading_font = ctk.CTkFont(size=16, weight="bold")
    hint_font = ctk.CTkFont(size=11)
    muted = COLOR_TEXT_MUTED

    # ----- shared per-provider state -----
    model_by_provider = {
        "groq": config.groq_model,
        "openai": config.openai_model,
        "gemini": config.gemini_model,
        "local": config.model_size,
    }
    key_by_provider = {p: (secrets_store.get_api_key(p) or "") for p in CLOUD_PROVIDERS}
    original_keys = dict(key_by_provider)
    state = {"provider": config.transcription_provider}

    provider_labels = [PROVIDERS[p].label for p in PROVIDER_ORDER]
    provider_by_label = {PROVIDERS[p].label: p for p in PROVIDER_ORDER}

    language_options, current_language = _build_language_options(config.language)
    language_by_label = {label: value for label, value in language_options}
    language_labels = [label for label, _ in language_options]

    mic_options, current_mic, mic_error = _build_microphone_options(config.input_device)
    mic_by_label = {option.label: option for option in mic_options}
    mic_labels = [option.label for option in mic_options]

    limit_options, current_limit = _build_record_limit_options(config.max_record_seconds)
    limit_by_label = {label: value for label, value in limit_options}
    limit_labels = [label for label, _ in limit_options]

    hotkey_var = tk.StringVar(value=config.hotkey)
    key_var = tk.StringVar()
    show_key_var = tk.BooleanVar(value=False)

    # Reusable control style dictionary
    menu_style = {
        "fg_color": COLOR_CONTROL_BG,
        "button_color": "#1F2436",
        "button_hover_color": COLOR_CONTROL_HOVER,
        "dropdown_fg_color": COLOR_BG_CARD,
        "dropdown_hover_color": "#1C2130",
        "dropdown_text_color": COLOR_TEXT_MAIN,
        "text_color": COLOR_TEXT_MAIN,
        "corner_radius": 6,
    }

    tabview = ctk.CTkTabview(
        root,
        width=610,
        height=430,
        anchor="w",
        fg_color=COLOR_BG_CARD,
        border_color=COLOR_BORDER,
        border_width=1,
        segmented_button_fg_color="#090B10",
        segmented_button_selected_color=COLOR_ACCENT,
        segmented_button_selected_hover_color=COLOR_ACCENT_HOVER,
        segmented_button_unselected_color="#141722",
        segmented_button_unselected_hover_color=COLOR_CONTROL_HOVER,
    )
    tabview.pack(padx=16, pady=(14, 0), fill="both", expand=True)
    tab_trans = tabview.add("Transcrição")
    tab_audio = tabview.add("Áudio")
    tab_hotkey = tabview.add("Atalho")
    tab_meeting = tabview.add("Reuniões")
    tab_about = tabview.add("Sobre")

    # =========================== TRANSCRIÇÃO ===========================
    tab_trans.grid_columnconfigure(1, weight=1)

    ctk.CTkLabel(tab_trans, text="Provedor", text_color=COLOR_TEXT_MAIN).grid(
        row=0, column=0, sticky="w", padx=(4, 14), pady=8
    )
    provider_menu = ctk.CTkOptionMenu(tab_trans, values=provider_labels, width=270, **menu_style)
    provider_menu.grid(row=0, column=1, sticky="ew", pady=8)

    ctk.CTkLabel(tab_trans, text="Modelo", text_color=COLOR_TEXT_MAIN).grid(
        row=1, column=0, sticky="w", padx=(4, 14), pady=8
    )
    model_menu = ctk.CTkComboBox(
        tab_trans,
        values=["—"],
        width=270,
        fg_color=COLOR_CONTROL_BG,
        border_color=COLOR_BORDER,
        border_width=1,
        button_color="#1F2436",
        button_hover_color=COLOR_CONTROL_HOVER,
        dropdown_fg_color=COLOR_BG_CARD,
        dropdown_hover_color="#1C2130",
        dropdown_text_color=COLOR_TEXT_MAIN,
        text_color=COLOR_TEXT_MAIN,
        corner_radius=6,
    )
    model_menu.grid(row=1, column=1, sticky="ew", pady=8)

    key_label = ctk.CTkLabel(tab_trans, text="Chave de API", text_color=COLOR_TEXT_MAIN)
    key_label.grid(row=2, column=0, sticky="w", padx=(4, 14), pady=(8, 2))
    key_frame = ctk.CTkFrame(tab_trans, fg_color="transparent")
    key_frame.grid(row=2, column=1, sticky="ew", pady=(8, 2))
    key_frame.grid_columnconfigure(0, weight=1)
    key_entry = ctk.CTkEntry(
        key_frame,
        textvariable=key_var,
        show="•",
        placeholder_text="cole sua chave aqui",
        fg_color=COLOR_CONTROL_BG,
        border_color=COLOR_BORDER,
        border_width=1,
        text_color=COLOR_TEXT_MAIN,
        corner_radius=6,
    )
    key_entry.grid(row=0, column=0, sticky="ew")
    ctk.CTkButton(
        key_frame,
        text="Colar",
        width=68,
        command=lambda: _paste_into(root, key_var),
        fg_color=COLOR_CONTROL_BG,
        hover_color=COLOR_CONTROL_HOVER,
        border_color=COLOR_BORDER,
        border_width=1,
        text_color=COLOR_TEXT_MAIN,
        corner_radius=6,
    ).grid(row=0, column=1, padx=(8, 0))

    def _toggle_show_key() -> None:
        key_entry.configure(show="" if show_key_var.get() else "•")

    key_switch = ctk.CTkCheckBox(
        tab_trans,
        text="Mostrar chave",
        variable=show_key_var,
        command=_toggle_show_key,
        fg_color=COLOR_ACCENT,
        hover_color=COLOR_ACCENT_HOVER,
        border_color=COLOR_BORDER,
        text_color=COLOR_TEXT_MAIN,
    )
    key_switch.grid(row=3, column=1, sticky="w", pady=(4, 0))
    key_hint = ctk.CTkLabel(tab_trans, text="", text_color=muted, font=hint_font)
    key_hint.grid(row=4, column=1, sticky="w", pady=(2, 4))

    ctk.CTkLabel(tab_trans, text="Idioma", text_color=COLOR_TEXT_MAIN).grid(
        row=5, column=0, sticky="w", padx=(4, 14), pady=8
    )
    language_menu = ctk.CTkOptionMenu(tab_trans, values=language_labels, width=270, **menu_style)
    language_menu.grid(row=5, column=1, sticky="ew", pady=8)
    language_menu.set(current_language)

    def _load_provider(provider: str) -> None:
        info = PROVIDERS[provider]
        models = list(info.models)
        current = model_by_provider[provider]
        model_menu.configure(values=models if current in models else [current, *models])
        model_menu.set(current)
        if info.needs_api_key:
            key_label.grid()
            key_frame.grid()
            key_switch.grid()
            key_hint.grid()
            key_var.set(key_by_provider.get(provider, ""))
            key_hint.configure(text=info.key_hint)
        else:
            key_label.grid_remove()
            key_frame.grid_remove()
            key_switch.grid_remove()
            key_hint.grid_remove()

    def _stash_current() -> None:
        current = state["provider"]
        model_by_provider[current] = model_menu.get()
        if current in CLOUD_PROVIDERS:
            key_by_provider[current] = key_var.get()

    def _on_provider_change(label: str) -> None:
        _stash_current()
        state["provider"] = provider_by_label[label]
        _load_provider(state["provider"])

    provider_menu.configure(command=_on_provider_change)
    provider_menu.set(PROVIDERS[state["provider"]].label)
    _load_provider(state["provider"])

    # ============================== ÁUDIO ==============================
    ctk.CTkLabel(tab_audio, text="Microfone de entrada", text_color=COLOR_TEXT_MAIN).pack(
        anchor="w", padx=4, pady=(8, 6)
    )
    mic_menu = ctk.CTkOptionMenu(
        tab_audio, values=mic_labels or [_DEFAULT_MIC], width=430, dynamic_resizing=False, **menu_style
    )
    mic_menu.pack(fill="x", padx=4)
    mic_menu.set(current_mic)
    if mic_error:
        ctk.CTkLabel(
            tab_audio, text=f"Falha ao listar microfones: {mic_error}", text_color=COLOR_DANGER
        ).pack(anchor="w", padx=4, pady=(6, 0))

    ctk.CTkLabel(tab_audio, text="Tempo limite de escuta / gravação", text_color=COLOR_TEXT_MAIN).pack(
        anchor="w", padx=4, pady=(16, 6)
    )
    limit_menu = ctk.CTkOptionMenu(
        tab_audio, values=limit_labels, width=430, dynamic_resizing=False, **menu_style
    )
    limit_menu.pack(fill="x", padx=4)
    limit_menu.set(current_limit)
    ctk.CTkLabel(
        tab_audio,
        text="Tempo contínuo que o microfone escuta antes de parar automaticamente.",
        text_color=muted,
        font=hint_font,
    ).pack(anchor="w", padx=4, pady=(6, 0))

    # ============================== ATALHO =============================
    ctk.CTkLabel(
        tab_hotkey, text="Atalho global para iniciar/parar a gravação", text_color=COLOR_TEXT_MAIN
    ).pack(anchor="w", padx=4, pady=(8, 8))
    hotkey_row = ctk.CTkFrame(tab_hotkey, fg_color="transparent")
    hotkey_row.pack(fill="x", padx=4)
    hotkey_entry = ctk.CTkEntry(
        hotkey_row,
        textvariable=hotkey_var,
        width=240,
        fg_color=COLOR_CONTROL_BG,
        border_color=COLOR_BORDER,
        border_width=1,
        text_color=COLOR_TEXT_MAIN,
        corner_radius=6,
    )
    hotkey_entry.pack(side="left")
    capture_btn = ctk.CTkButton(
        hotkey_row,
        text="Gravar atalho",
        width=140,
        fg_color=COLOR_CONTROL_BG,
        hover_color=COLOR_CONTROL_HOVER,
        border_color=COLOR_BORDER,
        border_width=1,
        text_color=COLOR_TEXT_MAIN,
        corner_radius=6,
    )
    capture_btn.pack(side="left", padx=(8, 0))
    hotkey_status = ctk.CTkLabel(tab_hotkey, text="Ex.: Ctrl+Shift+H", text_color=muted, font=hint_font)
    hotkey_status.pack(anchor="w", padx=4, pady=(8, 0))
    _wire_hotkey_capture(tab_hotkey, capture_btn, hotkey_var, hotkey_status)
    collect_meeting = _build_meeting_tab(tab_meeting, config, menu_style, root)

    # ============================== SOBRE =============================
    _build_about_tab(tab_about, context, heading_font, muted)

    # ============================== RODAPÉ ============================
    footer = ctk.CTkFrame(root, fg_color="transparent")
    footer.pack(fill="x", padx=16, pady=(10, 14))

    def save() -> None:
        if context.can_save and not context.can_save():
            messagebox.showerror("Configurações", "Conclua a reunião e a fila antes de salvar alterações.")
            return
        setup = getattr(root, "_model_setup", None)
        if setup and setup[0].poll() is None:
            messagebox.showinfo("Modelos", "Aguarde o preparo dos modelos antes de fechar ou salvar as configurações.")
            return
        _stash_current()

        hotkey = hotkey_var.get().strip()
        try:
            parse_hotkey(hotkey)
        except ValueError as exc:
            messagebox.showerror("Configurações", f"Atalho inválido: {exc}")
            return

        selected_mic = mic_by_label.get(mic_menu.get())
        if selected_mic is None:
            messagebox.showerror("Configurações", "Selecione um microfone válido.")
            return

        data = asdict(config)
        data["transcription_provider"] = state["provider"]
        data["language"] = language_by_label.get(language_menu.get(), config.language)
        data["groq_model"] = model_by_provider["groq"]
        data["openai_model"] = model_by_provider["openai"]
        data["gemini_model"] = model_by_provider["gemini"]
        data["model_size"] = model_by_provider["local"]
        data["input_device"] = selected_mic.value
        data["max_record_seconds"] = limit_by_label.get(limit_menu.get(), config.max_record_seconds)
        data["hotkey"] = hotkey
        try:
            data.update(collect_meeting())
            new_config = AppConfig(**AppConfig._sanitize(data))
            validate_hotkeys(new_config)
        except Exception as exc:
            messagebox.showerror("Configurações", str(exc))
            return

        if context.commit_settings:
            changes = {key: value for key, value in key_by_provider.items() if value != original_keys.get(key, "")}
            token_value = collect_meeting.token_value()
            if token_value != collect_meeting.token_original:
                changes["huggingface"] = token_value
            try:
                saved = context.commit_settings(new_config, changes)
            except Exception as exc:
                messagebox.showerror("Configurações", str(exc))
                return
        else:
            if any(key_by_provider[p] != original_keys[p] for p in original_keys) or collect_meeting.token_value() != collect_meeting.token_original:
                messagebox.showerror("Configurações", "Abra a versão atual do PACE na bandeja para salvar credenciais com segurança.")
                return
            saved = on_save(new_config)
        if saved:
            root.destroy()
        else:
            messagebox.showerror(
                "Configurações", "Não foi possível salvar agora. Veja os logs do aplicativo."
            )

    ctk.CTkButton(
        footer,
        text="Cancelar",
        width=110,
        command=root.destroy,
        fg_color="transparent",
        hover_color="#181C28",
        border_color=COLOR_BORDER,
        border_width=1,
        text_color=COLOR_TEXT_MUTED,
        corner_radius=7,
    ).pack(side="right")
    ctk.CTkButton(
        footer,
        text="Salvar",
        width=120,
        command=save,
        fg_color=COLOR_ACCENT,
        hover_color=COLOR_ACCENT_HOVER,
        text_color="#FFFFFF",
        font=ctk.CTkFont(size=12, weight="bold"),
        corner_radius=7,
    ).pack(side="right", padx=(0, 8))

    root.update_idletasks()
    _center(root)
    root.after(60, root.lift)
    root.after(80, root.focus_force)
    root.mainloop()


def _build_meeting_tab(tab, config, menu_style, root):
    from whisper_dictation.meeting_audio import list_meeting_devices
    from whisper_dictation.meeting_service import runtime_command
    frame = ctk.CTkScrollableFrame(tab, fg_color="transparent")
    frame.pack(fill="both", expand=True)
    fields = {}
    def entry(key, label, value, show=None):
        ctk.CTkLabel(frame, text=label, anchor="w").pack(fill="x", pady=(10, 4))
        widget = ctk.CTkEntry(frame, show=show or "")
        widget.insert(0, str(value))
        widget.pack(fill="x")
        fields[key] = widget
        return widget
    entry("meeting_hotkey", "Iniciar / encerrar reunião", config.meeting_hotkey)
    entry("meeting_pause_hotkey", "Pausar / retomar (vazio para desativar)", config.meeting_pause_hotkey)
    try:
        devices = list_meeting_devices()
    except Exception as exc:
        devices = {"inputs": [], "outputs": []}
        ctk.CTkLabel(frame, text=f"Dispositivos indisponíveis: {exc}", wraplength=530, text_color=COLOR_DANGER).pack(fill="x", pady=8)
    mappings = {}
    for key, label, kind in (("meeting_input_device", "Microfone da reunião", "inputs"), ("meeting_output_device", "Saída de áudio do computador", "outputs")):
        ctk.CTkLabel(frame, text=label, anchor="w").pack(fill="x", pady=(10, 4))
        choices = {"Padrão do sistema": None}
        for device in devices[kind]:
            choices[f"{device['name']} [{device['id']}]"] = str(device["id"])
        selected = getattr(config, key)
        current = next((name for name, value in choices.items() if value == selected), None)
        if current is None:
            current = f"Dispositivo salvo (indisponível): {selected}"
            choices[current] = selected
        widget = ctk.CTkOptionMenu(frame, values=list(choices), dynamic_resizing=False, **menu_style)
        widget.set(current)
        widget.pack(fill="x")
        fields[key], mappings[key] = widget, choices
    overlay = tk.BooleanVar(value=config.meeting_show_overlay)
    ctk.CTkCheckBox(frame, text="Mostrar cápsula discreta durante a reunião", variable=overlay).pack(anchor="w", pady=14)
    ctk.CTkLabel(frame, text="Reuniões são gravadas sem limite de tempo e salvas em disco.\nA saída selecionada inclui todos os sons reproduzidos nela.", justify="left", text_color=COLOR_TEXT_MUTED).pack(fill="x")
    folder = entry("meeting_storage_dir", "Pasta da biblioteca (vazio usa a pasta padrão do PACE)", config.meeting_storage_dir)
    def choose_folder():
        value = filedialog.askdirectory(parent=root)
        if value:
            folder.delete(0, "end")
            folder.insert(0, value)
    ctk.CTkButton(frame, text="Escolher pasta", width=130, command=choose_folder).pack(anchor="w", pady=5)
    ctk.CTkLabel(frame, text="Diarização local", font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w", pady=(16, 4))
    engines = {"Nemotron 3 — sem token (até 8 falantes)": "nemotron", "Community-1 — avançado (contagem orientada)": "community-1"}
    engine = ctk.CTkOptionMenu(frame, values=list(engines), **menu_style)
    engine.set(next(k for k, v in engines.items() if v == config.diarization_engine))
    engine.pack(fill="x", pady=4)
    backends = {"Automático (testar e escolher)": "auto", "CPU": "cpu", "GPU Vulkan": "vulkan", "NVIDIA CUDA": "cuda", "Intel OpenVINO": "openvino", "DirectML": "directml"}
    backend = ctk.CTkOptionMenu(frame, values=list(backends), **menu_style)
    backend.set(next(k for k, v in backends.items() if v == config.diarization_backend))
    backend.pack(fill="x", pady=4)
    entry("diarization_num_speakers", "Quantidade de falantes (0 = automática)", config.diarization_num_speakers)
    entry("diarization_min_speakers", "Mínimo de falantes (0 = automático)", config.diarization_min_speakers)
    entry("diarization_max_speakers", "Máximo de falantes (0 = automático)", config.diarization_max_speakers)
    entry("diarization_model_dir", "Pasta dos modelos (vazio usa cache padrão)", config.diarization_model_dir)
    ctk.CTkLabel(frame, text="Para modelos de transcrição sem timestamps, usar:", anchor="w").pack(fill="x", pady=(10, 4))
    timing = ctk.CTkOptionMenu(frame, values=["Whisper local", "OpenAI whisper-1"], **menu_style)
    timing.set("Whisper local" if config.meeting_timestamp_provider == "local" else "OpenAI whisper-1")
    timing.pack(fill="x")
    model_hint = ctk.CTkLabel(frame, text="", justify="left", wraplength=530, text_color=COLOR_TEXT_MUTED)
    model_hint.pack(fill="x", pady=(16, 5))
    ctk.CTkButton(frame, text="Abrir página do modelo", command=lambda: webbrowser.open("https://huggingface.co/nvidia/Nemotron-3-Diarization" if engines[engine.get()] == "nemotron" else "https://huggingface.co/pyannote/speaker-diarization-community-1")).pack(anchor="w", pady=5)
    token_original = secrets_store.get_api_key("huggingface") or ""
    token_frame = ctk.CTkFrame(frame, fg_color="transparent")
    token_frame.pack(fill="x")
    token_label = ctk.CTkLabel(token_frame, text="Token Hugging Face — somente Community-1", anchor="w")
    token_label.pack(fill="x", pady=(10, 4))
    token = ctk.CTkEntry(token_frame, show="•")
    token.insert(0, token_original)
    token.pack(fill="x")
    def select_engine(_=None):
        community = engines[engine.get()] == "community-1"
        allowed = {"auto", "cpu", "cuda", "openvino", "directml"} if community else {"auto", "cpu", "vulkan"}
        choices = [label for label, value in backends.items() if value in allowed]
        backend.configure(values=choices)
        if backend.get() not in choices:
            backend.set(choices[0])
        for key in ("diarization_num_speakers", "diarization_min_speakers", "diarization_max_speakers"):
            fields[key].configure(state="normal" if community else "disabled")
        token.configure(state="normal" if community else "disabled")
        if community:
            if not token_frame.winfo_manager():
                token_frame.pack(fill="x", before=setup_status)
        else:
            token_frame.pack_forget()
        token_label.configure(text_color=COLOR_TEXT_MAIN if community else COLOR_TEXT_MUTED)
        model_hint.configure(text=("Community-1: aceite as condições na página do modelo e configure\num token para o primeiro download. Depois, funciona offline." if community else "Nemotron 3: preparo sem conta ou token. Estima até 8 falantes\nautomaticamente; a contagem acima é usada apenas pelo Community-1.\nCPU e GPU Vulkan são medidas antes da seleção automática."))
    engine.configure(command=select_engine)
    select_engine()
    setup_status = ctk.CTkLabel(frame, text="", wraplength=530, justify="left", text_color=COLOR_TEXT_MUTED)
    setup_status.pack(fill="x", pady=5)
    prepare_button = ctk.CTkButton(frame, text="Preparar modelos")
    prepare_button.pack(anchor="w", pady=(4, 12))

    def collect():
        result = {key: widget.get().strip() for key, widget in fields.items() if key not in mappings and key != "hf_token"}
        for key, mapping in mappings.items():
            result[key] = mapping[fields[key].get()]
        result["diarization_backend"] = backends[backend.get()]
        result["diarization_engine"] = engines[engine.get()]
        result["meeting_timestamp_provider"] = "local" if timing.get() == "Whisper local" else "whisper-1"
        result["meeting_show_overlay"] = overlay.get()
        result["diarization_num_speakers"] = int(result["diarization_num_speakers"])
        if result["diarization_engine"] == "nemotron":
            for key in ("diarization_num_speakers", "diarization_min_speakers", "diarization_max_speakers"):
                result[key] = 0
        validated = AppConfig(**AppConfig._sanitize({**asdict(config), **result}))
        validate_hotkeys(validated)
        return result

    def prepare():
        try:
            setup = getattr(root, "_model_setup", None)
            if setup and setup[0].poll() is None:
                return
            updated = AppConfig(**AppConfig._sanitize({**asdict(config), **collect()}))
            if updated.diarization_engine == "community-1" and token.get().strip() != token_original:
                secrets_store.set_api_key("huggingface", token.get().strip())
            setup_dir = Path(tempfile.mkdtemp(prefix="setup-", dir=data_dir()))
            setup_config = setup_dir / "config.json"
            setup_progress = setup_dir / "progress.json"
            updated.save(setup_config)
            project_root = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2]
            with (setup_dir / "setup.log").open("ab") as log:
                from whisper_dictation.child_job import ChildJob
                job = ChildJob()
                proc = subprocess.Popen(runtime_command(project_root, "--prepare-models", "--config-path", str(setup_config), "--progress-file", str(setup_progress)),
                                        stdout=log, stderr=log, creationflags=0x08000000)
                try:
                    job.add(proc.pid)
                except Exception:
                    proc.terminate()
                    proc.wait(timeout=10)
                    job.close()
                    raise
                root._model_setup = (proc, job)
        except Exception as exc:
            setup_status.configure(text=f"Não foi possível preparar: {exc}", text_color=COLOR_DANGER)
            return
        prepare_button.configure(state="disabled")
        setup_status.configure(text="Preparando modelos…", text_color=COLOR_TEXT_MUTED)
        def poll():
            if setup_progress.exists():
                try:
                    setup_status.configure(text=json.loads(setup_progress.read_text(encoding="utf-8"))["message"])
                except (OSError, ValueError, KeyError):
                    pass
            if proc.poll() is None:
                root.after(500, poll)
            else:
                job.close()
                prepare_button.configure(state="normal")
                if proc.returncode:
                    setup_status.configure(text_color=COLOR_DANGER)
                    if not setup_progress.exists():
                        setup_status.configure(text=f"Preparo falhou. Veja {setup_dir / 'setup.log'}")
                else:
                    setup_status.configure(text="Modelos preparados. Pronto para identificar falantes.", text_color=COLOR_TEXT_MUTED)
        root.after(500, poll)
    prepare_button.configure(command=prepare)
    collect.token_value = lambda: token.get().strip()
    collect.token_original = token_original
    return collect


def _persist_keys(current: dict[str, str], original: dict[str, str]) -> None:
    for provider, value in current.items():
        if value != original.get(provider, ""):
            secrets_store.set_api_key(provider, value)


def _build_about_tab(tab, context: SettingsContext, heading_font, muted) -> None:
    brand_frame = ctk.CTkFrame(tab, fg_color="transparent")
    brand_frame.pack(anchor="w", padx=4, pady=(10, 16), fill="x")

    logo_path = _find_asset("assets/branding/pace_512.png")
    if logo_path and logo_path.exists():
        try:
            pil_img = Image.open(logo_path)
            ctk_img = ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=(56, 56))
            ctk.CTkLabel(brand_frame, image=ctk_img, text="").pack(side="left", padx=(0, 16))
        except Exception:
            pass

    text_frame = ctk.CTkFrame(brand_frame, fg_color="transparent")
    text_frame.pack(side="left", fill="both", expand=True)

    ctk.CTkLabel(
        text_frame, text="PACE", font=ctk.CTkFont(size=22, weight="bold"), text_color=COLOR_TEXT_MAIN
    ).pack(anchor="w", pady=(0, 2))
    ctk.CTkLabel(
        text_frame,
        text="The speed of thought",
        font=ctk.CTkFont(size=12, slant="italic"),
        text_color=COLOR_TEXT_MUTED,
    ).pack(anchor="w", pady=(0, 2))
    ctk.CTkLabel(text_frame, text=f"Versão {context.version}", text_color=muted).pack(
        anchor="w"
    )

    update_status = ctk.CTkLabel(tab, text="", text_color=COLOR_TEXT_MUTED)
    update_status.pack(anchor="w", padx=4)
    update_btn = ctk.CTkButton(
        tab,
        text="Verificar atualizações",
        width=190,
        fg_color=COLOR_CONTROL_BG,
        hover_color=COLOR_CONTROL_HOVER,
        border_color=COLOR_BORDER,
        border_width=1,
        text_color=COLOR_TEXT_MAIN,
        corner_radius=6,
    )
    update_btn.pack(anchor="w", padx=4, pady=(4, 14))
    _wire_update_button(tab, update_btn, update_status, context)

    tools = ctk.CTkFrame(tab, fg_color="transparent")
    tools.pack(anchor="w", padx=4, fill="x")
    secondary = {
        "fg_color": COLOR_CONTROL_BG,
        "hover_color": COLOR_CONTROL_HOVER,
        "border_color": COLOR_BORDER,
        "border_width": 1,
        "text_color": COLOR_TEXT_MAIN,
        "corner_radius": 6,
    }
    if context.open_logs:
        ctk.CTkButton(tools, text="Abrir logs", width=100, command=context.open_logs, **secondary).pack(
            side="left", padx=(0, 8)
        )
    if context.open_project_folder:
        ctk.CTkButton(
            tools, text="Abrir pasta", width=100, command=context.open_project_folder, **secondary
        ).pack(side="left", padx=(0, 8))
    if context.copy_last_transcript:
        ctk.CTkButton(
            tools, text="Copiar transcrição", width=150, command=context.copy_last_transcript, **secondary
        ).pack(side="left")


def _wire_update_button(parent, button, status, context: SettingsContext) -> None:
    if not context.check_update:
        button.configure(state="disabled")
        status.configure(text="Verificação de atualização indisponível.")
        return

    def _apply_update(update: object) -> None:
        if context.run_update:
            try:
                context.run_update(update)
                status.configure(text="Reinício solicitado. Conclua as gravações e a fila se estiverem ativas.")
            except Exception as exc:
                status.configure(text=str(exc))

    def do_check() -> None:
        button.configure(state="disabled")
        status.configure(text="Verificando e preparando atualização…")
        responses = queue.Queue()

        def worker() -> None:
            try:
                update = context.check_update()  # type: ignore[misc]
                error = False
            except Exception:
                update, error = None, True

            responses.put((update, error))

        def finish() -> None:
            try:
                update, error = responses.get_nowait()
            except queue.Empty:
                parent.after(100, finish)
            else:
                button.configure(state="normal")
                if error:
                    status.configure(text="Não foi possível verificar agora.")
                elif update:
                    version = getattr(update, "version", "?")
                    status.configure(text=f"Atualização pronta: {version}")
                    button.configure(text="Reiniciar para atualizar", command=lambda: _apply_update(update))
                else:
                    status.configure(text="Você já está na versão mais recente.")

        threading.Thread(target=worker, daemon=True).start()
        parent.after(100, finish)

    button.configure(command=do_check)


def _wire_hotkey_capture(parent, button, hotkey_var: tk.StringVar, status) -> None:
    held: set[str] = set()
    capturing = {"on": False}

    def start_capture() -> None:
        held.clear()
        capturing["on"] = True
        button.configure(text="Pressione as teclas…")
        status.configure(text="Segure Ctrl/Alt/Shift + uma tecla.")
        # Focus the window (not the button) so Space/Enter don't re-invoke it.
        parent.winfo_toplevel().focus_set()

    def stop_capture() -> None:
        capturing["on"] = False
        button.configure(text="Gravar atalho")

    def on_press(event: tk.Event) -> str | None:
        if not capturing["on"]:
            return None
        keysym = event.keysym
        if keysym in _MOD_KEYSYMS:
            held.add(_MOD_KEYSYMS[keysym])
            return "break"
        main = _normalize_keysym(keysym, event.char)
        if main is None:
            status.configure(text=f"Tecla não suportada: {keysym}")
            return "break"
        mods = [m for m in _MOD_ORDER if m in held]
        if not mods:
            status.configure(text="Use ao menos um modificador (Ctrl/Alt/Shift).")
            return "break"
        combo = "+".join([*mods, main.upper() if len(main) == 1 else main])
        hotkey_var.set(combo)
        status.configure(text=f"Atalho definido: {combo}")
        stop_capture()
        return "break"

    def on_release(event: tk.Event) -> None:
        if event.keysym in _MOD_KEYSYMS:
            held.discard(_MOD_KEYSYMS[event.keysym])

    button.configure(command=start_capture)
    top = parent.winfo_toplevel()
    top.bind("<KeyPress>", on_press, add="+")
    top.bind("<KeyRelease>", on_release, add="+")


def _normalize_keysym(keysym: str, char: str) -> str | None:
    if keysym in _KEYSYM_MAP:
        return _KEYSYM_MAP[keysym]
    if len(keysym) == 1 and keysym.isalnum():
        return keysym
    if char and char.isdigit():
        return char
    if len(keysym) in (2, 3) and keysym[0] in "Ff" and keysym[1:].isdigit():
        return keysym.lower()
    return None


def _paste_into(root, var: tk.StringVar) -> None:
    try:
        var.set(root.clipboard_get().strip())
    except Exception:
        pass


def _center(root) -> None:
    w, h = root.winfo_reqwidth(), root.winfo_reqheight()
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")


def _build_microphone_options(
    current_value: int | str | None,
) -> tuple[list[_MicrophoneOption], str, str | None]:
    options = [_MicrophoneOption(_DEFAULT_MIC, None)]
    error = None

    try:
        devices = list_input_devices()
    except Exception as exc:
        devices = []
        error = str(exc)

    selected_label = _DEFAULT_MIC
    matched_current = current_value is None
    for index, name, sample_rate in devices:
        label = f"[{index}] {name} ({sample_rate} Hz)"
        options.append(_MicrophoneOption(label, index))
        if _matches_microphone(current_value, index, name):
            selected_label = label
            matched_current = True

    if current_value is not None and not matched_current:
        unavailable = _MicrophoneOption(f"Atual indisponível: {current_value}", current_value)
        options.append(unavailable)
        selected_label = unavailable.label

    return options, selected_label, error


def _matches_microphone(value: int | str | None, index: int, name: str) -> bool:
    if value is None:
        return False
    if isinstance(value, int):
        return value == index
    return value == name


def _build_language_options(current: str) -> tuple[list[tuple[str, str]], str]:
    options = list(LANGUAGE_OPTIONS)
    for label, value in options:
        if value == current:
            return options, label

    custom_label = f"Personalizado: {current}"
    options.append((custom_label, current))
    return options, custom_label


def _build_record_limit_options(current: int) -> tuple[list[tuple[str, int]], str]:
    options = list(RECORD_LIMIT_OPTIONS)
    for label, value in options:
        if value == current:
            return options, label

    if current <= 0:
        return options, options[0][0]

    mins = current / 60.0
    custom_label = f"Personalizado: {int(mins) if mins.is_integer() else mins:.1f} min"
    options.append((custom_label, current))
    return options, custom_label

