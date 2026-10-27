"""Named, pluggable implementations: notifiers, result sinks, profile loaders.

Every extension point in runkit is a `PluginRegistry`: a name (a URI scheme, a notifier kind,
a file suffix) mapped to a factory. Built-ins register themselves on import; an app registers
its own in code; a third-party package registers through a Python entry point, with no change
to runkit or the app:

    # pyproject.toml of a plugin package
    [project.entry-points."runkit.notifiers"]
    pagerduty = "runkit_pagerduty:PagerDutyNotifier"

Entry points are loaded lazily, the first time a registry is asked for a name it does not
have, so an unused plugin costs nothing and a broken one only fails when it is selected.
"""

from __future__ import annotations

from collections.abc import Iterator
from importlib.metadata import entry_points
from typing import Generic, TypeVar

T = TypeVar("T")


class UnknownPlugin(KeyError):
    def __init__(self, kind: str, name: str, known: list[str]):
        super().__init__(name)
        self.message = f"unknown {kind} {name!r}; known: {', '.join(known) or 'none'}"

    def __str__(self) -> str:
        return self.message


class PluginRegistry(Generic[T]):
    """`name -> T`, where T is usually a factory. Names are case-insensitive."""

    def __init__(self, kind: str, entry_point_group: str | None = None):
        self.kind = kind
        self.entry_point_group = entry_point_group
        self._items: dict[str, T] = {}
        self._entry_points_loaded = entry_point_group is None

    def register(self, name: str, item: T | None = None, *, replace: bool = False):
        """Register `item` under `name`; used bare it is a decorator. Registering a taken name
        is an error unless `replace=True`, so two plugins cannot silently shadow each other."""

        def add(value: T) -> T:
            key = name.lower()
            if key in self._items and not replace and self._items[key] is not value:
                raise ValueError(f"{self.kind} {name!r} is already registered")
            self._items[key] = value
            return value

        return add(item) if item is not None else add

    def get(self, name: str) -> T:
        key = name.lower()
        if key not in self._items:
            self._load_entry_points()
        try:
            return self._items[key]
        except KeyError:
            raise UnknownPlugin(self.kind, name, self.names()) from None

    def names(self) -> list[str]:
        self._load_entry_points()
        return sorted(self._items)

    def __contains__(self, name: str) -> bool:
        try:
            self.get(name)
        except UnknownPlugin:
            return False
        return True

    def __iter__(self) -> Iterator[str]:
        return iter(self.names())

    def _load_entry_points(self) -> None:
        if self._entry_points_loaded:
            return
        self._entry_points_loaded = True
        if self.entry_point_group is None:
            return
        for point in entry_points(group=self.entry_point_group):
            if point.name.lower() not in self._items:
                self._items[point.name.lower()] = point.load()
