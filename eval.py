"""Command-line evaluation run (the original harness entry point).

Reads the connection and model settings from a .env file, evaluates a
HealthBench dataset file, and saves the run to results/test-<timestamp>/.
The web UI (serve.py) runs the same code with settings typed into the page.

    python eval.py                                   # default dataset below
    python eval.py healthbench/<file>.jsonl          # another dataset file
    python eval.py healthbench/<file>.jsonl --limit 10

.env variables: BASE_URL, API_KEY, MODEL_TEST, MODEL_GRADER, RESONING_TEST,
RESONING_GRADER (the harness spelling; REASONING_TEST / REASONING_GRADER also work).
"""

# 1. Initialize Environment
from dotenv import load_dotenv
load_dotenv()


# 2. Import libraries and dependencies
import argparse
import os
from pathlib import Path
from tqdm import tqdm
from pygrader import RunConfig, run_evaluation

# 3. Define parameters
ROOT = Path(__file__).parent
DATASET = "healthbench/2025-05-07-06-14-12_oss_eval_N200.jsonl"

parser = argparse.ArgumentParser(description="Run a HealthBench evaluation with the settings in .env")
parser.add_argument("dataset", nargs="?", default=DATASET, help=f"dataset .jsonl file (default: {DATASET})")
parser.add_argument("--limit", type=int, default=None, help="only evaluate the first N conversations")
args = parser.parse_args()

config = RunConfig(
    base_url=os.getenv("BASE_URL"),
    api_key=os.getenv("API_KEY"),
    model_test=os.getenv("MODEL_TEST"),
    model_grader=os.getenv("MODEL_GRADER"),
    reasoning_test=os.getenv("RESONING_TEST") or os.getenv("REASONING_TEST"),     # low | medium | high | None
    reasoning_grader=os.getenv("RESONING_GRADER") or os.getenv("REASONING_GRADER"),  # low | medium | high | None
    dataset=Path(args.dataset),
    limit=args.limit,
)

# 4. Load Template for the Grader
template = (ROOT / "GRADER.md").read_text()

# 5. Evaluate dataset, with a progress bar per conversation
bar = tqdm(desc="Evaluation Model", unit="conversation")

def on_progress(progress):
    bar.total = progress["total"]
    bar.n = progress["done"] + progress["failed"]
    bar.set_postfix(failed=progress["failed"], criterion=f"{progress['criterion_done']}/{progress['criterion_total']}")
    bar.refresh()

result = run_evaluation(config, ROOT / "results", template, root=ROOT, on_progress=on_progress)
bar.close()

# 6. Report
for error in result["errors"]:
    print("error:", error)
score = "n/a" if result["score"] is None else f"{result['score']:.4f}"
print(f"Report Finish: {result['state']}, {result['done']} graded, {result['failed']} failed, score {score}")
print(f"Saved to results/{result['folder']}/")
