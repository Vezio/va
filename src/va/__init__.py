"""Framework for command-line tools that test deployed systems.

The framework keeps the evidence of each run and classifies each failure by the component and
the party responsible for it. See docs/design.md for the design
rationale and docs/architecture.md for the extension points.

The names exported here form the public API. Other names are available from their modules.
"""

__version__ = "0.5.0"

from .cli import App, Command, UsageError, run_app  # noqa: E402
from .commands import RunOptions, add_run_arguments  # noqa: E402
from .compare import Change, ChangeKind, Comparison, compare  # noqa: E402
from .defaults import DEFAULTS, Defaults, RetryPolicy  # noqa: E402
from .exit_codes import ExitCode  # noqa: E402
from .hooks import HookChain, ProgressLog, RunHooks  # noqa: E402
from .layers import (  # noqa: E402
    AnswersItself,
    HeaderServedBy,
    LayerAdapter,
    Layered,
    LayerKind,
    LayerKinds,
    LayerLocator,
    Location,
)
from .layout import RunFile, Schema  # noqa: E402
from .log import emit, get_logger  # noqa: E402
from .model import (  # noqa: E402
    CaseKey,
    Check,
    Counts,
    Failure,
    Problem,
    Publication,
    PublicationStatus,
    ResultRecord,
    RunOutcome,
    Severity,
    SkipClass,
    SkipClasses,
    Verdict,
)
from .notifiers import Notifier  # noqa: E402
from .plugins import PLUGINS, PluginRegistry, Plugins  # noqa: E402
from .profile import Problems, ProfileError  # noqa: E402
from .publish import Archive, deliver  # noqa: E402
from .redact import REDACTOR, Redactor  # noqa: E402
from .report import RunReport  # noqa: E402
from .rundir import RunDir  # noqa: E402
from .runner import Case, CaseRegistry, Context, Runner, Selector, Target  # noqa: E402
from .session import Baseline, Session  # noqa: E402
from .settings import Key, Settings  # noqa: E402
from .sinks import Sink  # noqa: E402

__all__ = [
    "DEFAULTS",
    "PLUGINS",
    "REDACTOR",
    "AnswersItself",
    "App",
    "Archive",
    "Baseline",
    "Case",
    "CaseKey",
    "CaseRegistry",
    "Change",
    "ChangeKind",
    "Check",
    "Command",
    "Comparison",
    "Context",
    "Counts",
    "Defaults",
    "ExitCode",
    "Failure",
    "HookChain",
    "HeaderServedBy",
    "Key",
    "LayerAdapter",
    "Layered",
    "LayerKind",
    "LayerKinds",
    "LayerLocator",
    "Location",
    "Notifier",
    "PluginRegistry",
    "Plugins",
    "Problem",
    "Problems",
    "ProfileError",
    "ProgressLog",
    "Publication",
    "PublicationStatus",
    "Redactor",
    "ResultRecord",
    "RetryPolicy",
    "RunDir",
    "RunFile",
    "RunHooks",
    "RunOptions",
    "RunOutcome",
    "RunReport",
    "Runner",
    "Schema",
    "Selector",
    "Session",
    "Settings",
    "Severity",
    "Sink",
    "SkipClass",
    "SkipClasses",
    "Target",
    "UsageError",
    "Verdict",
    "add_run_arguments",
    "compare",
    "emit",
    "get_logger",
    "deliver",
    "run_app",
]
