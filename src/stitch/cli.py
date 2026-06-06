from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .bus import create_bus
from .config import init_project, load_config
from .orchestrator import run, run_saved_pipeline
from .paths import StitchPaths
from .pipeline_store import latest_report_path, list_pipelines, load_pipeline
from .sources import SourceRegistrationError, list_sources, register_source


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    paths = StitchPaths.discover()

    try:
        return args.func(args, paths)
    except (RuntimeError, FileNotFoundError, KeyError, SourceRegistrationError) as exc:
        print(f"stitch: {exc}", file=sys.stderr)
        return 1


def _cmd_init(args: argparse.Namespace, paths: StitchPaths) -> int:
    config = init_project(paths, redis_url=args.redis_url, weave_project=args.weave_project)
    print(f"Initialized Stitch at {paths.stitch_dir}")
    print(f"Redis: {config.redis_url or 'local file bus'}")
    print(f"Weave project: {config.weave_project or 'disabled'}")
    return 0


def _cmd_add(args: argparse.Namespace, paths: StitchPaths) -> int:
    if not paths.config_file.exists():
        init_project(paths)
    source = register_source(paths, args.source, label=args.label)
    print(f"Added {source.kind} source {source.id}: {source.label} ({source.uri})")
    return 0


def _cmd_sources(args: argparse.Namespace, paths: StitchPaths) -> int:
    for source in list_sources(paths):
        print(f"{source.id}\t{source.kind}\t{source.label}\t{source.uri}")
    return 0


def _cmd_run(args: argparse.Namespace, paths: StitchPaths) -> int:
    summary = run(
        goal=args.goal,
        paths=paths,
        name=args.name,
        prompt=not args.no_prompt,
        review=args.review,
    )
    _print_summary(summary)
    return 0 if summary.status == "complete" else 2


def _cmd_questions(args: argparse.Namespace, paths: StitchPaths) -> int:
    config = load_config(paths)
    bus = create_bus(paths, config.redis_url)
    status = None if args.all else "pending"
    questions = bus.list_questions(status=status)
    if not questions:
        print("No questions.")
        return 0
    for question in questions:
        suffix = f" answer={question.answer}" if question.answer else ""
        print(f"{question.id}\t{question.status}\t{question.agent}\t{question.question}{suffix}")
    return 0


def _cmd_answer(args: argparse.Namespace, paths: StitchPaths) -> int:
    config = load_config(paths)
    bus = create_bus(paths, config.redis_url)
    question = bus.answer_question(args.question_id, " ".join(args.answer))
    print(f"Answered {question.id}: {question.answer}")
    return 0


def _cmd_pipeline_ls(args: argparse.Namespace, paths: StitchPaths) -> int:
    for name in list_pipelines(paths):
        print(name)
    return 0


def _cmd_pipeline_show(args: argparse.Namespace, paths: StitchPaths) -> int:
    pipeline = load_pipeline(paths, args.name)
    print(pipeline.markdown or pipeline.yaml)
    return 0


def _cmd_pipeline_run(args: argparse.Namespace, paths: StitchPaths) -> int:
    summary = run_saved_pipeline(name=args.name, paths=paths)
    _print_summary(summary)
    return 0


def _cmd_report(args: argparse.Namespace, paths: StitchPaths) -> int:
    path = latest_report_path(paths)
    if path is None:
        raise FileNotFoundError("No report has been generated yet.")
    if args.path:
        print(path)
    else:
        print(path.read_text(encoding="utf-8"))
    return 0


def _print_summary(summary) -> None:
    print(f"Run {summary.run_id}: {summary.status}")
    print(f"Pipeline: {summary.pipeline_dir}")
    if summary.report_path:
        print(f"Report: {summary.report_path}")
    if summary.questions:
        print("Open questions: " + ", ".join(summary.questions))
    if summary.trace_url:
        print(f"Weave trace: {summary.trace_url}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stitch")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init")
    init_parser.add_argument("--redis-url")
    init_parser.add_argument("--weave-project", default="stitch")
    init_parser.set_defaults(func=_cmd_init)

    add_parser = subparsers.add_parser("add")
    add_parser.add_argument("source")
    add_parser.add_argument("--label")
    add_parser.set_defaults(func=_cmd_add)

    sources_parser = subparsers.add_parser("sources")
    sources_parser.set_defaults(func=_cmd_sources)

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--goal", required=True)
    run_parser.add_argument("--name")
    run_parser.add_argument("--review", action="store_true")
    run_parser.add_argument("--no-prompt", action="store_true")
    run_parser.set_defaults(func=_cmd_run)

    questions_parser = subparsers.add_parser("questions")
    questions_parser.add_argument("--all", action="store_true")
    questions_parser.set_defaults(func=_cmd_questions)

    answer_parser = subparsers.add_parser("answer")
    answer_parser.add_argument("question_id")
    answer_parser.add_argument("answer", nargs="+")
    answer_parser.set_defaults(func=_cmd_answer)

    pipeline_parser = subparsers.add_parser("pipeline")
    pipeline_subparsers = pipeline_parser.add_subparsers(dest="pipeline_command", required=True)
    pipeline_ls = pipeline_subparsers.add_parser("ls")
    pipeline_ls.set_defaults(func=_cmd_pipeline_ls)
    pipeline_show = pipeline_subparsers.add_parser("show")
    pipeline_show.add_argument("name")
    pipeline_show.set_defaults(func=_cmd_pipeline_show)
    pipeline_run = pipeline_subparsers.add_parser("run")
    pipeline_run.add_argument("name")
    pipeline_run.set_defaults(func=_cmd_pipeline_run)

    report_parser = subparsers.add_parser("report")
    report_parser.add_argument("--path", action="store_true")
    report_parser.set_defaults(func=_cmd_report)

    return parser
