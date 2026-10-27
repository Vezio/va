"""The registry that holds every httpcheck case.

Case modules in `httpcheck.cases` register their functions here with the `REGISTRY.case`
decorator. Import `httpcheck.cases` (not this module) to obtain a registry with every case loaded.
"""

from va import CaseRegistry

REGISTRY = CaseRegistry()
