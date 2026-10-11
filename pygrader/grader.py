"""Generation and grading of one HealthBench conversation.

grade() sends the conversation to the model under test, then asks the grader
model, once per rubric criterion, whether the reply meets it (using the
GRADER.md prompt template). It returns one results.jsonl line.

Each graded rubric keeps the original harness fields (explanation,
criteria_met, prompt_id, points) and also stores the criterion text and its
tags, so the dashboard can show what was being graded without the dataset.
"""

from openai import OpenAI, NOT_GIVEN
import json
from .models import *
from .tools import *


def reasoning_param(effort: str | None):
    '''Only send reasoning_effort for low/medium/high; other values omit it for models that don't support it.'''
    return effort if effort in ["low", "medium", "high"] else NOT_GIVEN


def grade_prompt(client:OpenAI, model:str, r_grader:str, template:str, temperature:int, completion:str, record:HeathBenchRecord, on_criterion=None):
    '''Grade a completion against every rubric criterion of the record.

    on_criterion(done, total), if given, is called after each criterion so
    callers can report progress.
    '''
    grades = []
    rubrics = record.rubrics


    prompt = "\n\n".join([f"{x.get('role')}\n{x.get('content')}" for x in record.prompt])

    for index, rubric in enumerate(rubrics):
        grader_prompt = template.format(prompt=prompt, completion=completion, criterion=rubric.criterion)
        ans = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": grader_prompt}],
            temperature=temperature,
            reasoning_effort=reasoning_param(r_grader),
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "grading_result",
                    "schema": {
                        "type": "object",
                        "properties": {
                            "explanation": {"type": "string"},
                            "criteria_met": {"type": "boolean"}
                        },
                        "required": ["explanation", "criteria_met"],
                        "additionalProperties": False
                    }
                }
            }
        ).choices[0].message.content
        grade = get_result(ans)
        grade['prompt_id'] = record.prompt_id
        grade['points'] = rubric.points
        # Added to the original schema: what was graded.
        grade['criterion'] = rubric.criterion
        grade['tags'] = rubric.tags or []
        grades.append(grade)
        if on_criterion:
            on_criterion(index + 1, len(rubrics))

    report = {
        "prompt": record.prompt,
        "completion": completion,
        "rubrics": grades
        }

    return report


def grade(client:OpenAI, test_model:str, r_test:str, grader_model:str, r_grader:str, template:str, line_record:str, on_criterion=None) ->str:
    '''Generate a reply to one dataset line with the model under test, grade it, and return the JSON line.'''
    record = HeathBenchRecord(line_record)
    completion = client.chat.completions.create(
        model=test_model,
        messages=record.prompt,
        temperature=0.3,
        reasoning_effort=reasoning_param(r_test)
    ).choices[0].message.content
    report = grade_prompt(client, grader_model, r_grader, template, 0.3, completion, record, on_criterion)
    return json.dumps(report)
