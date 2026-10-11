"""pygrader: HealthBench generation, grading and scoring.

models   dataset record classes (HeathBenchRecord, Rubric)
grader   generate a reply and grade it against each rubric criterion
tools    grader-reply JSON extraction and score calculation
runner   run a whole dataset into results/ (used by eval.py and serve.py)
"""

from .models import *
from .tools import *
from .grader import *
from .runner import RunConfig, run_evaluation
