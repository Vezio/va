"""The modelcheck cases.

Each module in this package defines cases and registers them in `modelcheck.registry.REGISTRY`
with the `REGISTRY.case` decorator. Importing this package imports every case module, so
`from modelcheck.cases import REGISTRY` returns the registry with all cases registered.

To add a case module, create it in this package, import it below and add it to `__all__`. Each
case has a matching defect in `tests/fake_stack.py` that makes it fail.
"""

from modelcheck.cases import auth, chat, errors, models, stream
from modelcheck.registry import REGISTRY

__all__ = ["REGISTRY", "auth", "chat", "errors", "models", "stream"]
