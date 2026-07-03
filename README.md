# Whisper Dictation Tray

I originally built this because I was getting tired of manually typing out long prompts all day.

It's a lightweight tray app that lets you dictate text anywhere on Windows using Whisper. It actually gets punctuation right and is way more accurate than the built-in Windows dictation.

## Getting Started

Grab the installer directly here: [**Download WhisperDictation_Installer.exe**](https://github.com/luizfernando608/whisper-dictation-tray/releases/latest/download/WhisperDictation_Installer.exe)

Run it, and the app will live in your system tray (near the clock).

## Updating

The app checks GitHub for a newer release on startup. When one is available you
get a notification and the tray menu shows **⬇ Atualizar para vX.Y.Z** — click it
(or the button under **Configurações → Sobre**) and it downloads and runs the
latest installer automatically, then relaunches the updated app. Your
`config.json`, saved API keys, logs and temporary folders are preserved.

You can still update manually by downloading and running the latest installer.

## Usage

1. Click inside any text box (ChatGPT, Word, Discord, whatever).
2. Press `Ctrl + Shift + H` to start recording.
3. Talk.
4. Press `Ctrl + Shift + H` again to stop.
5. Your text gets pasted automatically.

## Settings

Open settings either from the **"Configurações do Whisper Dictation"** shortcut
(Start Menu / Desktop) or by right-clicking the tray icon → `Configurações...`.
The window has tabs:

- **Transcrição**: pick a provider and its model, and paste the provider's API
  key right there.
  - **Groq** (cloud, very fast)
  - **OpenAI** (`gpt-4o-transcribe`, `gpt-4o-mini-transcribe`, `whisper-1`)
  - **Google Gemini** (`gemini-2.5-flash`, `gemini-2.5-pro`, `gemini-2.0-flash`)
  - **Local** (100% offline via `faster-whisper`, no key needed)
  - Language: automatic detection, Portuguese, or English.
- **Áudio**: input microphone.
- **Atalho**: record a new global hotkey by pressing the key combination.
- **Sobre**: version, check/apply updates, and quick access to logs and folder.

Settings are saved to `config.json` and applied immediately. **API keys are never
written to disk in plain text** — they are stored in the Windows Credential
Manager. If a cloud provider fails (no key, network issue), it automatically
falls back to the local model.

## Privacy (It runs locally)

Out of the box, this runs 100% offline using `faster-whisper`. Your audio never leaves your computer.

If you want it to be ridiculously fast and don't mind using the cloud, switch the
provider to Groq, OpenAI or Gemini in the settings window and paste your API key.
Completely optional.
