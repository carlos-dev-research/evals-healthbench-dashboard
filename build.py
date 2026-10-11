"""Build step of the dashboard: turns the run folders into one data file.

Scans every test-*/ folder inside results/ (the output format of
https://github.com/carlos-dev-research/evals-healthbench), and for each run:

  1. reads the metadata in description.md (models, reasoning effort, dataset),
  2. reads every conversation in results.jsonl,
  3. computes the per-conversation and per-run scores with the same formula
     as the harness, plus summary counts for the dashboard tiles,
  4. attaches the rubric criterion text and tags. Runs made by this repo
     store them in results.jsonl; for older runs (original harness format)
     they are looked up by prompt_id in the HealthBench files in healthbench/,
     if those are present.

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

ROOT = Path(__file__).parent              # repo root
RESULTS = ROOT / "results"                # where the test-*/ run folders live
DATASETS = ROOT / "healthbench"           # HealthBench .jsonl files (optional, git-ignored)
OUTPUT = ROOT / "webview" / "data.js"     # generated file read by the page

# description.md label -> key in the output
DESCRIPTION_KEYS = {
    "Dataset": "dataset",
    "Model Evaluated": "model",
    "Model Evaluated Reasoning Effort": "model_reasoning",
    "Model Grader": "grader",
    "Model Grader Reasoning Effort": "grader_reasoning",
    "Timestamp": "timestamp",
    "Status": "status",            # added by this repo's runner: running, completed, cancelled, failed
    "Score": "reported_score",
}


class RubricLookup:
    '''Finds a conversation's rubric criteria in the HealthBench dataset files.

    Older results.jsonl files only store points and verdicts, not the criterion
    text. Each dataset file is read at most once per process (and again only if
    it changes), so repeated builds from serve.py stay fast.
    '''

    _cache = {}  # path -> (mtime, size, {prompt_id: [rubric, ...]})

    def __init__(self, preferred: Path | None = None):
        files = sorted(DATASETS.glob("*.jsonl")) if DATASETS.is_dir() else []
        # Try the run's own dataset file first; it is the most likely match.
        if preferred is not None and preferred.exists():
            files = [preferred] + [f for f in files if f.resolve() != preferred.resolve()]
        self.files = files

    @classmethod
    def _load(cls, path: Path) -> dict:
        stat = path.stat()
        cached = cls._cache.get(path.resolve())
        if cached and cached[:2] == (stat.st_mtime, stat.st_size):
            return cached[2]
        index = {}
        with path.open() as f:
            for line in f:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict) and record.get("prompt_id") and isinstance(record.get("rubrics"), list):
                    index[record["prompt_id"]] = [
                        {"criterion": r.get("criterion"), "points": r.get("points"), "tags": r.get("tags") or []}
                        for r in record["rubrics"]
                    ]
        cls._cache[path.resolve()] = (stat.st_mtime, stat.st_size, index)
        return index

    def find(self, prompt_id: str, points: list) -> list | None:
        '''Dataset rubrics for prompt_id, if they line up with the graded ones (same count and points, in order).'''
        for path in self.files:
            rubrics = self._load(path).get(prompt_id)
            if rubrics and [r["points"] for r in rubrics] == points:
                return rubrics
        return None


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
                        # Only present in runs made by this repo; filled in from the dataset otherwise.
                        "criterion": r.get("criterion"),
                        "tags": r.get("tags") or [],
                    }
                    for r in rubrics
                ],
                "earned": earned,
                "possible": possible,
                "score": score,
            })
    return conversations


def attach_criteria(conversations: list, dataset: Path | None):
    '''Fill in missing criterion text and tags from the HealthBench dataset files, where they match.'''
    missing = [c for c in conversations if c["prompt_id"] and any(not r["criterion"] for r in c["rubrics"])]
    if not missing:
        return
    lookup = RubricLookup(preferred=dataset)
    for c in missing:
        found = lookup.find(c["prompt_id"], [r["points"] for r in c["rubrics"]])
        if found:
            for rubric, source in zip(c["rubrics"], found):
                rubric["criterion"] = rubric["criterion"] or source["criterion"]
                rubric["tags"] = rubric["tags"] or source["tags"]


def build_run(folder: Path) -> dict:
    '''Parse one test-*/ folder into the run object the page expects.'''
    meta = parse_description(folder / "description.md")
    conversations = parse_results(folder / "results.jsonl")
    attach_criteria(conversations, ROOT / meta["dataset"] if meta.get("dataset") else None)

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
        "criteria_known": sum(1 for r in rubrics if r["criterion"]),
        "conversations": conversations,
    }


def main() -> dict:
    '''Rewrite webview/data.js from the results/test-*/ folders currently on disk.

    A broken run is skipped and reported instead of failing the whole build,
    so the output always reflects what is in the repo right now.

    Returns a short summary ({"runs": [...], "errors": [...]}) that serve.py
    sends back to the page's Refresh button.
    '''
    runs, errors = [], []
    # Sorted by name, which for test-YYYY-MM-DD_HH-MM-SS is oldest first.
    # A missing results/ folder just means no runs yet.
    for folder in sorted(p for p in RESULTS.glob("test-*") if p.is_dir()):
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
