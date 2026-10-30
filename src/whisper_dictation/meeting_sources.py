"""Prepare aligned capture sources without changing the original recording.

WebRTC receives playback as its reverse reference. Its fixed 8 ms output delay
is removed before writing the microphone derivative; source timestamps remain
on the original capture timeline. Mono imports keep the existing mixed path.
"""
from __future__ import annotations

from contextlib import ExitStack
import hashlib
import json
import math
import os
from pathlib import Path
import uuid

import numpy as np

from whisper_dictation.diarization import ProcessingCancelled, atomic_json
from whisper_dictation.media import PCM16WaveWriter
from whisper_dictation.wav_reader import PCM16Reader


RATE = 16000
FRAME = 160
DELAY = 128
VERSION = "webrtc-aec-1.0.1-v1"
SPEECH_PACKING = {"version": 1, "threshold": .35, "min_silence_duration_ms": 400, "speech_pad_ms": 200}


def _speech_chunks(samples):
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    return get_speech_timestamps(samples, VadOptions(**{key: value for key, value in SPEECH_PACKING.items()
                                                       if key != "version"}), sampling_rate=RATE)


def pack_speech(audio: Path, destination: Path, cancellation) -> dict:
    """Remove silence in one bounded ASR chunk; retain the exact sample map."""
    cancellation.check()
    reader = PCM16Reader(audio)
    try:
        if reader.rate != RATE or reader.channels != 1 or reader.frames > 300 * RATE:
            raise ValueError("Detecção de fala requer um bloco mono 16 kHz de até cinco minutos.")
        raw = reader.readframes(reader.frames)
    finally:
        reader.close()
    samples = np.frombuffer(raw, "<i2")
    chunks = _speech_chunks(samples.astype(np.float32) / 32768.0)
    cancellation.check()
    count = sum(chunk["end"] - chunk["start"] for chunk in chunks)
    if not count:
        return {"audio": None, "duration": 0.0, "chunks": []}
    with PCM16WaveWriter(destination) as output:
        for chunk in chunks:
            cancellation.check()
            output.writeframesraw(samples[chunk["start"]:chunk["end"]].tobytes())
    return {"audio": Path(destination), "duration": count / RATE, "chunks": chunks}


def restore_speech(transcript: dict, chunks: list[dict]) -> dict:
    """Restore provider times using removed samples, without guessing word times."""
    from faster_whisper.vad import SpeechTimestampsMap

    if not chunks:
        return {**transcript, "segments": []}
    mapping = SpeechTimestampsMap(chunks, RATE, time_precision=6)
    segments = []
    for segment in transcript["segments"]:
        words = []
        for word in segment.get("words", []):
            index = mapping.get_chunk_index((word["start"] + word["end"]) / 2)
            words.append({**word, "start": mapping.get_original_time(word["start"], index),
                          "end": mapping.get_original_time(word["end"], index)})
        start = words[0]["start"] if words else mapping.get_original_time(segment["start"])
        end = max(word["end"] for word in words) if words else mapping.get_original_time(segment["end"], is_end=True)
        segments.append({**segment, "start": start, "end": end, "words": words})
    return {**transcript, "segments": segments}


def _processor():
    from aec_audio_processing import AudioProcessor

    processor = AudioProcessor(enable_aec=True, enable_ns=False, enable_agc=False, enable_vad=False)
    processor.set_stream_format(RATE, 1)
    processor.set_reverse_stream_format(RATE, 1)
    return processor


def _hash(path, cancellation):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            cancellation.check()
            digest.update(block)
    return digest.hexdigest()


def _geometry(path):
    reader = PCM16Reader(path)
    try:
        return reader.rate, reader.channels, reader.frames
    finally:
        reader.close()


def prepare_sources(audio: Path, directory: Path, cancellation, progress=None):
    """Return verified sources, explicit unavailable fallback, or None for mono.

    Only capture-produced, equal-length mono 16 kHz files are eligible. Missing
    native support retains mixed processing rather than doubling playback text.
    """
    cancellation.check()
    audio, directory = Path(audio), Path(directory)
    microphone, computer = directory / "microphone.wav", directory / "computer.wav"
    try:
        capture = json.loads((directory / "capture.json").read_text(encoding="utf-8"))
        if not isinstance(capture, dict):
            return None
        if not {"microphone", "computer"}.issubset(capture.get("sources", {})):
            return None
        duration = float(capture["duration"])
        if not math.isfinite(duration) or duration <= 0:
            return None
        for path in (audio, microphone, computer):
            if path.is_symlink() or path.resolve().parent != directory.resolve():
                return None
        geometry = _geometry(audio)
        if geometry[:2] != (RATE, 1) or abs(geometry[2] / RATE - duration) > .02:
            return None
        if _geometry(microphone) != geometry or _geometry(computer) != geometry:
            return None
    except (OSError, ValueError, KeyError, TypeError):
        return None

    folder = directory / "sources"
    destination = folder / "microphone" / "aec.wav"
    manifest = folder / "prepared.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    signature = {"version": VERSION, "microphone": _hash(microphone, cancellation),
                 "computer": _hash(computer, cancellation), "frames": geometry[2]}
    try:
        saved = json.loads(manifest.read_text(encoding="utf-8"))
        if isinstance(saved, dict) and saved.get("signature") == signature and saved.get("output_sha256") == _hash(destination, cancellation):
            return {"sources": {"microphone": destination, "computer": computer},
                    "processing": saved["processing"], "warnings": []}
    except (OSError, ValueError, KeyError, TypeError):
        pass
    try:
        processor = _processor()
    except (ImportError, OSError) as exc:
        return {"sources": {}, "processing": {"mode": "mixed", "reason": "aec-unavailable"},
                "warnings": [f"Cancelamento de eco indisponível; usando áudio misturado: {type(exc).__name__}."]}
    temporary = destination.with_name("aec-" + uuid.uuid4().hex + ".part")
    report = progress or (lambda message, fraction: None)
    pending, masks = bytearray(), bytearray()
    skip, written, hold = DELAY, 0, 0
    microphone_energy = processed_energy = 0.0
    try:
        with ExitStack() as stack:
            near, far = PCM16Reader(microphone), PCM16Reader(computer)
            stack.callback(near.close)
            stack.callback(far.close)
            output = stack.enter_context(PCM16WaveWriter(temporary))
            total = geometry[2]
            while written < total:
                cancellation.check()
                raw = near.readframes(FRAME)
                reference = far.readframes(FRAME)
                count = len(raw) // 2
                if count:
                    if len(reference) != len(raw):
                        raise ValueError("Faixas de captura terminaram em tempos diferentes.")
                    if any(reference):
                        hold = RATE // 4
                    else:
                        hold = max(0, hold - count)
                    pending.extend(raw)
                    masks.extend(bytes([hold == 0]) * count)
                    microphone_energy += float(np.dot(np.frombuffer(raw, "<i2").astype(float),
                                                      np.frombuffer(raw, "<i2").astype(float)))
                processor.process_reverse_stream(reference.ljust(FRAME * 2, b"\0"))
                processor.set_stream_delay(50)
                processed = processor.process_stream(raw.ljust(FRAME * 2, b"\0"))
                if len(processed) != FRAME * 2:
                    raise ValueError("Cancelador de eco retornou um bloco inválido.")
                drop = min(skip, FRAME)
                processed, skip = processed[drop * 2:], skip - drop
                take = min(len(processed) // 2, len(pending) // 2, total - written)
                if not take:
                    if not count and not pending:
                        raise ValueError("Faixa de microfone foi truncada.")
                    continue
                values = np.frombuffer(processed[:take * 2], "<i2").copy()
                passthrough = np.frombuffer(bytes(masks[:take]), dtype=np.uint8).astype(bool)
                original = np.frombuffer(bytes(pending[:take * 2]), "<i2")
                values[passthrough] = original[passthrough]
                output.writeframesraw(values.tobytes())
                processed_energy += float(np.dot(values.astype(float), values.astype(float)))
                del pending[:take * 2]
                del masks[:take]
                written += take
                if written % RATE < FRAME or written == total:
                    report("Separando microfone e áudio do computador", written / total)
        cancellation.check()
        os.replace(temporary, destination)
        processing = {"mode": "source-separated-webrtc-aec", "version": VERSION,
                      "sample_rate": RATE, "frames": written, "delay_compensation_samples": DELAY,
                      "microphone_level_change_db": (10 * math.log10(max(processed_energy, 1e-12) /
                                                         max(microphone_energy, 1e-12)))}
        atomic_json(manifest, {"signature": signature, "output_sha256": _hash(destination, cancellation),
                               "processing": processing})
        return {"sources": {"microphone": destination, "computer": computer},
                "processing": processing, "warnings": []}
    except ProcessingCancelled:
        raise
    except (OSError, RuntimeError, ValueError):
        return {"sources": {}, "processing": {"mode": "mixed", "reason": "aec-failed"},
                "warnings": ["Não foi possível reduzir o eco; usando o áudio misturado preservado."]}
    finally:
        temporary.unlink(missing_ok=True)
