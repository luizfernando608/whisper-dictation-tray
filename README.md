# Whisper Dictation Tray

I originally built this because I was getting tired of manually typing out long prompts all day.

It's a lightweight tray app that lets you dictate text anywhere on Windows using Whisper. It actually gets punctuation right and is way more accurate than the built-in Windows dictation.

## Getting Started

Grab the installer directly here: [**Download WhisperDictation_Installer.exe**](https://github.com/luizfernando608/whisper-dictation-tray/releases/latest/download/WhisperDictation_Installer.exe)

Run it, and the app will live in your system tray (near the clock).

## Usage

1. Click inside any text box (ChatGPT, Word, Discord, whatever).
2. Press `Ctrl + Shift + H` to start recording.
3. Talk.
4. Press `Ctrl + Shift + H` again to stop.
5. Your text gets pasted automatically.

## Privacy (It runs locally)

Out of the box, this runs 100% offline using `faster-whisper`. Your audio never leaves your computer. 

If you want it to be ridiculously fast and don't mind using the cloud, you can right-click the tray icon, open the config, and drop in a Groq API key. But again, completely optional.
