# PACE — The Speed of Thought

Dictate at the speed of thought. A lightweight, premium Windows app for seamless neural voice dictation across any application.

## Getting Started

Grab the installer directly here: [**Download Pace_Installer.exe**](https://github.com/luizfernando608/whisper-dictation-tray/releases/latest/download/Pace_Installer.exe)

Run it, and PACE will live in your system tray (near the clock).

## Updating

The app checks GitHub for a newer release on startup and prepares the exact
installer after checking its SHA-256 and size. Interrupted or invalid downloads
get up to three fresh attempts. Once ready, choose **Reiniciar para atualizar** in
the tray or **Configurações → Sobre**. Finish recordings and processing before
restarting. PACE closes its windows and confirms that its detached helper is ready
before exiting to install silently and relaunch. If installation or startup
verification fails, the helper restores the previous binaries, shortcuts and
installation registration.
Settings, credentials and the recordings library are preserved.

You can still update manually by downloading and running the latest installer.

## Usage

1. Click inside any text box (ChatGPT, Word, Discord, whatever).
2. Press `Ctrl + Shift + H` to start recording.
3. Talk.
4. Press `Ctrl + Shift + H` again to stop.
5. Your text gets pasted automatically.

## Meetings and media files (1.5)

- `Ctrl + Shift + R`: start or finish a meeting recording, capturing the selected
  microphone and computer output simultaneously. Recordings stream to disk without
  a time limit. The selected output includes all sounds played through that device.
- `Ctrl + Shift + P`: pause or resume. Both controls are also in the tray and the
  **Gravações** window. An optional small overlay shows levels, pause and stop;
  it is hidden by default.
- Open **Gravações** from the tray or Start Menu to play audio, search transcripts,
  rename recordings and speakers, copy text, export audio/TXT/SRT/JSON, retry,
  cancel processing or delete a recording. Processing starts after recording ends.
- Import or drag audio/video files into this same window. PACE extracts their audio
  without changing the original file. WAV, MP3, M4A, OGG, FLAC, MP4 and MKV are
  supported through the bundled decoder; no system FFmpeg installation is needed.

The default **Nemotron 3** automatically prepares missing or incomplete model
assets when processing a recording. Download and setup progress appears in the
library; audio and transcription checkpoints are preserved if setup fails.
You can also prepare them in advance through **Configurações → Reuniões →
Preparar modelos**. Public weights and native runtimes need no account or token
and work locally once prepared. CPU processing remains available if the optional
Vulkan runtime cannot be prepared; the official Windows CPU runtime requires AVX2.
It automatically estimates up to eight speakers. CPU and Vulkan GPU execution
are compared against CPU results and timed; automatic selection retains the fastest
validated option and falls back to CPU when a GPU fails. GPU availability depends
on the installed driver; using a GPU is not necessarily faster.

The advanced **Community-1** option supports an exact or minimum/maximum speaker
count. Its first download requires accepting the publisher's conditions and a
Hugging Face token, stored in Windows Credential Manager. It provides CPU and
optional CUDA/OpenVINO/DirectML execution after validation. Speaker labels identify
voices within a recording; the app does not know a person's name until you assign it.

Transcription uses the configured provider. Groq and OpenAI `whisper-1` provide
timestamps; models without timestamps use the selected local Whisper or
`whisper-1` alternative. Local models must be prepared before use. Failed diarization
preserves audio and any partial transcript so processing can be retried.

The library defaults to `%LOCALAPPDATA%\PaceData\recordings`, outside the app's
installation folder. Its location and the model cache can be changed in settings.

## Settings

The tray icon's primary click opens PACE. Dictation and meeting capture remain
explicit tray actions and global shortcuts. Library and settings share one main
window: use **Configurações** in the sidebar, the Start Menu shortcut, or the tray
menu to open the settings page. Switching pages preserves unsaved settings drafts
and the selected recording; Save applies changes and returns to the library.
The settings page has tabs:

- **Transcrição**: pick a provider and its model, and paste the provider's API
  key right there.
  - **Groq** (cloud, very fast)
  - **OpenAI** (`gpt-transcribe` by default, plus compatible legacy transcription models)
  - **Google Gemini** (`gemini-3.8-flash` by default, `gemini-3.5-transcribe`,
    and other current audio-input Gemini models)
  - **Local** (100% offline via `faster-whisper`, no key needed)
  - Language: automatic detection, Portuguese, or English.
- **Áudio**: input microphone and recording time limit (unlimited manual stop, or 2 to 120 minutes auto-stop).
- **Atalho**: record a new global hotkey by pressing the key combination.
- **Reuniões**: meeting shortcuts/devices, optional overlay, library location,
  diarization engine/backend, speaker counts and model preparation.
- **Sobre**: version, check/apply updates, and quick access to logs and folder.

Settings are saved to `config.json` and applied immediately. **API keys are never
written to disk in plain text** — they are stored in the Windows Credential
Manager. If a cloud provider fails (no key, network issue), it automatically
falls back to the local model.

## Privacy (It runs locally)

Meeting capture, playback and diarization run locally. With the **Local**
transcription provider, prepared `faster-whisper` models keep transcription offline
as well. Model preparation requires an internet connection to download files.

If you want it to be ridiculously fast and don't mind using the cloud, switch the
provider to Groq, OpenAI or Gemini in the settings window and paste your API key.
Cloud transcription sends the audio to the selected provider; diarization remains
local. Choose **Local** when you want offline transcription.

## Development and validation

Install `requirements.txt` in a Python 3.12 Windows x64 virtual environment.
Run `scripts/test.ps1` for automated tests and `build.ps1` for PyInstaller/Inno Setup.
The build isolates DirectML from the CPU ONNX runtime and checks downloaded wheel
integrity. Model preparation keeps publisher notices/licenses beside cached weights:
Nemotron 3 uses OpenMDW-1.1, its native runtime Apache-2.0, and Community-1 CC-BY-4.0.
See [the installation/release validation](.specs/features/meeting-recordings/install-validation-2026-10-20.md)
and [the provider/UI work](.specs/features/provider-compatibility-unified-window/spec.md)
for measured results and the scope of real versus simulated API validation.
