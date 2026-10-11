"""Build step of the dashboard: turns the run folders into one data file.

Scans every test-*/ folder in the repo root (the output format of
https://github.com/carlos-dev-research/evals-healthbench), and for each run:

  1. reads the metadata in description.md (models, reasoning effort, dataset),
  2. reads every conversation in results.jsonl,
  3. computes the per-conversation and per-run scores with the same formula
     as the harness, plus summary counts for the dashboard tiles.

Everything is written to webview/data.js as two JavaScript globals that the
page loads with a <script> tag:

  window.HEALTHBENCH_RUNS    list of parsed runs
  window.HEALTHBENCH_ERRORS  folders that were skipped, with the reason

Loading the data as a script rather than with fetch() lets index.html work
when opened straight from disk. serve.py imports this module and calls main()
on startup and when the page's Refresh button is pressed.

Uses only the Python standard library. Run from the repo root:

    python build.py
"""

import functools
import json
import re
from pathlib import Path

ROOT = Path(__file__).parent              # repo root, where the test-*/ folders live
OUTPUT = ROOT / "webview" / "data.js"     # generated file read by the page

# description.md label -> key in the output
DESCRIPTION_KEYS = {
    "Dataset": "dataset",
    "Model Evaluated": "model",
    "Model Evaluated Reasoning Effort": "model_reasoning",
    "Model Grader": "grader",
    "Model Grader Reasoning Effort": "grader_reasoning",
    "Timestamp": "timestamp",
    "Score": "reported_score",
}


def parse_description(path: Path) -> dict:
    '''Read the "Key: value" lines of a run's description.md.

    The file is optional: a missing file or missing keys just leave those
    fields out, and the page shows them as blank.
    '''
    meta = {}
    if not path.exists():
        return meta
    for line in path.read_text().splitlines():
        # "Model Grader: openai/gpt-oss-20b" -> ("Model Grader", "openai/gpt-oss-20b");
        # the "# TEST RUN DESCRIPTION" heading and blank lines don't match.
        match = re.match(r"^([^:#]+):\s*(.*)$", line)
        if match and match.group(1).strip() in DESCRIPTION_KEYS:
            meta[DESCRIPTION_KEYS[match.group(1).strip()]] = match.group(2).strip()
    if "reported_score" in meta:
        meta["reported_score"] = float(meta["reported_score"])
    return meta


def score_conversation(rubrics: list) -> tuple:
    '''Same formula as evals-healthbench: points earned / max positive points.

    Returns (earned, possible, score). Met negative criteria (penalties)
    subtract from earned, so a conversation's score can be below 0.
    '''
    earned = sum(r["points"] for r in rubrics if r["criteria_met"])
    possible = sum(max(0, r["points"]) for r in rubrics)
    # The harness would divide by zero here; mark it as unscored instead.
    score = earned / possible if possible > 0 else None
    return earned, possible, score


def parse_results(path: Path) -> list:
    '''Read results.jsonl into a list of conversations with their scores.

    Raises ValueError with the line number if a line isn't valid JSON, so
    main() can report exactly where the file is broken.
    '''
    conversations = []
    with path.open() as f:
        for line_number, line in enumerate(f, start=1):
            if not line.strip():  # the harness may leave blank lines
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"invalid JSON on line {line_number} of results.jsonl ({error.msg})") from None
            rubrics = record["rubrics"]
            earned, possible, score = score_conversation(rubrics)
            # Keep only what the page displays; field names are shortened
            # (criteria_met -> met) and prompt_id is stored once per conversation.
            conversations.append({
                "index": len(conversations),  # position in the file, shown as #1, #2, ...
                "prompt_id": rubrics[0]["prompt_id"] if rubrics else None,
                "prompt": record["prompt"],
                "completion": record["completion"],
                "rubrics": [
                    {
                        "points": r["points"],
                        "met": r["criteria_met"],
                        "explanation": r["explanation"],
                    }
                    for r in rubrics
                ],
                "earned": earned,
                "possible": possible,
                "score": score,
            })
    return conversations


def build_run(folder: Path) -> dict:
    '''Parse one test-*/ folder into the run object the page expects.'''
    meta = parse_description(folder / "description.md")
    conversations = parse_results(folder / "results.jsonl")

    # Run score = mean of the conversation scores, clipped to [0, 1] like the harness.
    scores = [c["score"] for c in conversations if c["score"] is not None]
    mean = sum(scores) / len(scores) if scores else 0.0
    score = min(max(mean, 0.0), 1.0)

    # Counts for the summary tiles: positive criteria met, penalties triggered.
    rubrics = [r for c in conversations for r in c["rubrics"]]
    positive = [r for r in rubrics if r["points"] > 0]
    negative = [r for r in rubrics if r["points"] < 0]

    # The page always uses the computed score; flag runs where the harness reported a different one.
    reported = meta.get("reported_score")
    if reported is not None and abs(reported - score) > 1e-6:
        print(f"  warning: {folder.name} reports {reported:.4f}, computed {score:.4f}")

    return {
        "id": folder.name,
        **meta,
        "score": score,
        "conversations_count": len(conversations),
        "criteria_count": len(rubrics),
        "positive_count": len(positive),
        "positive_met": sum(r["met"] for r in positive),
        "negative_count": len(negative),
        "negative_met": sum(r["met"] for r in negative),
        "conversations": conversations,
    }


def main() -> dict:
    '''Rewrite webview/data.js from the test-*/ folders currently on disk.

    A broken run is skipped and reported instead of failing the whole build,
    so the output always reflects what is in the repo right now.

    Returns a short summary ({"runs": [...], "errors": [...]}) that serve.py
    sends back to the page's Refresh button.
    '''
    runs, errors = [], []
    # Sorted by name, which for test-YYYY-MM-DD_HH-MM-SS is oldest first.
    for folder in sorted(p for p in ROOT.glob("test-*") if p.is_dir()):
        if not (folder / "results.jsonl").exists():
            errors.append({"id": folder.name, "error": "results.jsonl not found"})
            continue
        try:
            run = build_run(folder)
        except Exception as error:
            errors.append({"id": folder.name, "error": f"{type(error).__name__}: {error}"})
            continue
        print(f"{run['id']}: {run['conversations_count']} conversations, score {run['score']:.3f}")
        runs.append(run)

    for e in errors:
        print(f"  skipped {e['id']}: {e['error']}")

    # Write to a temp file and swap it in, so the page never loads a half-written file.
    OUTPUT.parent.mkdir(exist_ok=True)
    # Compact JSON (no spaces) keeps the file small; ensure_ascii=False keeps accents readable.
    dump = functools.partial(json.dumps, ensure_ascii=False, separators=(",", ":"))
    tmp = OUTPUT.with_suffix(".js.tmp")
    tmp.write_text(f"window.HEALTHBENCH_RUNS = {dump(runs)};\nwindow.HEALTHBENCH_ERRORS = {dump(errors)};\n")
    tmp.replace(OUTPUT)
    print(f"Wrote {len(runs)} runs to {OUTPUT.relative_to(ROOT)}")

    return {
        "runs": [{"id": r["id"], "conversations": r["conversations_count"], "score": r["score"]} for r in runs],
        "errors": errors,
    }


if __name__ == "__main__":
    main()
