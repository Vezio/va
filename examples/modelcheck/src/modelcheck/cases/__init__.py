"""Test cases for modelcheck.

Each module registers its cases into `REGISTRY` when it is imported, so a new case module must
be imported here. Every case has a matching defect in `tests/fake_stack.py` that makes it fail.
"""

from va import CaseRegistry

REGISTRY = CaseRegistry()

# Importing the modules registers their cases.
from . import auth, chat, errors, models, stream  # noqa: E402, F401
