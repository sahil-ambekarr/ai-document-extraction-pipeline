"""Evaluate extraction accuracy on the labelled synthetic set.

    python scripts/eval.py --provider ollama --mode text
    python scripts/eval.py --provider ollama --mode vision --docs inv-001-classic,po-017-banded-scan
    python scripts/eval.py --report-only        # rebuild docs/eval-results.md from eval/results/

Each run writes eval/results/<provider>_<model>_<mode>.json (overwriting the
previous run of that configuration) and then regenerates docs/eval-results.md
from every JSON file in eval/results/. Documents are processed one at a time
because the local Ollama server is CPU-only and shared.

--record also stores the raw model outputs in data/mock_responses/ so that the
mock provider (tests, CI, demo) replays real answers, retries included.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import re
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.config import get_settings  # noqa: E402
from app.evaluation import HEADER_FIELDS, aggregate, score_document  # noqa: E402
from app.pipeline import ExtractionInputError, run_extraction  # noqa: E402
from app.providers import ProviderError, build_provider  # noqa: E402
from app.rendering import render_document  # noqa: E402

SAMPLES = REPO_ROOT / "data" / "samples"
LABELS = REPO_ROOT / "data" / "labels"
RESULTS = REPO_ROOT / "eval" / "results"
REPORT = REPO_ROOT / "docs" / "eval-results.md"
HARDWARE = "CPU-only laptop, AMD integrated graphics (unused by Ollama), 16 GB RAM, Windows 11"


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9.]+", "-", value).strip("-")


def load_labels(selected: list[str] | None) -> list[tuple[str, dict[str, Any]]]:
    labels = []
    for path in sorted(LABELS.glob("*.json")):
        if selected and path.stem not in selected:
            continue
        labels.append((path.stem, json.loads(path.read_text(encoding="utf-8"))))
    if selected and len(labels) != len(selected):
        found = {stem for stem, _ in labels}
        raise SystemExit(f"Unknown document(s): {sorted(set(selected) - found)}")
    return labels


def record_mock(sha256: str, mode: str, outputs: list[str], source: str) -> None:
    folder = REPO_ROOT / "data" / "mock_responses"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{sha256}.json"
    recording = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    recording.setdefault("sources", {})[mode] = source
    recording.setdefault("by_mode", {})[mode] = outputs
    path.write_text(json.dumps(recording, indent=2) + "\n", encoding="utf-8", newline="\n")


def evaluate(args: argparse.Namespace) -> Path:
    os.environ["LLM_PROVIDER"] = args.provider
    settings = get_settings()
    provider = build_provider(settings)
    model = provider.model_for(args.mode)
    run_date = dt.date.today().isoformat()
    documents: list[dict[str, Any]] = []
    scores = []

    for stem, label in load_labels(args.docs.split(",") if args.docs else None):
        data = (SAMPLES / label["file"]).read_bytes()
        rendered = render_document(data, settings.max_pages, settings.vision_max_side)
        entry: dict[str, Any] = {"doc": stem, "file": label["file"], "pages": rendered.page_count}
        print(f"[{len(documents) + 1}] {stem} ...", end=" ", flush=True)
        try:
            result = run_extraction(rendered, provider, args.mode, args.max_attempts)
        except ExtractionInputError as exc:
            entry.update(status="skipped", reason=str(exc))
            documents.append(entry)
            print("skipped (no text layer)")
            continue
        except ProviderError as exc:
            entry.update(status="provider_error", reason=str(exc))
            score = score_document(None, label)
            entry["score"] = {"field_accuracy": 0.0, "line_item_f1": 0.0, "all_correct": False}
            documents.append(entry)
            scores.append(score)
            print(f"provider error: {exc}")
            continue

        score = score_document(result.data, label)
        scores.append(score)
        entry.update(
            status="ok",
            valid=result.valid,
            attempts=result.attempt_count,
            retries=result.retry_count,
            latency_ms=result.latency_ms,
            attempt_latency_ms=[a.latency_ms for a in result.attempts],
            final_errors=result.errors,
            score={
                "field_accuracy": round(score.field_accuracy, 4),
                "fields": score.fields,
                "line_items": {"matched": score.line_items.matched,
                               "predicted": score.line_items.predicted,
                               "expected": score.line_items.expected,
                               "f1": round(score.line_items.f1, 4)},
                "all_correct": score.all_correct,
            },
            predicted=result.data,
        )
        documents.append(entry)
        if args.record:
            record_mock(rendered.sha256, result.mode, [a.raw_output for a in result.attempts],
                        f"{args.provider}/{model}, {result.mode} mode, recorded {run_date} "
                        "by scripts/eval.py")
        print(f"valid={result.valid} retries={result.retry_count} "
              f"fields={score.field_accuracy:.2f} items_f1={score.line_items.f1:.2f} "
              f"{result.latency_ms / 1000:.0f}s")

    ran = [d for d in documents if d["status"] == "ok"]
    summary = aggregate(scores) if scores else {}
    summary.update(
        evaluated=len(scores),
        skipped=sum(d["status"] == "skipped" for d in documents),
        provider_errors=sum(d["status"] == "provider_error" for d in documents),
        schema_valid_rate=(sum(d["valid"] for d in ran) / len(scores)) if scores else 0.0,
        mean_retries=(sum(d["retries"] for d in ran) / len(ran)) if ran else 0.0,
        docs_needing_retry=sum(d["retries"] > 0 for d in ran),
        mean_latency_s=(sum(d["latency_ms"] for d in ran) / len(ran) / 1000) if ran else 0.0,
    )
    output = {
        "config": {
            "provider": args.provider, "model": model, "mode": args.mode,
            "max_attempts": args.max_attempts, "subset": args.docs or "all",
            "vision_max_side": settings.vision_max_side, "date": run_date,
            "hardware": HARDWARE, "python": platform.python_version(),
        },
        "summary": summary,
        "documents": documents,
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    path = RESULTS / f"{args.provider}_{_slug(model)}_{args.mode}.json"
    path.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {path.relative_to(REPO_ROOT)}")
    return path


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def write_report() -> None:
    runs = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(RESULTS.glob("*.json"))]
    lines = [
        "# Evaluation results",
        "",
        "Generated by `python scripts/eval.py` from the raw files in `eval/results/`. "
        "Do not edit by hand.",
        "",
        "## Summary",
        "",
        "| Provider | Model | Mode | Docs scored | Field accuracy | Line-item F1 (micro) "
        "| All fields correct | Schema-valid | Docs needing a retry | Mean retries "
        "| Mean latency |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for run in runs:
        c, s = run["config"], run["summary"]
        scored = f"{s['evaluated']}" + (f" (subset: {c['subset']})" if c["subset"] != "all"
                                        else "")
        if s.get("skipped"):
            scored += f", {s['skipped']} skipped"
        lines.append(
            f"| {c['provider']} | {c['model']} | {c['mode']} | {scored} "
            f"| {_pct(s.get('field_accuracy', 0))} | {_pct(s.get('line_item_f1_micro', 0))} "
            f"| {_pct(s.get('all_fields_correct_rate', 0))} | {_pct(s['schema_valid_rate'])} "
            f"| {s['docs_needing_retry']} | {s['mean_retries']:.2f} "
            f"| {s['mean_latency_s']:.0f} s |"
        )
    for run in runs:
        c, s = run["config"], run["summary"]
        lines += [
            "",
            f"## {c['provider']} / {c['model']} / {c['mode']} mode",
            "",
            f"Run on {c['date']}, {c['hardware']}, Python {c['python']}, "
            f"max attempts {c['max_attempts']}"
            + (f", vision images downscaled to {c['vision_max_side']} px on the long side"
               if c["mode"] == "vision" else "") + ".",
            "",
            "Per-field accuracy:",
            "",
            "| " + " | ".join(HEADER_FIELDS) + " |",
            "|" + "---|" * len(HEADER_FIELDS),
            "| " + " | ".join(_pct(s.get("per_field_accuracy", {}).get(f, 0))
                              for f in HEADER_FIELDS) + " |",
            "",
            "| Document | Pages | Status | Valid | Retries | Field acc. | Items matched "
            "| Latency | Final validation errors |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for d in run["documents"]:
            if d["status"] != "ok":
                lines.append(f"| {d['doc']} | {d['pages']} | {d['status']} | | | | | "
                             f"| {d.get('reason', '')[:120]} |")
                continue
            items = d["score"]["line_items"]
            errors = "; ".join(d["final_errors"])[:160].replace("|", "/")
            lines.append(
                f"| {d['doc']} | {d['pages']} | ok | {'yes' if d['valid'] else 'no'} "
                f"| {d['retries']} | {_pct(d['score']['field_accuracy'])} "
                f"| {items['matched']}/{items['expected']} (pred {items['predicted']}) "
                f"| {d['latency_ms'] / 1000:.0f} s | {errors} |"
            )
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {REPORT.relative_to(REPO_ROOT)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate extraction on data/samples")
    parser.add_argument("--provider", choices=["ollama", "claude", "mock"], default="ollama")
    parser.add_argument("--mode", choices=["text", "vision", "auto"], default="text")
    parser.add_argument("--docs", help="Comma-separated document stems (default: all)")
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--record", action="store_true",
                        help="Save raw outputs to data/mock_responses/ for the mock provider")
    parser.add_argument("--report-only", action="store_true")
    args = parser.parse_args()
    if not args.report_only:
        started = time.perf_counter()
        evaluate(args)
        print(f"total wall time {time.perf_counter() - started:.0f}s")
    write_report()


if __name__ == "__main__":
    main()
