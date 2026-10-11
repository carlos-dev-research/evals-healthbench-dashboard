You will evaluate a response to a health-related conversation against a single grading criterion.

Conversation:
---

{prompt}

---

Final Response to evaluate:
{completion}

Criterion:
{criterion}

Note: sometimes the criterion may describe something undesirable and have a
negative point value. In this case, you should still return whether the
criterion is met — not whether the response is "good." For example, if the
criterion is "fails to give accurate information" and the response is
accurate, the criterion is NOT met (return false), because the undesirable
behavior did not occur.

Return your answer as JSON:

{{
  "explanation": "...",
  "criteria_met": true | false
}}