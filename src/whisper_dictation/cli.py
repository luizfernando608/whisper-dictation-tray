"""pace-cli: transcrição e identificação de falantes do PACE para o terminal e agentes de IA.

Resultado no stdout; progresso e erros no stderr. Códigos de saída:
0 ok · 1 erro · 2 uso inválido · 3 trabalho falhou · 4 ainda processando após --timeout · 5 cancelado.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from whisper_dictation import agent_jobs
from whisper_dictation.agent_jobs import Context, JobError

EPILOG = """exemplos:
  pace-cli transcribe "C:\\Users\\Ana\\Downloads\\reuniao.mp4"
  pace-cli transcribe audio.ogg --speakers 2 --language pt
  pace-cli transcribe aula.m4a --no-wait          (devolve o id na hora)
  pace-cli wait <id> --timeout 600
  pace-cli result <id> --format srt --output legenda.srt
  pace-cli agents connect                         (registra o PACE no Claude, Codex, Cursor)

Formatos aceitos: wav mp3 m4a ogg opus flac aac mp4 mkv webm mov e outros com áudio.
A transcrição usa a chave do Groq configurada no PACE (ou --local, offline) e identifica
os falantes localmente. Cada trabalho aparece na biblioteca de Gravações do PACE.
"""


def _formats(parser: argparse.ArgumentParser, default: str = "text") -> None:
    parser.add_argument("--format", choices=("text", "json", "txt", "srt"), default=default,
                        help="text: falas por falante com horário (padrão, ideal para agentes); json: estruturado; "
                             "txt/srt: exportação igual à do app")
    parser.add_argument("--json", action="store_const", const="json", dest="format", help="atalho para --format json")
    parser.add_argument("--output", "-o", type=Path, help="grava o resultado neste arquivo (UTF-8) em vez de imprimir")


def build_parser() -> argparse.ArgumentParser:
    from whisper_dictation._version import __version__
    parser = argparse.ArgumentParser(prog="pace-cli", description=__doc__.split("\n\n")[0],
                                     epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"PACE {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="comando")

    transcribe = commands.add_parser("transcribe", help="transcreve e identifica falantes de um áudio ou vídeo")
    transcribe.add_argument("file", help="caminho do arquivo de áudio ou vídeo")
    transcribe.add_argument("--speakers", type=int, help="número exato de falantes, se souber")
    transcribe.add_argument("--min-speakers", type=int)
    transcribe.add_argument("--max-speakers", type=int)
    transcribe.add_argument("--language", help="idioma (ex.: pt, en); padrão: o configurado no PACE")
    transcribe.add_argument("--local", action="store_true", help="transcreve offline, sem enviar o áudio para a nuvem")
    transcribe.add_argument("--title", help="nome na biblioteca do PACE (padrão: nome do arquivo)")
    transcribe.add_argument("--force", action="store_true", help="reprocessa mesmo se já houver resultado deste arquivo")
    transcribe.add_argument("--no-wait", action="store_true", help="só inicia e devolve o id")
    transcribe.add_argument("--timeout", type=float, help="segundos máximos de espera; o trabalho continua depois (código 4)")
    transcribe.add_argument("--quiet", "-q", action="store_true", help="sem mensagens de progresso")
    _formats(transcribe)

    wait = commands.add_parser("wait", help="espera um trabalho terminar e mostra o resultado")
    wait.add_argument("id")
    wait.add_argument("--timeout", type=float)
    wait.add_argument("--quiet", "-q", action="store_true")
    _formats(wait)

    result = commands.add_parser("result", help="mostra o resultado de um trabalho concluído")
    result.add_argument("id")
    _formats(result)

    status = commands.add_parser("status", help="estado e progresso de um trabalho")
    status.add_argument("id")
    status.add_argument("--json", action="store_true")

    listing = commands.add_parser("list", help="lista as transcrições recentes da biblioteca")
    listing.add_argument("--limit", type=int, default=20)
    listing.add_argument("--json", action="store_true")

    cancel = commands.add_parser("cancel", help="cancela um trabalho em andamento")
    cancel.add_argument("id")

    doctor = commands.add_parser("doctor", help="diagnóstico: versão, chave configurada (sem mostrar), agentes")
    doctor.add_argument("--json", action="store_true")

    agents = commands.add_parser("agents", help="conecta o PACE aos agentes de IA instalados")
    agents.add_argument("action", choices=("status", "connect", "disconnect"))
    agents.add_argument("--target", action="append", help="claude-code, claude-desktop, codex ou cursor (padrão: todos detectados)")
    agents.add_argument("--all", action="store_true", help="em disconnect, remove de todos")
    agents.add_argument("--json", action="store_true")
    agents.add_argument("--quiet", "-q", action="store_true")

    commands.add_parser("mcp", help="servidor MCP em stdio (usado pelos agentes; não rode à mão)")
    return parser


def _utf8_streams() -> None:
    # Agents read pipes as UTF-8; a real console keeps Windows' Unicode output.
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None and not stream.isatty():
                stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass


def _emit(text: str, output: Path | None) -> None:
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
        print(f"Resultado salvo em {output.resolve()}", file=sys.stderr)
    else:
        print(text)


def _duration(seconds) -> str:
    from whisper_dictation.transcript_format import timestamp
    return timestamp(float(seconds or 0))


def render(record: dict, data: dict, fmt: str) -> str:
    from whisper_dictation.transcript_format import agent_text, transcript_text
    names = record.get("speaker_names") or {}
    if fmt == "json":
        return json.dumps(agent_jobs.payload(record, data), ensure_ascii=False, indent=2)
    if fmt in {"txt", "srt"}:
        return transcript_text(data, names, srt=fmt == "srt")
    view = agent_jobs.payload(record, data)
    header = (f"# PACE · {record.get('title')} · id {record['id']} · duração {_duration(view['duration_seconds'])} · "
              f"{len(view['speakers'])} falante(s) · idioma {view.get('language') or '?'}")
    warnings = "".join(f"\n# aviso: {warning}" for warning in view["warnings"])
    return f"{header}{warnings}\n{agent_text(data, names)}"


def _progress_printer(quiet: bool):
    def show(record: dict) -> None:
        if not quiet:
            percent = round(float(record.get("progress") or 0) * 100)
            print(f"PACE: {record.get('stage') or record['status']} ({percent}%)", file=sys.stderr, flush=True)
    return show


def _finish(context: Context, record: dict, args) -> int:
    if record["status"] not in agent_jobs.DONE_STATUSES:
        view = agent_jobs.summary(record)
        if args.format == "json":
            print(json.dumps(view, ensure_ascii=False, indent=2))
        else:
            print(f"Ainda processando ({view['stage'] or view['status']}, {round(view['progress'] * 100)}%). "
                  f"id {record['id']}. Continue com: pace-cli wait {record['id']}")
        return 4
    record, data = agent_jobs.result(context, record["id"])
    _emit(render(record, data, args.format), args.output)
    return 0


def run(args, context: Context) -> int:
    command = args.command
    if command == "transcribe":
        record, reused = agent_jobs.start(context, args.file, speakers=args.speakers, min_speakers=args.min_speakers,
                                          max_speakers=args.max_speakers, language=args.language, local=args.local,
                                          force=args.force, title=args.title)
        if not args.quiet:
            note = "resultado já existente reaproveitado" if reused and record["status"] == "ready" else "trabalho iniciado"
            print(f"PACE: {note} · id {record['id']}", file=sys.stderr, flush=True)
        if args.no_wait:
            view = agent_jobs.summary(record)
            print(json.dumps(view, ensure_ascii=False, indent=2) if args.format == "json" else
                  f"id {record['id']} · {view['status']}. Acompanhe com: pace-cli wait {record['id']}")
            return 0
        record = agent_jobs.wait(context, record["id"], args.timeout, _progress_printer(args.quiet))
        return _finish(context, record, args)
    if command == "wait":
        record = agent_jobs.wait(context, args.id, args.timeout, _progress_printer(args.quiet))
        return _finish(context, record, args)
    if command == "result":
        record, data = agent_jobs.result(context, args.id)
        _emit(render(record, data, args.format), args.output)
        return 0
    if command == "status":
        view = agent_jobs.summary(agent_jobs.ensure_running(context, args.id))
        if args.json:
            print(json.dumps(view, ensure_ascii=False, indent=2))
        else:
            print(f"{view['id']} · {view['status']} · {view['stage']} · {round(view['progress'] * 100)}%"
                  + (f" · erro: {view['error']}" if view["error"] else ""))
        return 0
    if command == "list":
        rows = [agent_jobs.summary(row) for row in context.store().list()[:max(1, args.limit)]]
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
        else:
            for row in rows:
                print(f"{row['id']}  {row['status']:<11} {_duration(row['duration_seconds'])}  "
                      f"{(row['created_at'] or '')[:16]}  {row['title']}")
        return 0
    if command == "cancel":
        record = agent_jobs.cancel(context, args.id)
        print(f"{record['id']} · {record['status']}")
        return 0
    if command == "doctor":
        info = agent_jobs.doctor(context)
        if args.json:
            print(json.dumps(info, ensure_ascii=False, indent=2))
        else:
            for key, value in info.items():
                print(f"{key}: {json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value}")
        return 0
    if command == "agents":
        from whisper_dictation import agents
        if args.action == "status":
            report = agents.status(context.project_root)
        elif args.action == "connect":
            report = agents.connect(context.project_root, args.target)
        else:
            report = agents.disconnect(context.project_root, None if args.all else args.target)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        elif not args.quiet:
            print(agents.describe(report))
        return 0 if all(item.get("ok", True) for item in report.values()) else 1
    if command == "mcp":
        from whisper_dictation.mcp_server import serve
        return serve(context)
    raise JobError("Informe um comando. Veja pace-cli --help.", exit_code=2, code="usage")


def main(argv: list[str] | None = None, project_root: Path | None = None, code_root: Path | None = None) -> int:
    _utf8_streams()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2
    context = Context(Path(project_root) if project_root else Path(sys.executable).parent, code_root)
    try:
        return run(args, context)
    except JobError as error:
        if getattr(args, "format", None) == "json" or getattr(args, "json", False) is True:
            print(json.dumps({"ok": False, "error": error.as_dict()}, ensure_ascii=False), file=sys.stderr)
        else:
            print(f"Erro: {error.message}" + (f"\nDica: {error.hint}" if error.hint else ""), file=sys.stderr)
        return error.exit_code
    except KeyboardInterrupt:
        print("Interrompido. O trabalho continua no PACE; use pace-cli wait <id>.", file=sys.stderr)
        return 130
