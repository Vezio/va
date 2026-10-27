"""modelcheck's cases. Each module registers into REGISTRY on import: a new module must be
imported here. Every case is proven to fail against a defect in tests/fake_stack.py."""

from va import CaseRegistry

REGISTRY = CaseRegistry()

from . import auth, chat, errors, models, stream  # noqa: E402, F401  -- registration on import
