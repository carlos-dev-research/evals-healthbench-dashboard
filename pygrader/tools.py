"""Helpers for grading: JSON extraction from grader replies, and scoring.

Uses only the Python standard library (the original harness used pandas and
numpy for scoring; the formula is unchanged).
"""

import json

class JsonNotFoundError(Exception):
    """Raised when no valid JSON object could be found in the model's answer."""
    def __init__(self, message="No valid JSON object found in model answer", raw_text=None):
        self.message = message
        self.raw_text = raw_text
        super().__init__(self.message)

def get_result(ans: str) -> dict:
    '''Scan for JSON object in model answer; returns the first valid balanced-brace JSON found.'''
    found = False
    acc = 0
    start = -1

    for i in range(len(ans)):
        if ans[i] == "{":
            if not found:
                start = i
            found = True
            acc += 1
        elif ans[i] == "}":
            acc -= 1

        if found and acc == 0:
            candidate = ans[start:i + 1]
            try:
                return json.loads(candidate, strict=False)
            except json.JSONDecodeError:
                found = False  # reset and keep scanning for another candidate
                continue

    raise JsonNotFoundError("No valid JSON object found in model answer")

def get_score(rubrics: list) -> float | None:
    '''Score of one conversation: points earned / max achievable positive points.

    Met negative criteria (penalties) subtract, so the result can be below 0.
    Returns None when the conversation has no positive criteria.
    '''
    earned = sum(int(r.get("criteria_met")) * r.get("points") for r in rubrics)
    possible = sum(max(0, r.get("points")) for r in rubrics)
    return earned / possible if possible > 0 else None

def calculate_score(results_file) -> float:
    '''Run score: mean conversation score in a results.jsonl, clipped to [0, 1].'''
    scores = []
    with open(results_file) as f:
        for line in f:
            if line.strip():
                score = get_score(json.loads(line)["rubrics"])
                if score is not None:
                    scores.append(score)
    if not scores:
        return 0.0
    return min(max(sum(scores) / len(scores), 0.0), 1.0)
