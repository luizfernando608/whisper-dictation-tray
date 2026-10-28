"""Agent Skill installed by "Conectar aos agentes de IA" (Claude Code and Codex).

The MCP server tells agents *what* PACE can do; this skill tells them *when* to
use it and what to do with the transcript. ``{cli}`` and ``{version}`` are
filled in at install time so the CLI fallback always points at this install.
"""
from __future__ import annotations

SKILL_NAME = "pace-transcricao"
MARKER = "<!-- pace-managed-skill -->"

SKILL_TEMPLATE = """---
name: pace-transcricao
description: >
  Transcrever e identificar falantes (diarização) de áudio ou vídeo com o PACE instalado neste
  computador: reunião, call, entrevista, aula, palestra, vídeo, áudio do WhatsApp (.ogg/.opus).
  Use SEMPRE que houver uma gravação para entender, ou quando pedirem transcrição, "quem falou o quê",
  legenda/SRT, ata, resumo ou pontos principais de um áudio ou vídeo.
---
{marker}
# PACE — transcrição e falantes (v{version})

O PACE transcreve (Whisper via Groq, com a chave guardada no próprio PACE, ou offline) e identifica
os falantes localmente. Cada trabalho também aparece na biblioteca de Gravações do PACE.

## 1. Conseguir o arquivo
- Caminho dado pelo usuário: use-o (caminho completo).
- "O áudio que eu baixei": o arquivo de áudio/vídeo mais recente em `Downloads`; confirme o nome.
- Áudio do WhatsApp: peça para salvar (WhatsApp Desktop: passe o mouse no áudio → ▾ → Baixar/Salvar
  como) ou, se você controla o computador, faça isso; depois procure em `Downloads` (.ogg/.opus).
- Link de vídeo: baixe primeiro (por exemplo com yt-dlp, se existir) e transcreva o arquivo.

## 2. Transcrever
Prefira as ferramentas MCP do servidor `pace`:
- `transcribe_audio` com `path` (e `speakers` se souber quantas pessoas falam; `local_only` para sigilo).
- Se voltar um id ainda em processamento, chame `get_transcription` com o id até ficar pronto.

Sem MCP, use o terminal (o caminho tem espaços: mantenha as aspas):
```
"{cli}" transcribe "<arquivo>" [--speakers N] [--language pt] [--local]
"{cli}" transcribe "<arquivo>" --no-wait      # áudio longo: devolve o id
"{cli}" wait <id> --timeout 540
"{cli}" result <id> --format srt --output legenda.srt
```
No PowerShell, antes rode `[Console]::OutputEncoding = [Text.Encoding]::UTF8` para não estragar acentos.
Erros vêm com uma dica; `"{cli}" doctor` diz se a chave está configurada (sem mostrá-la).

## 3. Depois da transcrição
O resultado vem como `[hh:mm:ss] SPEAKER_00: texto`, uma linha por fala. `SPEAKER_00` é quem fala primeiro.
1. Troque os rótulos por nomes usando as pistas ("Bom dia, Ana" → quem responde é a Ana). Sem pista
   segura, use papéis ("Entrevistador", "Participante 2") e diga que é suposição.
2. Confira a diarização pelo sentido (quem pergunta e quem responde); se um "falante" responde a si mesmo,
   o rótulo está trocado ali — corrija e avise.
3. Corrija nomes próprios, siglas e jargões que o reconhecimento errou, pelo contexto.
4. Áudio curto (WhatsApp): responda no chat com a transcrição limpa e, se útil, 1–3 linhas do que a
   pessoa quer ou pede.
5. Gravação longa: salve ao lado do arquivo `<nome>_transcricao.md` (texto limpo, falantes nomeados,
   horários) e `<nome>_resumo.md`:
   Em poucas palavras · Pontos principais (com [mm:ss]) · Decisões · Próximos passos (o quê/quem/prazo)
   · Em aberto. Só o que foi dito; nada inventado. Responda no idioma da gravação.

## Chave e privacidade
A chave fica no cofre do PACE e nunca deve ser exibida. Sem chave: oriente a abrir PACE → Configurações
e colar a chave do Groq (console.groq.com/keys), ou use `local_only`/`--local`. Com a chave, o áudio vai
para a nuvem do Groq; para conteúdo sigiloso, use o modo local.
"""


def render(cli: str, version: str) -> str:
    return SKILL_TEMPLATE.format(cli=cli, version=version, marker=MARKER)
