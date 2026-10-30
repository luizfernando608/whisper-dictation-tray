"""Plain-text transcript formatting shared by the library window and agent tools.

Kept free of audio/GUI imports so the console tool and MCP server start quickly.
"""
from __future__ import annotations

import re


def timestamp(seconds: float, milliseconds: bool = False) -> str:
    value = max(0, round(seconds * 1000))
    hours, value = divmod(value, 3600000)
    minutes, value = divmod(value, 60000)
    seconds, millis = divmod(value, 1000)
    text = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return text + f",{millis:03d}" if milliseconds else text


def speaker_label(speaker: str | None) -> str:
    if not speaker:
        return "Falante não identificado"
    match = re.fullmatch(r"SPEAKER_(\d+)", speaker, re.IGNORECASE)
    return f"Falante {int(match[1]) + 1}" if match else speaker


def transcript_text(result: dict, names: dict | None = None, srt: bool = False) -> str:
    names = names or {}
    lines = []
    for index, segment in enumerate(result.get("segments", []), 1):
        speaker = segment.get("speaker")
        label = names.get(speaker, speaker_label(speaker))
        text = segment.get("text", "").strip()
        if srt:
            lines.append(f"{index}\n{timestamp(segment['start'], True)} --> {timestamp(segment['end'], True)}\n{label}: {text}\n")
        else:
            lines.append(f"[{timestamp(segment['start'])}] {label}: {text}")
    return "\n".join(lines) if lines else result.get("text", "")


def speaker_turns(result: dict, names: dict | None = None) -> list[dict]:
    """Join consecutive segments of the same speaker into one turn.

    Word-level segments split on short pauses; agents read whole turns more
    reliably and with fewer tokens. Raw identifiers stay available as ``speaker``.
    """
    names = names or {}
    turns: list[dict] = []
    for segment in result.get("segments", []):
        text = str(segment.get("text", "")).strip()
        if not text:
            continue
        speaker = segment.get("speaker")
        if turns and turns[-1]["speaker"] == speaker:
            turns[-1]["end"] = max(turns[-1]["end"], float(segment["end"]))
            turns[-1]["text"] = f"{turns[-1]['text']} {text}"
            continue
        turns.append({"start": float(segment["start"]), "end": float(segment["end"]), "speaker": speaker,
                      "label": names.get(speaker, speaker or "DESCONHECIDO"), "text": text})
    return turns


def agent_text(result: dict, names: dict | None = None) -> str:
    """Compact ``[hh:mm:ss] SPEAKER_00: ...`` lines, one per speaker turn."""
    turns = speaker_turns(result, names)
    if not turns:
        return str(result.get("text", "")).strip()
    return "\n".join(f"[{timestamp(turn['start'])}] {turn['label']}: {turn['text']}" for turn in turns)
