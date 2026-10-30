$ErrorActionPreference = "Stop"
$PaceRoot = Split-Path $PSScriptRoot -Parent
$env:PYTHONPATH = Join-Path $PaceRoot "src"
& (Join-Path $PaceRoot ".venv\Scripts\python.exe") -m unittest discover -s (Join-Path $PaceRoot "tests") -v
if ($LASTEXITCODE -ne 0) { throw "Tests failed" }
& (Join-Path $PaceRoot ".venv\Scripts\python.exe") -m compileall -q (Join-Path $PaceRoot "src") (Join-Path $PaceRoot "main.py")
if ($LASTEXITCODE -ne 0) { throw "Compilation failed" }
