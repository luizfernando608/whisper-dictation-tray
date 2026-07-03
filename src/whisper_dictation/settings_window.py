from __future__ import annotations

import threading
import tkinter as tk
from dataclasses import asdict, dataclass, replace
from tkinter import messagebox
from typing import Callable

import customtkinter as ctk

from whisper_dictation import secrets_store
from whisper_dictation._version import __version__
from whisper_dictation.audio import list_input_devices
from whisper_dictation.providers import CLOUD_PROVIDERS, PROVIDER_ORDER, PROVIDERS
from whisper_dictation.settings import AppConfig
from whisper_dictation.win32_hotkey import parse_hotkey

SaveCallback = Callable[[AppConfig], bool]

LANGUAGE_OPTIONS = [
    ("Automático", "auto"),
    ("Português", "pt"),
    ("Inglês", "en"),
]

_DEFAULT_MIC = "Padrão do sistema"

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
    ctk.set_appearance_mode("system")
    ctk.set_default_color_theme("blue")

    root = ctk.CTk()
    root.title("Whisper Dictation — Configurações")
    root.resizable(False, False)

    heading_font = ctk.CTkFont(size=16, weight="bold")
    hint_font = ctk.CTkFont(size=11)
    muted = ("gray45", "gray60")

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

    hotkey_var = tk.StringVar(value=config.hotkey)
    key_var = tk.StringVar()
    show_key_var = tk.BooleanVar(value=False)

    tabview = ctk.CTkTabview(root, width=470, height=290, anchor="w")
    tabview.pack(padx=16, pady=(14, 0), fill="both", expand=True)
    tab_trans = tabview.add("Transcrição")
    tab_audio = tabview.add("Áudio")
    tab_hotkey = tabview.add("Atalho")
    tab_about = tabview.add("Sobre")

    # =========================== TRANSCRIÇÃO ===========================
    tab_trans.grid_columnconfigure(1, weight=1)

    ctk.CTkLabel(tab_trans, text="Provedor").grid(row=0, column=0, sticky="w", padx=(4, 14), pady=8)
    provider_menu = ctk.CTkOptionMenu(tab_trans, values=provider_labels, width=270)
    provider_menu.grid(row=0, column=1, sticky="ew", pady=8)

    ctk.CTkLabel(tab_trans, text="Modelo").grid(row=1, column=0, sticky="w", padx=(4, 14), pady=8)
    # Editable: pick from the list or type any model id (lists drift over time).
    model_menu = ctk.CTkComboBox(tab_trans, values=["—"], width=270)
    model_menu.grid(row=1, column=1, sticky="ew", pady=8)

    key_label = ctk.CTkLabel(tab_trans, text="Chave de API")
    key_label.grid(row=2, column=0, sticky="w", padx=(4, 14), pady=(8, 2))
    key_frame = ctk.CTkFrame(tab_trans, fg_color="transparent")
    key_frame.grid(row=2, column=1, sticky="ew", pady=(8, 2))
    key_frame.grid_columnconfigure(0, weight=1)
    key_entry = ctk.CTkEntry(key_frame, textvariable=key_var, show="•", placeholder_text="cole sua chave aqui")
    key_entry.grid(row=0, column=0, sticky="ew")
    ctk.CTkButton(
        key_frame, text="Colar", width=64, command=lambda: _paste_into(root, key_var)
    ).grid(row=0, column=1, padx=(8, 0))

    def _toggle_show_key() -> None:
        key_entry.configure(show="" if show_key_var.get() else "•")

    key_switch = ctk.CTkCheckBox(
        tab_trans, text="Mostrar chave", variable=show_key_var, command=_toggle_show_key
    )
    key_switch.grid(row=3, column=1, sticky="w", pady=(4, 0))
    key_hint = ctk.CTkLabel(tab_trans, text="", text_color=muted, font=hint_font)
    key_hint.grid(row=4, column=1, sticky="w", pady=(2, 4))

    ctk.CTkLabel(tab_trans, text="Idioma").grid(row=5, column=0, sticky="w", padx=(4, 14), pady=8)
    language_menu = ctk.CTkOptionMenu(tab_trans, values=language_labels, width=270)
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
    ctk.CTkLabel(tab_audio, text="Microfone de entrada").pack(anchor="w", padx=4, pady=(8, 6))
    mic_menu = ctk.CTkOptionMenu(tab_audio, values=mic_labels or [_DEFAULT_MIC], width=430, dynamic_resizing=False)
    mic_menu.pack(fill="x", padx=4)
    mic_menu.set(current_mic)
    if mic_error:
        ctk.CTkLabel(
            tab_audio, text=f"Falha ao listar microfones: {mic_error}", text_color=("red", "#ff6b6b")
        ).pack(anchor="w", padx=4, pady=(6, 0))

    # ============================== ATALHO =============================
    ctk.CTkLabel(tab_hotkey, text="Atalho global para iniciar/parar a gravação").pack(
        anchor="w", padx=4, pady=(8, 8)
    )
    hotkey_row = ctk.CTkFrame(tab_hotkey, fg_color="transparent")
    hotkey_row.pack(fill="x", padx=4)
    ctk.CTkEntry(hotkey_row, textvariable=hotkey_var, width=240).pack(side="left")
    capture_btn = ctk.CTkButton(hotkey_row, text="Gravar atalho", width=140)
    capture_btn.pack(side="left", padx=(8, 0))
    hotkey_status = ctk.CTkLabel(tab_hotkey, text="Ex.: Ctrl+Shift+H", text_color=muted, font=hint_font)
    hotkey_status.pack(anchor="w", padx=4, pady=(8, 0))
    _wire_hotkey_capture(tab_hotkey, capture_btn, hotkey_var, hotkey_status)

    # ============================== SOBRE =============================
    _build_about_tab(tab_about, context, heading_font, muted)

    # ============================== RODAPÉ ============================
    footer = ctk.CTkFrame(root, fg_color="transparent")
    footer.pack(fill="x", padx=16, pady=(10, 14))

    def save() -> None:
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
        data["hotkey"] = hotkey

        new_config = AppConfig(**AppConfig._sanitize(data))

        try:
            _persist_keys(key_by_provider, original_keys)
        except Exception as exc:
            messagebox.showerror("Configurações", f"Falha ao salvar a chave de API: {exc}")
            return

        if on_save(new_config):
            root.destroy()
        else:
            messagebox.showerror(
                "Configurações", "Não foi possível salvar agora. Veja os logs do aplicativo."
            )

    ctk.CTkButton(
        footer, text="Cancelar", width=110, command=root.destroy, fg_color="transparent", border_width=1
    ).pack(side="right")
    ctk.CTkButton(footer, text="Salvar", width=120, command=save).pack(side="right", padx=(0, 8))

    root.update_idletasks()
    _center(root)
    root.after(60, root.lift)
    root.after(80, root.focus_force)
    root.mainloop()


def _persist_keys(current: dict[str, str], original: dict[str, str]) -> None:
    for provider, value in current.items():
        if value != original.get(provider, ""):
            secrets_store.set_api_key(provider, value)


def _build_about_tab(tab, context: SettingsContext, heading_font, muted) -> None:
    ctk.CTkLabel(tab, text="Whisper Dictation Tray", font=heading_font).pack(
        anchor="w", padx=4, pady=(8, 0)
    )
    ctk.CTkLabel(tab, text=f"Versão {context.version}", text_color=muted).pack(
        anchor="w", padx=4, pady=(0, 14)
    )

    update_status = ctk.CTkLabel(tab, text="")
    update_status.pack(anchor="w", padx=4)
    update_btn = ctk.CTkButton(tab, text="Verificar atualizações", width=190)
    update_btn.pack(anchor="w", padx=4, pady=(4, 14))
    _wire_update_button(tab, update_btn, update_status, context)

    tools = ctk.CTkFrame(tab, fg_color="transparent")
    tools.pack(anchor="w", padx=4, fill="x")
    secondary = {"fg_color": "transparent", "border_width": 1, "text_color": ("gray10", "gray90")}
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
            status.configure(text="Baixando atualização…")
            button.configure(state="disabled")
            threading.Thread(target=lambda: context.run_update(update), daemon=True).start()

    def do_check() -> None:
        button.configure(state="disabled")
        status.configure(text="Verificando…")

        def worker() -> None:
            try:
                update = context.check_update()  # type: ignore[misc]
                error = False
            except Exception:
                update, error = None, True

            def finish() -> None:
                button.configure(state="normal")
                if error:
                    status.configure(text="Não foi possível verificar agora.")
                elif update:
                    version = getattr(update, "version", "?")
                    status.configure(text=f"Atualização disponível: {version}")
                    button.configure(text="Atualizar agora", command=lambda: _apply_update(update))
                else:
                    status.configure(text="Você já está na versão mais recente.")

            parent.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

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
