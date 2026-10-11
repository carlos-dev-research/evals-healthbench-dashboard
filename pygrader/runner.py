"""Run a full evaluation: every conversation of a dataset file, into results/.

Shared by eval.py (command line, settings from .env) and serve.py (web UI,
settings typed into the page). Creates results/test-<timestamp>/ with the
same two files as the original harness:

  results.jsonl   one graded conversation per line, appended as it finishes
  description.md  run metadata; written at the start with "Status: running"
                  and rewritten at the end with the final status and score

The connection settings (base URL, API key) are only used to build the API
client and are never written to disk.
"""

import json
import shutil
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import openai
from openai import OpenAI

from .grader import grade
from .tools import calculate_score, get_score

# Errors that will fail on every conversation (bad key, unknown model, ...):
# stop the run instead of repeating the same failure hundreds of times.
FATAL_ERRORS = (openai.AuthenticationError, openai.PermissionDeniedError, openai.NotFoundError)
# Stop as well if this many conversations fail in a row (e.g. the endpoint is down).
MAX_CONSECUTIVE_FAILURES = 5


@dataclass
class RunConfig:
    base_url: str
    api_key: str
    model_test: str
    model_grader: str
    reasoning_test: str | None
    reasoning_grader: str | None
    dataset: Path                 # HealthBench .jsonl file to evaluate
    limit: int | None = None      # only evaluate the first N conversations

    def __repr__(self):
        # Keep the API key out of logs and tracebacks.
        return f"RunConfig(model_test={self.model_test!r}, model_grader={self.model_grader!r}, dataset={str(self.dataset)!r})"


def read_dataset(path: Path, limit: int | None = None) -> list:
    '''Return the non-empty lines of a dataset file, optionally only the first `limit`.'''
    with open(path) as f:
        lines = [line for line in f if line.strip()]
    return lines[:limit] if limit else lines


def write_description(folder: Path, config: RunConfig, dataset_label: str, timestamp: str,
                      status: str, total: int, done: int, failed: int, score: float | None):
    '''Write description.md in the harness format, plus Status and Conversations lines.'''
    text = f"""# TEST RUN DESCRIPTION

Dataset: {dataset_label}

Model Evaluated: {config.model_test}

Model Evaluated Reasoning Effort: {config.reasoning_test}

Model Grader: {config.model_grader}

Model Grader Reasoning Effort: {config.reasoning_grader}

Timestamp: {timestamp}

Status: {status}

Conversations: {done} graded, {failed} failed, {total} selected
"""
    if score is not None:
        text += f"\nScore: {score}\n"
    (folder / "description.md").write_text(text)


def run_evaluation(config: RunConfig, results_dir: Path, template: str, root: Path | None = None,
                   on_progress=None, should_stop=lambda: False) -> dict:
    '''Evaluate every conversation in config.dataset and save the run under results_dir.

    on_progress(progress) is called whenever something changes, with a dict:
      state            "running", "completed", "cancelled" or "failed"
      folder           run folder name (test-<timestamp>)
      total            conversations to evaluate
      done / failed    conversations graded / skipped because of an error
      criterion_done / criterion_total   grading progress inside the current conversation
      score            mean score of the conversations graded so far
      errors           last few error messages
      started_at       time.time() when the run started
    should_stop() is checked before each conversation; returning True cancels the run.
    If no conversation could be graded, the run folder is removed and "folder" is None.
    Returns the final progress dict.
    '''
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    folder = results_dir / f"test-{timestamp}"
    # Two runs started in the same second would share a folder: add a suffix instead.
    suffix = 2
    while folder.exists():
        folder = results_dir / f"test-{timestamp}-{suffix}"
        suffix += 1
    folder.mkdir(parents=True)
    results_file = folder / "results.jsonl"
    results_file.touch()

    # Dataset path as the harness wrote it: relative to the repo root when possible.
    dataset_label = str(config.dataset)
    if root is not None:
        for dataset, base in ((Path(config.dataset), root), (Path(config.dataset).resolve(), root.resolve())):
            try:
                dataset_label = str(dataset.relative_to(base))
                break
            except ValueError:
                continue

    lines = read_dataset(config.dataset, config.limit)
    progress = {
        "state": "running", "folder": folder.name, "dataset": dataset_label,
        "model_test": config.model_test, "model_grader": config.model_grader,
        "total": len(lines), "done": 0, "failed": 0,
        "criterion_done": 0, "criterion_total": 0,
        "score": None, "errors": [], "started_at": time.time(), "finished_at": None,
    }
    scores = []

    def report():
        if on_progress:
            on_progress(dict(progress, errors=list(progress["errors"])))

    def describe(status, score=None):
        write_description(folder, config, dataset_label, timestamp, status,
                          len(lines), progress["done"], progress["failed"], score)

    def on_criterion(done, total):
        progress["criterion_done"], progress["criterion_total"] = done, total
        report()

    describe("running")
    report()

    # max_retries: the client retries rate limits, timeouts and 5xx errors itself.
    client = OpenAI(base_url=config.base_url, api_key=config.api_key, max_retries=4, timeout=180)
    consecutive_failures = 0

    try:
        for number, line in enumerate(lines, start=1):
            if should_stop():
                progress["state"] = "cancelled"
                break
            progress["criterion_done"], progress["criterion_total"] = 0, 0
            report()
            try:
                report_line = grade(client, config.model_test, config.reasoning_test,
                                    config.model_grader, config.reasoning_grader,
                                    template, line, on_criterion)
            except FATAL_ERRORS:
                raise
            except Exception as error:
                # Skip this conversation and keep going; it is not written to results.jsonl.
                progress["failed"] += 1
                progress["errors"] = (progress["errors"] + [f"Conversation {number}: {type(error).__name__}: {error}"])[-10:]
                consecutive_failures += 1
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    raise RuntimeError(f"{MAX_CONSECUTIVE_FAILURES} conversations failed in a row; stopping the run") from error
                report()
                continue

            consecutive_failures = 0
            with open(results_file, "a") as f:
                f.write(report_line + "\n")
            score = get_score(json.loads(report_line)["rubrics"])
            if score is not None:
                scores.append(score)
                progress["score"] = min(max(sum(scores) / len(scores), 0.0), 1.0)
            progress["done"] += 1
            report()
        else:
            progress["state"] = "completed"
    except Exception as error:
        progress["state"] = "failed"
        progress["errors"] = (progress["errors"] + [f"{type(error).__name__}: {error}"])[-10:]

    progress["finished_at"] = time.time()
    if progress["done"]:
        final_score = calculate_score(results_file)
        progress["score"] = final_score
        describe(progress["state"], final_score)
    else:
        # Nothing was graded (e.g. wrong API key): don't leave an empty run in results/.
        shutil.rmtree(folder, ignore_errors=True)
        progress["folder"] = None
    report()
    return progress
