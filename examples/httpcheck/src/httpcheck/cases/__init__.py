"""The httpcheck cases.

Each module in this package defines cases and registers them in `httpcheck.registry.REGISTRY`
with the `REGISTRY.case` decorator. Importing this package imports every case module, so
`from httpcheck.cases import REGISTRY` returns the registry with all cases registered.

To add a case module, create it in this package, import it below and add it to `__all__`. Each
case has a matching defect in `tests/fake_server.py` that makes it fail.
"""

from httpcheck.cases import api, health
from httpcheck.registry import REGISTRY

__all__ = ["REGISTRY", "api", "health"]
