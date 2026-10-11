"""Parse every test-*/ run folder and write webview/data.js for the dashboard.

Uses only the Python standard library. Run from the repo root:

    python build.py
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).parent
OUTPUT = ROOT / "webview" / "data.js"

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
    '''Read the "Key: value" lines of a run's description.md.'''
    meta = {}
    if not path.exists():
        return meta
    for line in path.read_text().splitlines():
        match = re.match(r"^([^:#]+):\s*(.*)$", line)
        if match and match.group(1).strip() in DESCRIPTION_KEYS:
            meta[DESCRIPTION_KEYS[match.group(1).strip()]] = match.group(2).strip()
    if "reported_score" in meta:
        meta["reported_score"] = float(meta["reported_score"])
    return meta


def score_conversation(rubrics: list) -> tuple:
    '''Same formula as evals-healthbench: points earned / max positive points.'''
    earned = sum(r["points"] for r in rubrics if r["criteria_met"])
    possible = sum(max(0, r["points"]) for r in rubrics)
    score = earned / possible if possible > 0 else None
    return earned, possible, score


def parse_results(path: Path) -> list:
    '''Read results.jsonl into a list of conversations with their scores.'''
    conversations = []
    with path.open() as f:
        for line in f:
            if not line.strip():
                continue
            record = json.loads(line)
            rubrics = record["rubrics"]
            earned, possible, score = score_conversation(rubrics)
            conversations.append({
                "index": len(conversations),
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
    meta = parse_description(folder / "description.md")
    conversations = parse_results(folder / "results.jsonl")

    scores = [c["score"] for c in conversations if c["score"] is not None]
    mean = sum(scores) / len(scores) if scores else 0.0
    score = min(max(mean, 0.0), 1.0)

    rubrics = [r for c in conversations for r in c["rubrics"]]
    positive = [r for r in rubrics if r["points"] > 0]
    negative = [r for r in rubrics if r["points"] < 0]

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


def main():
    folders = sorted(p for p in ROOT.glob("test-*") if (p / "results.jsonl").exists())
    runs = []
    for folder in folders:
        run = build_run(folder)
        print(f"{run['id']}: {run['conversations_count']} conversations, score {run['score']:.3f}")
        runs.append(run)

    OUTPUT.parent.mkdir(exist_ok=True)
    payload = json.dumps(runs, ensure_ascii=False, separators=(",", ":"))
    OUTPUT.write_text(f"window.HEALTHBENCH_RUNS = {payload};\n")
    print(f"Wrote {len(runs)} runs to {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
