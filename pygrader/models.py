"""Classes for reading HealthBench dataset records.

A HealthBench .jsonl file has one record per line: a conversation (prompt)
plus the physician-written rubric criteria used to grade a reply to it.
HeathBenchRecord parses one line; Rubric holds one criterion.
"""

import json

class Rubric:
    def __init__(self, rubric):
        '''Properties
        - Criterion: What the model should have done
        - Points: How many points fulfilling the criterion is worth
        - Tags
        '''
        x = None
        if isinstance(rubric, str):
            x = json.loads(rubric)
        elif isinstance(rubric, dict):
            x = rubric
        else:
            raise Exception("No valid data type for criterion")

        self.criterion = x.get('criterion')
        self.points = x.get('points')
        self.tags = x.get('tags')


    def __str__(self):
        return (
            f"Points: {self.points} Tags: {' '.join(self.tags or [])}\n"
            f"{self.criterion}"
        )

    def __repr__(self):
        return f"Points: {self.points} Tags: {' '.join(self.tags or [])}\n"

    
class HeathBenchRecord:
    def __init__(self, record):    
        '''Properties
        - prompt_id: unique id
        - prompt: Actual conversation the model responds to
        - rubrics: Grading criteria usesd to score a model's response to that prompt
            - criterion
            - points
            - tags metadata about the criterion
        - example_tags: tags categorizing the record
        - ideal_completions_data: optional reference/ideal answer data
        - canary : contamination marker
        '''
        x = json.loads(record)
        self.prompt_id = x.get('prompt_id')
        self.prompt = x.get('prompt')
        self.rubrics = [Rubric(r) for r in x.get('rubrics')]
        self.example_tags = x.get('example_tags')
        self.ideal_completions_data = x.get('ideal_completions_data')
        self.canary = x.get('canary')

    def __str__(self):
        # Joined outside the f-string: backslashes inside f-string expressions need Python 3.12+.
        rubrics = "\n\n".join(str(r) for r in self.rubrics)
        return (
            f"Health Bench Record\n"
            f"Prompt Id: {self.prompt_id}\n"
            f"Canary: {self.canary}\n"
            f"Example Tags: {' '.join(self.example_tags or [])}\n"
            f"Ideal Completions Data: {self.ideal_completions_data}\n\n"
            f"Prompt:\n{self.prompt}\n\n"
            f"Rubrics:\n\n{rubrics}\n"
        )

    def __repr__(self):
        return f"HealthBenchRecord(prompt_id={self.prompt_id!r})"


