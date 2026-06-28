# Whisper Dictation Tray

I originally built this because I was getting tired of manually typing out long prompts all day.

It's a lightweight tray app that lets you dictate text anywhere on Windows using Whisper. It actually gets punctuation right and is way more accurate than the built-in Windows dictation.

## Getting Started

Grab the installer directly here: [**Download WhisperDictation_Installer.exe**](https://github.com/luizfernando608/whisper-dictation-tray/releases/latest/download/WhisperDictation_Installer.exe)

Run it, and the app will live in your system tray (near the clock).

## Updating

Download and run the latest installer again. It upgrades the existing install in
`%LOCALAPPDATA%\WhisperDictation`, closes the running app when Windows allows it,
replaces the application files, and keeps your `config.json`, `.env`, logs, and
temporary folders.

## Usage

1. Click inside any text box (ChatGPT, Word, Discord, whatever).
2. Press `Ctrl + Shift + H` to start recording.
3. Talk.
4. Press `Ctrl + Shift + H` again to stop.
5. Your text gets pasted automatically.

## Settings

Right-click the tray icon and open `Configurações...` to choose:

- Transcription provider: Groq API or local CPU.
- Language: automatic detection, Portuguese, or English.
- Transcription model for the selected provider.
- Input microphone.

The settings are saved to `config.json` and applied immediately when you click save.

## Privacy (It runs locally)

Out of the box, this runs 100% offline using `faster-whisper`. Your audio never leaves your computer. 

If you want it to be ridiculously fast and don't mind using the cloud, you can switch the provider to Groq API in the settings window and set a Groq API key in `.env`. But again, completely optional.
