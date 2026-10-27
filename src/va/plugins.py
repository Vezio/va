"""Registries of named, pluggable implementations: notifiers, result sinks, profile loaders.

Each extension point in va is a `PluginRegistry`, which maps a name (a URI scheme, a notifier
kind or a file suffix) to a factory. Built-in implementations register themselves on import,
an application can register its own in code, and a third-party package can register through a
Python entry point without changes to va or the application:

    # pyproject.toml of a plugin package
    [project.entry-points."va.notifiers"]
    pagerduty = "va_pagerduty:PagerDutyNotifier"

Entry points are loaded lazily, the first time a registry is asked for a name it does not
have. An unused plugin therefore has no cost, and a broken plugin causes an error only when
it is selected.
"""

from __future__ import annotations

from collections.abc import Iterator
from importlib.metadata import entry_points
from typing import Any, Generic, TypeVar

T = TypeVar("T")


class UnknownPlugin(KeyError):
    """Raised when a registry has no plugin under the requested name.

    Args:
        kind: The kind of plugin, used in the message.
        name: The requested name.
        known: The names that are registered.

    Attributes:
        message: The error message, listing the known names.
    """

    def __init__(self, kind: str, name: str, known: list[str]):
        super().__init__(name)
        self.message = f"unknown {kind} {name!r}; known: {', '.join(known) or 'none'}"

    def __str__(self) -> str:
        """Return the error message."""
        return self.message


class PluginRegistry(Generic[T]):
    """A case-insensitive mapping from names to registered items, usually factories.

    Args:
        kind: The kind of plugin, used in error messages.
        entry_point_group: The entry point group to load additional plugins from, if any.

    Attributes:
        kind: The kind of plugin.
        entry_point_group: The entry point group, or None.
    """

    def __init__(self, kind: str, entry_point_group: str | None = None):
        self.kind = kind
        self.entry_point_group = entry_point_group
        self._items: dict[str, T] = {}
        self._entry_points_loaded = entry_point_group is None

    def register(self, name: str, item: T | None = None, *, replace: bool = False):
        """Register an item under a name, directly or as a decorator.

        When `item` is omitted, the method returns a decorator that registers the decorated
        object. Registering a different item under a name that is already taken is an error
        unless `replace` is true, so that one plugin cannot silently shadow another.

        Args:
            name: The name to register under. It is stored in lower case.
            item: The item to register, or None to return a decorator.
            replace: Whether to replace an existing registration.

        Returns:
            The registered item, or a decorator when `item` is None.

        Raises:
            ValueError: If the name is already registered to a different item and `replace` is
                false.
        """

        def add(value: T) -> T:
            """Register a value under the enclosing name and return it unchanged.

            Args:
                value: The item to register.

            Returns:
                The same item.

            Raises:
                ValueError: If the name is already taken and replacement was not requested.
            """
            key = name.lower()
            if key in self._items and not replace and self._items[key] is not value:
                raise ValueError(f"{self.kind} {name!r} is already registered")
            self._items[key] = value
            return value

        return add(item) if item is not None else add

    def get(self, name: str) -> T:
        """Return the item registered under a name, loading entry points if needed.

        Args:
            name: The name to look up, compared case-insensitively.

        Returns:
            The registered item.

        Raises:
            UnknownPlugin: If no item is registered under the name.
        """
        key = name.lower()
        if key not in self._items:
            self._load_entry_points()
        try:
            return self._items[key]
        except KeyError:
            raise UnknownPlugin(self.kind, name, self.names()) from None

    def names(self) -> list[str]:
        """Return every registered name, including entry points, sorted."""
        self._load_entry_points()
        return sorted(self._items)

    def __contains__(self, name: str) -> bool:
        """Return whether an item is registered under a name."""
        try:
            self.get(name)
        except UnknownPlugin:
            return False
        return True

    def __iter__(self) -> Iterator[str]:
        """Iterate over the registered names in sorted order."""
        return iter(self.names())

    def _load_entry_points(self) -> None:
        """Load plugins from the entry point group once.

        Names that are already registered in code take precedence over entry points.
        """
        if self._entry_points_loaded:
            return
        self._entry_points_loaded = True
        if self.entry_point_group is None:
            return
        for point in entry_points(group=self.entry_point_group):
            if point.name.lower() not in self._items:
                self._items[point.name.lower()] = point.load()


class Plugins:
    """The set of all extension point registries.

    `PLUGINS` is the process-wide instance. Built-in implementations register into it on
    import, an application adds its own in code, and entry points are discovered from the
    groups named here. Registries reject a second registration of a name unless
    `replace=True`, so that one plugin cannot silently shadow another.

    Attributes:
        notifiers: Notifier factories by kind, from the `va.notifiers` group.
        sinks: Result sink factories by URI scheme, from the `va.sinks` group.
        profile_loaders: Profile loaders by file suffix, from the `va.profile_loaders` group.
    """

    def __init__(self) -> None:
        # Each value is a factory: a notifier kind maps to a callable taking a target and
        # returning a Notifier, a URI scheme to a callable taking a URI and returning a Sink,
        # and a file suffix to a callable taking text and returning a mapping. The registries
        # are typed loosely so that this module has no dependencies on the rest of va.
        self.notifiers: PluginRegistry[Any] = PluginRegistry("notifier", "va.notifiers")
        self.sinks: PluginRegistry[Any] = PluginRegistry("result sink", "va.sinks")
        self.profile_loaders: PluginRegistry[Any] = PluginRegistry(
            "profile format", "va.profile_loaders"
        )


PLUGINS = Plugins()
