"""httpcheck's cases. Each module registers into REGISTRY on import; add a module, import it
here, and prove the new case can fail against the fake server (tests/fake_server.py)."""

from runkit.runner import CaseRegistry

REGISTRY = CaseRegistry()

from . import api, health  # noqa: E402, F401  -- registration on import
