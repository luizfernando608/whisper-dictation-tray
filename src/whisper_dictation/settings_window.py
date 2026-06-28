from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable

from whisper_dictation.audio import list_input_devices
from whisper_dictation.settings import AppConfig

SaveCallback = Callable[[AppConfig], bool]

GROQ_MODELS = [
    "whisper-large-v3",
    "whisper-large-v3-turbo",
    "distil-whisper-large-v3-en",
]

LOCAL_MODELS = ["tiny", "base", "small", "medium", "large-v2", "large-v3"]
COMPUTE_TYPES = ["int8", "float32"]
LANGUAGE_OPTIONS = [
    ("Automático", "auto"),
    ("Português", "pt"),
    ("Inglês", "en"),
]

_DEFAULT_MIC = "Padrão do sistema"
_window_lock = threading.Lock()
_window_thread: threading.Thread | None = None


@dataclass(frozen=True, slots=True)
class _MicrophoneOption:
    label: str
    value: int | str | None


def open_settings_window(config: AppConfig, on_save: SaveCallback) -> None:
    global _window_thread
    with _window_lock:
        if _window_thread is not None and _window_thread.is_alive():
            return

        _window_thread = threading.Thread(
            target=_run_window,
            args=(replace(config), on_save),
            daemon=True,
            name="SettingsTk",
        )
        _window_thread.start()


def _run_window(config: AppConfig, on_save: SaveCallback) -> None:
    root = tk.Tk()
    root.title("Configurações - Whisper Dictation")
    root.resizable(False, False)

    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure("TLabel", font=("Segoe UI", 10))
    style.configure("TRadiobutton", font=("Segoe UI", 10))
    style.configure("Header.TLabel", font=("Segoe UI", 10, "bold"))
    style.configure("TButton", font=("Segoe UI", 10), padding=(10, 5))

    provider_var = tk.StringVar(value=config.transcription_provider)
    groq_model_var = tk.StringVar(value=config.groq_model)
    local_model_var = tk.StringVar(value=config.model_size)
    compute_type_var = tk.StringVar(value=config.compute_type)
    language_options, current_language = _build_language_options(config.language)
    language_by_label = {label: value for label, value in language_options}
    language_var = tk.StringVar(value=current_language)

    mic_options, current_mic, mic_error = _build_microphone_options(config.input_device)
    mic_by_label = {option.label: option for option in mic_options}
    mic_var = tk.StringVar(value=current_mic)

    main = ttk.Frame(root, padding=20)
    main.pack(fill=tk.BOTH, expand=True)
    main.columnconfigure(1, weight=1)

    ttk.Label(main, text="Transcrição", style="Header.TLabel").grid(
        row=0, column=0, columnspan=2, sticky="w", pady=(0, 6)
    )
    provider_frame = ttk.Frame(main)
    provider_frame.grid(row=1, column=0, columnspan=2, sticky="w")
    ttk.Radiobutton(provider_frame, text="Groq API", variable=provider_var, value="groq").pack(
        side=tk.LEFT, padx=(0, 18)
    )
    ttk.Radiobutton(provider_frame, text="CPU local", variable=provider_var, value="local").pack(
        side=tk.LEFT
    )

    model_frame = ttk.Frame(main)
    model_frame.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(12, 0))
    model_frame.columnconfigure(1, weight=1)

    ttk.Label(model_frame, text="Idioma:").grid(row=0, column=0, sticky="w")
    ttk.Combobox(
        model_frame,
        textvariable=language_var,
        values=[label for label, _value in language_options],
        state="readonly",
        width=32,
    ).grid(row=0, column=1, sticky="ew", padx=(12, 0))

    groq_label = ttk.Label(model_frame, text="Modelo Groq:")
    groq_label.grid(row=1, column=0, sticky="w", pady=(8, 0))
    groq_combo = ttk.Combobox(
        model_frame,
        textvariable=groq_model_var,
        values=_values_with_current(config.groq_model, GROQ_MODELS),
        width=32,
    )
    groq_combo.grid(row=1, column=1, sticky="ew", padx=(12, 0), pady=(8, 0))

    local_label = ttk.Label(model_frame, text="Modelo local:")
    local_label.grid(row=2, column=0, sticky="w", pady=(8, 0))
    local_combo = ttk.Combobox(
        model_frame,
        textvariable=local_model_var,
        values=_values_with_current(config.model_size, LOCAL_MODELS),
        state="readonly",
        width=32,
    )
    local_combo.grid(row=2, column=1, sticky="ew", padx=(12, 0), pady=(8, 0))

    compute_label = ttk.Label(model_frame, text="CPU compute:")
    compute_label.grid(row=3, column=0, sticky="w", pady=(8, 0))
    compute_combo = ttk.Combobox(
        model_frame,
        textvariable=compute_type_var,
        values=_values_with_current(config.compute_type, COMPUTE_TYPES),
        state="readonly",
        width=32,
    )
    compute_combo.grid(row=3, column=1, sticky="ew", padx=(12, 0), pady=(8, 0))

    def refresh_provider(*_: object) -> None:
        if provider_var.get() == "groq":
            groq_label.grid()
            groq_combo.grid()
            local_label.grid_remove()
            local_combo.grid_remove()
            compute_label.grid_remove()
            compute_combo.grid_remove()
            return

        groq_label.grid_remove()
        groq_combo.grid_remove()
        local_label.grid()
        local_combo.grid()
        compute_label.grid()
        compute_combo.grid()

    provider_var.trace_add("write", refresh_provider)
    refresh_provider()

    ttk.Separator(main, orient="horizontal").grid(
        row=3, column=0, columnspan=2, sticky="ew", pady=14
    )

    ttk.Label(main, text="Microfone", style="Header.TLabel").grid(
        row=4, column=0, columnspan=2, sticky="w", pady=(0, 6)
    )
    ttk.Combobox(
        main,
        textvariable=mic_var,
        values=[option.label for option in mic_options],
        state="readonly",
        width=48,
    ).grid(row=5, column=0, columnspan=2, sticky="ew")
    if mic_error:
        ttk.Label(main, text=f"Falha ao listar microfones: {mic_error}").grid(
            row=6, column=0, columnspan=2, sticky="w", pady=(6, 0)
        )

    btn_frame = ttk.Frame(main)
    btn_frame.grid(row=7, column=0, columnspan=2, pady=(20, 0))

    def save() -> None:
        selected_mic = mic_by_label.get(mic_var.get())
        if selected_mic is None:
            messagebox.showerror("Configurações", "Selecione um microfone válido.")
            return

        data = asdict(config)
        data["transcription_provider"] = provider_var.get()
        data["language"] = language_by_label.get(language_var.get(), config.language)
        data["groq_model"] = groq_model_var.get()
        data["model_size"] = local_model_var.get()
        data["compute_type"] = compute_type_var.get()
        data["input_device"] = selected_mic.value

        new_config = AppConfig(**AppConfig._sanitize(data))
        if on_save(new_config):
            root.destroy()
        else:
            messagebox.showerror(
                "Configurações",
                "Não foi possível salvar agora. Veja os logs do aplicativo.",
            )

    ttk.Button(btn_frame, text="Salvar", command=save).grid(row=0, column=0, padx=6)
    ttk.Button(btn_frame, text="Cancelar", command=root.destroy).grid(row=0, column=1, padx=6)

    root.update_idletasks()
    w, h = root.winfo_reqwidth(), root.winfo_reqheight()
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")
    root.lift()
    root.focus_force()

    root.mainloop()


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


def _values_with_current(current: str, values: list[str]) -> list[str]:
    return values if current in values else [current, *values]
