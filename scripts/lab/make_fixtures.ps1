# Gera áudios de teste sem dados reais: um diálogo de duas vozes do Windows (WAV),
# e cópias em .ogg/opus (como áudio do WhatsApp) e .mp4/aac (como vídeo).
# Uso: scripts\lab\make_fixtures.ps1 -Output <pasta> [-Python <python com PyAV>]
param(
    [Parameter(Mandatory = $true)][string]$Output,
    [string]$Python
)
$ErrorActionPreference = "Stop"
if (-not $Python) { $Python = Join-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) ".venv\Scripts\python.exe" }
Add-Type -AssemblyName System.Speech
New-Item -ItemType Directory -Force $Output | Out-Null

$voices = (New-Object System.Speech.Synthesis.SpeechSynthesizer).GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name }
$first = $voices | Where-Object { $_ -match "Maria|Francisca|Daniel" } | Select-Object -First 1
if (-not $first) { $first = $voices[0] }
$second = $voices | Where-Object { $_ -ne $first } | Select-Object -First 1
if (-not $second) { $second = $first }

$lines = @(
    @($first,  "Bom dia, Ana. Vamos revisar o cronograma do projeto hoje."),
    @($second, "Bom dia. O relatório está pronto e o orçamento foi aprovado na sexta-feira."),
    @($first,  "Ótimo. Então a entrega fica marcada para o dia quinze de novembro."),
    @($second, "Combinado. Eu envio a versão final para o cliente amanhã de manhã."),
    @($first,  "Perfeito. Obrigada, até a próxima reunião.")
)
$parts = @()
for ($index = 0; $index -lt $lines.Count; $index++) {
    $speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer
    $speaker.SelectVoice($lines[$index][0])
    $part = Join-Path $Output ("part-{0:d2}.wav" -f $index)
    $format = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
    $speaker.SetOutputToWaveFile($part, $format)
    $speaker.Speak($lines[$index][1])
    $speaker.Dispose()
    $parts += $part
}

$script = @'
import sys, wave
from pathlib import Path
import av
output = Path(sys.argv[1]); parts = sys.argv[2:]
pause = b"\x00\x00" * 16000 * 1  # 1 s de silêncio entre falas
frames = b""
for part in parts:
    with wave.open(part, "rb") as source:
        frames += source.readframes(source.getnframes()) + pause
dialog = output / "dialogo.wav"
with wave.open(str(dialog), "wb") as target:
    target.setnchannels(1); target.setsampwidth(2); target.setframerate(16000); target.writeframes(frames)
for name, codec, rate in (("whatsapp.ogg", "libopus", 48000), ("video.mp4", "aac", 44100)):
    with av.open(str(dialog)) as source, av.open(str(output / name), "w") as target:
        stream = target.add_stream(codec, rate=rate); stream.layout = "mono"
        resampler = av.AudioResampler(format=stream.format.name if codec == "aac" else "s16", layout="mono", rate=rate)
        for frame in source.decode(audio=0):
            for converted in resampler.resample(frame):
                for packet in stream.encode(converted):
                    target.mux(packet)
        for packet in stream.encode(None):
            target.mux(packet)
for part in parts:
    Path(part).unlink()
print("\n".join(str(output / n) for n in ("dialogo.wav", "whatsapp.ogg", "video.mp4")))
'@
$scriptPath = Join-Path $env:TEMP "pace-make-fixtures.py"
Set-Content -Path $scriptPath -Value $script -Encoding utf8
& $Python -I $scriptPath $Output @parts
if ($LASTEXITCODE -ne 0) { throw "Falha ao gerar fixtures" }
Remove-Item $scriptPath
