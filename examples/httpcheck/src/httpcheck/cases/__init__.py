"""Test cases for httpcheck.

Each module registers its cases into `REGISTRY` when it is imported. To add a case, create a
module, import it here, and add a test that shows the case failing against the matching defect
in `tests/fake_server.py`.
"""

from va.runner import CaseRegistry

REGISTRY = CaseRegistry()

# Importing the modules registers their cases.
from . import api, health  # noqa: E402, F401
