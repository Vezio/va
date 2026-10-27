"""The registry that holds every modelcheck case.

Case modules in `modelcheck.cases` register their functions here with the `REGISTRY.case`
decorator. Import `modelcheck.cases` (not this module) to obtain a registry with every case loaded.
"""

from va import CaseRegistry

REGISTRY = CaseRegistry()
