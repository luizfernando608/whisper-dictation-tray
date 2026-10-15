"""Recoverable transcription/diarization worker; never inserts text into focus."""
from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
import wave
from pathlib import Path

from whisper_dictation import secrets_store
from whisper_dictation.diarization import (
    ProcessingCancelled, atomic_json, diarize, model_root, _hash,
)
from whisper_dictation.meeting_sources import SPEECH_PACKING, pack_speech, prepare_sources, restore_speech
from whisper_dictation.settings import AppConfig, load_env_file


class _FileCancellation:
    def __init__(self, path: Path):
        self.path = path

    def is_set(self) -> bool:
        return self.path.exists()

    def check(self) -> None:
        if self.is_set():
            raise ProcessingCancelled()


def _get(value, name, default=None):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def _clean(text: str) -> str:
    return re.sub(r"\s+([,.;:!?])", r"\1", re.sub(r"\s+", " ", text).strip())


def merge_transcript(transcript: dict, turns: list[dict]) -> list[dict]:
    """Associate genuine word timestamps by overlap, leaving ambiguity unassigned.

    The regular overlapping turns are stored separately in result.json. No voice
    source is assumed to contain only one person, and gaps never borrow the
    nearest person's identity.
    """
    words = []
    for segment in transcript.get("segments", []):
        if segment.get("words"):
            words.extend({"start": word["start"], "end": word["end"], "text": word["word"]}
                         for word in segment["words"])
        else:
            words.append({"start": segment["start"], "end": segment["end"], "text": segment["text"]})
    result = []
    # The overlap cursor must see one chronological timeline, including words
    # from interleaved local/legacy segments.
    words.sort(key=lambda word: float(word["start"]))
    ordered = sorted(turns, key=lambda turn: turn["start"])
    cursor = 0
    for word in words:
        start, end = float(word["start"]), float(word["end"])
        if not math.isfinite(start) or not math.isfinite(end) or end < start:
            raise ValueError("Transcrição contém timestamps inválidos.")
        while cursor < len(ordered) and ordered[cursor]["end"] <= start:
            cursor += 1
        scores = {}
        for index in range(cursor, len(ordered)):
            turn = ordered[index]
            if turn["start"] >= end:
                break
            overlap = max(0, min(end, turn["end"]) - max(start, turn["start"]))
            scores[turn["speaker"]] = scores.get(turn["speaker"], 0.0) + overlap
        ranked = sorted(scores.items(), key=lambda pair: pair[1], reverse=True)
        speaker = None
        if ranked and ranked[0][1] > 0:
            if len(ranked) == 1 or ranked[0][1] > ranked[1][1] * 1.5:
                speaker = ranked[0][0]
        text = str(word["text"]).strip()
        if not text:
            continue
        if result and result[-1]["speaker"] == speaker and start - result[-1]["end"] <= 1.0:
            result[-1]["end"] = max(result[-1]["end"], end)
            result[-1]["_parts"].append(text)
        else:
            result.append({"start": start, "end": end, "_parts": [text], "speaker": speaker})
    for segment in result:
        segment["text"] = _clean(" ".join(segment.pop("_parts")))
    return result


def _chunks(audio: Path, directory: Path, cancellation: _FileCancellation, seconds: int = 300):
    """Decode bounded chunks with PyAV, also accepting large RF64 WAV files."""
    import av

    size = seconds * 16000
    buffer = bytearray()
    index, sample_offset = 0, 0
    with av.open(str(audio)) as container:
        streams = list(container.streams.audio)
        if not streams:
            raise ValueError("Arquivo não contém faixa de áudio.")
        resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
        def normalized():
            for frame in container.decode(streams[0]):
                cancellation.check()
                yield from resampler.resample(frame)
            yield from resampler.resample(None)
        for frame in normalized():
            buffer.extend(frame.to_ndarray().astype("<i2", copy=False).tobytes())
            while len(buffer) >= size * 2:
                cancellation.check()
                data = bytes(buffer[:size * 2])
                del buffer[:size * 2]
                path = directory / f"chunk-{index:05d}.wav"
                _write_chunk(path, data)
                yield index, sample_offset / 16000, size / 16000, path
                path.unlink(missing_ok=True)
                sample_offset += size
                index += 1
        if buffer:
            cancellation.check()
            path = directory / f"chunk-{index:05d}.wav"
            _write_chunk(path, bytes(buffer))
            yield index, sample_offset / 16000, len(buffer) / 32000, path
            path.unlink(missing_ok=True)


def _write_chunk(path: Path, data: bytes):
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(data)


def _effective_provider(config) -> tuple[str, str, str]:
    provider = config.transcription_provider
    warning = ""
    if provider == "groq":
        return provider, config.groq_model, warning
    if provider == "openai" and config.openai_model == "whisper-1":
        return provider, "whisper-1", warning
    if provider == "gemini" or provider == "openai":
        fallback = getattr(config, "meeting_timestamp_provider", "local")
        warning = (
            "Modelo configurado não oferece timestamps de palavras compatíveis; "
            f"reunião usa a alternativa selecionada nas configurações: {fallback}."
        )
        if fallback == "whisper-1":
            return "openai", "whisper-1", warning
        if fallback != "local":
            raise ValueError("Alternativa de timestamps inválida; escolha local ou whisper-1.")
    return "local", config.model_size, warning


def _transcribe_chunk(path: Path, duration: float, config, provider: str, model_name: str,
                      clients: dict, root: Path) -> dict:
    language = config.language if config.language not in {"", "auto"} else None
    if provider == "local":
        if "local" not in clients:
            from faster_whisper import WhisperModel
            try:
                asr_manifest = root / "asr-prepared.json"
                if not asr_manifest.exists():
                    asr_manifest = root / "prepared.json"
                prepared = json.loads(asr_manifest.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise RuntimeError("Prepare os modelos locais em Configurações antes de transcrever.") from exc
            if prepared.get("asr_model") != model_name or not prepared.get("asr_path"):
                raise RuntimeError("O modelo Whisper selecionado ainda não foi preparado. Use Preparar modelos.")
            clients["local"] = WhisperModel(
                prepared["asr_path"], device="cpu", compute_type=config.compute_type,
                cpu_threads=max(1, config.cpu_threads or 4), local_files_only=True,
            )
        segments, info = clients["local"].transcribe(
            str(path), language=language, word_timestamps=True, beam_size=config.beam_size,
            vad_filter=True, vad_parameters={"min_silence_duration_ms": config.vad_min_silence_ms},
            condition_on_previous_text=False,
        )
        collected = []
        for segment in segments:
            collected.append({"start": float(segment.start), "end": float(segment.end), "text": segment.text.strip(),
                              "words": [{"start": float(word.start), "end": float(word.end), "word": word.word}
                                        for word in (segment.words or [])]})
        return {"text": _clean(" ".join(s["text"] for s in collected)), "segments": collected,
                "language": info.language}
    if provider not in clients:
        key = secrets_store.get_api_key(provider, config.groq_api_key_env if provider == "groq" else None)
        if not key:
            raise RuntimeError(f"Chave de API de {provider} não configurada.")
        if provider == "groq":
            from groq import Groq
            clients[provider] = Groq(api_key=key, max_retries=2)
        else:
            from openai import OpenAI
            clients[provider] = OpenAI(api_key=key, max_retries=2)
    request = {"model": model_name, "response_format": "verbose_json",
               "timestamp_granularities": ["word", "segment"], "temperature": 0.0}
    if language:
        request["language"] = language
    with path.open("rb") as stream:
        response = clients[provider].audio.transcriptions.create(
            file=stream, timeout=max(config.groq_timeout_seconds, 60.0 + duration * 0.15), **request,
        )
    # Preserve the SDK response before interpreting its timeline. Projecting a
    # global word into every matching segment duplicates it when segments overlap
    # and drops words that fall outside the provider's segment boundaries.
    words = [{"start": _get(word, "start"), "end": _get(word, "end"), "word": str(_get(word, "word", ""))}
             for word in (_get(response, "words", []) or [])]
    segments = [{"start": _get(segment, "start"), "end": _get(segment, "end"), "text": str(_get(segment, "text", "")), "words": []}
                for segment in (_get(response, "segments", []) or [])]
    text = _clean(str(_get(response, "text", "") or ""))
    if text and not segments and not words:
        raise RuntimeError(f"{provider}/{model_name} não retornou timestamps. Escolha uma alternativa compatível.")
    return {"text": text, "segments": segments, "words": words,
            "language": str(_get(response, "language", language or "auto"))}


def _normalise_transcript(transcript: dict, duration: float) -> dict:
    """Use each genuine word once, stable in time; never invent word alignment."""
    if not isinstance(transcript, dict) or not isinstance(transcript.get("segments"), list):
        raise ValueError("Formato de transcrição inválido.")
    if any(not isinstance(segment, dict) for segment in transcript["segments"]):
        raise ValueError("Formato de segmento inválido.")

    def interval(item):
        try:
            start, end = float(item["start"]), float(item["end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Provedor retornou timestamps inválidos.") from exc
        if not (math.isfinite(start) and math.isfinite(end) and 0 <= start <= end <= duration + .5):
            raise ValueError("Provedor retornou timestamps fora da faixa de áudio.")
        return start, end

    global_words = transcript.get("words") or []
    words, segments = [], []
    sources = [global_words] if global_words else [segment.get("words") or [] for segment in transcript["segments"]]
    for source in sources:
        if not isinstance(source, list):
            raise ValueError("Formato de palavras inválido.")
        for word in source:
            start, end = interval(word)
            words.append({**word, "start": start, "end": end})
    # Sorting by start only keeps provider order for equal timestamps, including
    # legitimate identical repeated words. No deduplication or time clamping.
    words.sort(key=lambda word: word["start"])
    coarse = []
    for segment in transcript["segments"]:
        if not global_words and segment.get("words"):
            continue
        start, end = interval(segment)
        if global_words and any(
            min(end, word["end"]) > max(start, word["start"]) or
            (word["start"] == word["end"] and start <= word["start"] <= end)
            for word in words
        ):
            continue
        coarse.append({**segment, "start": start, "end": end, "words": []})
    if words and not coarse:
        text = _clean(" ".join(str(word["word"]) for word in words))
        segments = [{"start": words[0]["start"], "end": max(word["end"] for word in words), "text": text, "words": words}]
    else:
        # Mixed legacy/local results may contain coarse segments without words.
        # Keep each coarse block intact, interleaving it with genuinely timed words.
        segments = [{"start": word["start"], "end": word["end"], "text": str(word["word"]), "words": [word]} for word in words]
        segments.extend(coarse)
        segments.sort(key=lambda segment: segment["start"])
    return {"text": _clean(" ".join(segment["text"] for segment in segments)), "segments": segments,
            "language": transcript.get("language", "auto")}


def _validate_transcript(transcript: dict, duration: float) -> None:
    if not isinstance(transcript.get("segments"), list):
        raise ValueError("Formato de transcrição inválido.")
    previous = -1.0
    previous_word = -1.0
    for segment in transcript["segments"]:
        for item in [segment] + segment.get("words", []):
            start, end = float(item["start"]), float(item["end"])
            if not (math.isfinite(start) and math.isfinite(end) and 0 <= start <= end <= duration + 0.5):
                raise ValueError("Provedor retornou timestamps fora da faixa de áudio.")
        if segment["start"] < previous:
            raise ValueError("Provedor retornou segmentos fora de ordem.")
        for word in segment.get("words", []):
            if word["start"] < previous_word:
                raise ValueError("Provedor retornou palavras fora de ordem.")
            previous_word = word["start"]
        previous = segment["start"]


def transcribe_recording(audio: Path, directory: Path, config, cancellation: _FileCancellation,
                         progress=None, *, partial_callback=None, speech_only=False) -> dict:
    provider, model_name, warning = _effective_provider(config)
    signature = {"schema": 1, "audio_sha256": _hash(audio),
                 "provider": provider, "model": model_name, "language": config.language,
                 "beam": config.beam_size, "vad": config.vad_min_silence_ms, "chunk_seconds": 300}
    if speech_only:
        signature["speech_only"] = SPEECH_PACKING
    fingerprint = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()
    directory.mkdir(exist_ok=True)
    clients, collected = {}, []
    language, total_duration = config.language, 0.0

    def restore_raw(saved, length):
        if not speech_only:
            return _normalise_transcript(saved["transcript"], length)
        packed_duration = float(saved["packed_duration"])
        if not math.isfinite(packed_duration) or not 0 <= packed_duration <= length:
            raise ValueError("Duração de fala compactada inválida.")
        packed = _normalise_transcript(saved["transcript"], packed_duration)
        _validate_transcript(packed, packed_duration)
        restored = restore_speech(packed, saved["speech_map"])
        return _normalise_transcript(restored, length)

    try:
        for index, offset, length, path in _chunks(audio, directory, cancellation):
            checkpoint = directory / f"transcript-{index:05d}.json"
            raw_checkpoint = directory / f"transcript-{index:05d}.raw.json"
            partial = None
            # Prefer the original response so changes to normalization can be
            # replayed locally. Legacy validated checkpoints remain reusable.
            for candidate in (raw_checkpoint, checkpoint):
                if not candidate.exists():
                    continue
                try:
                    saved = json.loads(candidate.read_text(encoding="utf-8"))
                    if isinstance(saved, dict) and saved.get("fingerprint") == fingerprint:
                        restored = (restore_raw(saved, length) if candidate == raw_checkpoint else
                                    _normalise_transcript(saved["transcript"], length))
                        _validate_transcript(restored, length)
                        partial = restored
                        break
                except (ValueError, OSError, KeyError, TypeError):
                    pass
            if partial is None:
                cancellation.check()
                saved = {"fingerprint": fingerprint}
                if speech_only:
                    destination = directory / f"speech-{index:05d}.wav"
                    try:
                        packed = pack_speech(path, destination, cancellation)
                        cancellation.check()
                        packed_duration = float(packed["duration"])
                        if packed_duration > 0:
                            raw = _transcribe_chunk(Path(packed["audio"]), packed_duration, config, provider,
                                                    model_name, clients, model_root(config))
                        else:
                            raw = {"text": "", "segments": [], "language": language}
                        saved.update(transcript=raw, speech_map=packed["chunks"], packed_duration=packed_duration)
                    finally:
                        destination.unlink(missing_ok=True)
                else:
                    saved["transcript"] = _transcribe_chunk(path, length, config, provider, model_name,
                                                             clients, model_root(config))
                atomic_json(raw_checkpoint, saved)
                partial = restore_raw(saved, length)
                _validate_transcript(partial, length)
            atomic_json(checkpoint, {"fingerprint": fingerprint, "transcript": partial})
            language = partial.get("language", language)
            for segment in partial["segments"]:
                adjusted = {**segment, "start": segment["start"] + offset, "end": segment["end"] + offset,
                            "words": [{**word, "start": word["start"] + offset, "end": word["end"] + offset}
                                      for word in segment.get("words", [])]}
                collected.append(adjusted)
            total_duration = offset + length
            partial_result = {"text": _clean(" ".join(s["text"] for s in collected)), "segments": collected,
                              "provider": provider, "model": model_name, "warning": warning,
                              "duration": total_duration, "language": language, "partial": True}
            atomic_json(directory.parent / "transcript.partial.json", partial_result)
            if partial_callback:
                partial_callback(partial_result)
            if progress:
                progress(total_duration)
            cancellation.check()
    finally:
        for client in clients.values():
            close = getattr(client, "close", None)
            if close:
                close()
    if total_duration <= 0:
        raise ValueError("Arquivo não contém amostras de áudio decodificáveis.")
    return {"text": _clean(" ".join(s["text"] for s in collected)), "segments": collected,
            "provider": provider, "model": model_name, "warning": warning,
            "duration": total_duration, "language": language}


def _combined_transcript(transcripts: dict[str, dict]) -> dict:
    """Keep source timelines aligned without removing simultaneous words."""
    first = next(iter(transcripts.values()))
    pieces = []
    for source, transcript in transcripts.items():
        for segment in transcript["segments"]:
            if segment.get("words"):
                pieces.extend({"start": word["start"], "end": word["end"], "text": word["word"],
                               "words": [word], "source": source} for word in segment["words"])
            else:
                pieces.append({**segment, "source": source})
    segments = sorted(pieces, key=lambda segment: segment["start"])
    warnings = list(dict.fromkeys(transcript["warning"] for transcript in transcripts.values()
                                 if transcript.get("warning")))
    return {"text": _clean(" ".join(segment["text"] for segment in segments)), "segments": segments,
            "provider": first["provider"], "model": first["model"], "language": first["language"],
            "duration": max(transcript["duration"] for transcript in transcripts.values()),
            "warning": "; ".join(warnings)}


def _process_sources(prepared: dict, directory: Path, record: dict, config, cancellation,
                     store, recording_id: str) -> dict:
    transcripts, diarizations = {}, {}
    sources = prepared["sources"]
    names = [name for name in ("microphone", "computer") if name in sources]
    if len(names) != 2:
        raise ValueError("Preparação de fontes não retornou as duas faixas verificadas.")
    warnings = list(prepared.get("warnings", []))
    progress_value = 0.0

    def update_progress(value, stage):
        nonlocal progress_value
        progress_value = max(progress_value, min(.95, max(0.0, value)))
        store.update(recording_id, status="processing", progress=progress_value, stage=stage,
                     partial_result_path=str(directory / "transcript.partial.json"))

    def save_partial(source, partial):
        combined = _combined_transcript({**transcripts, source: partial})
        combined["partial"] = True
        atomic_json(directory / "transcript.partial.json", combined)

    for index, name in enumerate(names):
        cancellation.check()
        source_directory = directory / "sources" / name
        source_directory.mkdir(parents=True, exist_ok=True)
        label = "Microfone" if name == "microphone" else "Computador"
        base, width = index * .95 / len(names), .95 / len(names)
        duration = float(record.get("duration") or 0)

        def transcript_progress(seconds):
            fraction = seconds / max(seconds, duration, .001)
            update_progress(base + width * .7 * fraction, f"Transcrevendo · {label}")

        update_progress(base, f"Transcrevendo · {label}")
        transcript = transcribe_recording(
            Path(sources[name]), source_directory / "transcription-checkpoints", config, cancellation,
            transcript_progress, partial_callback=lambda partial: save_partial(name, partial),
            speech_only=name == "microphone",
        )
        cancellation.check()
        transcripts[name] = transcript
        atomic_json(source_directory / "transcript.json", transcript)
        save_partial(name, transcript)
        update_progress(base + width * .7, f"Identificando falantes · {label}")

        def diarization_progress(stage, fraction):
            update_progress(base + width * (.7 + .3 * min(1.0, max(0.0, fraction))), f"{stage} · {label}")

        diarizations[name] = diarize(Path(sources[name]), config, source_directory,
                                    diarization_progress, cancellation.is_set)
        cancellation.check()
        update_progress(base + width, f"Identificando falantes · {label}")

    # Namespaces are independent even when two channels use the same model label.
    # Order public IDs by the earliest real turn, not the channel's label number.
    identities = {}
    for name in names:
        for turn in diarizations[name]["turns"]:
            key = (name, turn["speaker"])
            identities[key] = min(identities.get(key, float("inf")), turn["start"])
    speaker_map = {key: f"SPEAKER_{index:02d}" for index, key in
                   enumerate(sorted(identities, key=identities.get))}
    segments, turns, exclusive = [], [], []
    reasons = [record["fallback_reason"]] if record.get("fallback_reason") else []
    backends = {}
    for name in names:
        result = diarizations[name]
        # Match words only against their own source's voices. A combined overlap
        # scorer would incorrectly turn valid cross-source speech into ambiguity.
        for segment in merge_transcript(transcripts[name], result["turns"]):
            local = segment["speaker"]
            segments.append({**segment, "speaker": speaker_map[(name, local)] if local is not None else None,
                             "source": name})
        for key, target in (("turns", turns), ("exclusive_turns", exclusive)):
            target.extend({**turn, "speaker": speaker_map[(name, turn["speaker"])], "source": name}
                          for turn in result.get(key, []))
        backends[name] = {key: value for key, value in result.items() if key not in {"turns", "exclusive_turns"}}
        reasons.extend(f"{name}: {reason}" for reason in result.get("fallback_reasons", []))
    for sequence in (segments, turns, exclusive):
        sequence.sort(key=lambda item: item["start"])
    combined = _combined_transcript(transcripts)
    if combined["warning"]:
        warnings.append(combined["warning"])
    warnings = list(dict.fromkeys(warnings))
    combined["warning"] = "; ".join(warnings)
    combined["processing"] = prepared.get("processing", {})
    atomic_json(directory / "transcript.json", combined)
    first_result = diarizations[names[0]]
    backend_names = list(dict.fromkeys(result["backend"] for result in diarizations.values()))
    return {"schema": 1, "text": _clean(" ".join(segment["text"] for segment in segments)),
            "segments": segments, "turns": turns, "exclusive_turns": exclusive,
            "speaker_sources": {speaker: source for (source, _), speaker in speaker_map.items()},
            "processing": {**prepared.get("processing", {}), "mode": "separate_sources",
                           "source_order": names, "warning_count": len(warnings)},
            "source_backends": backends, "backend": "; ".join(backend_names),
            "fallback_reasons": reasons, "warnings": warnings, "duration": combined["duration"],
            "language": combined["language"], "transcription_provider": combined["provider"],
            "transcription_model": combined["model"], "transcription_warning": combined["warning"],
            "diarization_model": first_result.get("model", ""), "license": first_result.get("license", "")}


def _write_result(directory: Path, record: dict, output: dict, store) -> None:
    """Do not attach names to a different voice after a model/mode change."""
    names = record.get("speaker_names") or {}
    if names:
        try:
            previous = json.loads((directory / "result.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previous = None
        unchanged = (isinstance(previous, dict) and previous.get("turns") == output.get("turns") and
                     previous.get("speaker_sources") == output.get("speaker_sources"))
        if not unchanged:
            atomic_json(directory / f"speaker-names-{uuid.uuid4().hex}.json",
                        {"speaker_names": names, "result": previous})
            store.update(record["id"], speaker_names={})
    atomic_json(directory / "result.json", output)


def process_recording(store_root, recording_id: str, config_path) -> dict | None:
    """Subprocess entry point. Ready means both ASR and full diarization finished."""
    from whisper_dictation.recording_store import RecordingStore

    store = RecordingStore(Path(store_root))
    directory = store.directory(recording_id)
    cancellation = _FileCancellation(directory / "cancel.request")
    try:
        cancellation.check()
        config_path = Path(config_path)
        load_env_file(config_path.parent)
        config = AppConfig.load(config_path)
        record = store.get(recording_id)
        if record.get("backend_override"):
            config.diarization_backend = str(record["backend_override"])
        audio = Path(record.get("audio_path") or directory / "audio.wav")
        if not audio.is_file() and record.get("source_path"):
            from whisper_dictation.media import decode_media
            store.update(recording_id, status="transcribing", progress=0.0, error="", stage="Extraindo áudio")
            audio = directory / "audio.wav"
            duration = decode_media(record["source_path"], audio, cancel_event=cancellation)
            store.update(recording_id, audio_path=str(audio), duration=duration)
        if not audio.is_file():
            raise RuntimeError("Áudio da gravação não foi encontrado.")
        guided_count = any(int(record.get(key) if record.get(key) is not None else
                               getattr(config, "diarization_" + key, 0) or 0) > 0
                           for key in ("num_speakers", "min_speakers", "max_speakers"))
        source_warning = ""
        source_processing, source_warnings = None, []
        if record.get("origin") == "meeting" and guided_count:
            source_warning = ("Quantidade de falantes definida: processamento usa o áudio misturado, "
                              "pois esse total não pode ser aplicado independentemente a cada fonte.")
        if record.get("origin") == "meeting" and not guided_count:
            store.update(recording_id, status="processing", progress=0.0, error="", stage="Preparando fontes de áudio")
            prepared = prepare_sources(audio, directory, cancellation,
                lambda message, fraction: store.update(recording_id, stage=f"{message} ({fraction:.0%})"))
            cancellation.check()
            if prepared is not None and prepared.get("sources"):
                output = _process_sources(prepared, directory, record, config, cancellation, store, recording_id)
                cancellation.check()
                _write_result(directory, record, output, store)
                store.update(recording_id, status="ready", progress=1.0, error="", stage="Concluído",
                             duration=output["duration"], result_path=str(directory / "result.json"),
                             transcription_warning=output["transcription_warning"], backend=output["backend"],
                             fallback_reason="; ".join(output["fallback_reasons"]))
                return output
            if prepared is not None:
                source_processing = prepared.get("processing", {})
                source_warnings = prepared.get("warnings", [])
                source_warning = "; ".join(source_warnings)
        store.update(recording_id, status="transcribing", progress=0.0, error="", stage="Transcrevendo")
        def transcript_progress(seconds):
            duration = float(store.get(recording_id).get("duration") or seconds)
            store.update(recording_id, progress=min(0.65, 0.65 * seconds / max(seconds, duration)),
                         stage="Transcrevendo", partial_result_path=str(directory / "transcript.partial.json"))
        transcript = transcribe_recording(audio, directory / "transcription-checkpoints", config,
                                         cancellation, transcript_progress)
        if source_warning:
            transcript["warning"] = "; ".join(filter(None, (transcript.get("warning", ""), source_warning)))
        cancellation.check()
        atomic_json(directory / "transcript.json", transcript)
        store.update(recording_id, status="diarizing", progress=0.65, stage="Identificando falantes",
                     transcription_warning=transcript.get("warning", ""))
        def diarization_progress(stage, fraction):
            store.update(recording_id, progress=0.65 + min(1.0, max(0.0, fraction)) * 0.3, stage=stage)
        result = diarize(audio, config, directory, diarization_progress, cancellation.is_set,
                         num_speakers=record.get("num_speakers"), min_speakers=record.get("min_speakers"),
                         max_speakers=record.get("max_speakers"))
        cancellation.check()
        segments = merge_transcript(transcript, result["turns"])
        reasons = ([record["fallback_reason"]] if record.get("fallback_reason") else []) + result.get("fallback_reasons", [])
        output = {"schema": 1, "text": transcript["text"], "segments": segments, "turns": result["turns"],
                  "exclusive_turns": result.get("exclusive_turns", []), "backend": result["backend"],
                  "fallback_reasons": reasons, "duration": transcript["duration"],
                  "language": transcript["language"], "transcription_provider": transcript["provider"],
                  "transcription_model": transcript["model"], "transcription_warning": transcript.get("warning", ""),
                  "diarization_model": result.get("model", ""), "license": result.get("license", "")}
        if source_processing is not None:
            output["processing"] = source_processing
            output["warnings"] = source_warnings
        for key in ("device_log", "benchmark_seconds"):
            if key in result:
                output[key] = result[key]
        _write_result(directory, record, output, store)
        store.update(recording_id, status="ready", progress=1.0, error="", stage="Concluído",
                     duration=transcript["duration"], result_path=str(directory / "result.json"),
                     backend=result["backend"], fallback_reason="; ".join(reasons))
        return output
    except ProcessingCancelled:
        store.update(recording_id, status="cancelled", stage="Cancelado", error="")
        return None
    except Exception as exc:
        if cancellation.is_set():
            store.update(recording_id, status="cancelled", stage="Cancelado", error="")
        else:
            store.update(recording_id, status="failed", stage="Falha", error=f"{type(exc).__name__}: {exc}")
        return None
