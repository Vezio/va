"""runkit: the skeleton for CLIs that test deployed systems (model-serving stacks first),
keep the evidence, and say whose problem a failure is. See DESIGN.md for why it is shaped
this way, and ARCHITECTURE.md for the extension points.

The names below are the public API; everything else is reachable from its module.
"""

__version__ = "0.2.0"

from .cli import App, Command, UsageError, run_app  # noqa: E402
from .commands import RunOptions, add_run_arguments  # noqa: E402
from .compare import Change, ChangeKind, Comparison, compare  # noqa: E402
from .exit_codes import ExitCode  # noqa: E402
from .hooks import HookChain, ProgressLog, RunHooks  # noqa: E402
from .model import (  # noqa: E402
    DEFAULT_SKIP_CLASSES,
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
from .notifiers import NOTIFIERS, Notifier  # noqa: E402
from .plugins import PluginRegistry  # noqa: E402
from .profile import LOADERS, Problems, ProfileError  # noqa: E402
from .publish import Archive, deliver  # noqa: E402
from .redact import REDACTOR, Redactor  # noqa: E402
from .report import RunReport  # noqa: E402
from .rundir import RunDir  # noqa: E402
from .runner import Case, CaseRegistry, Context, Runner, Selector, Target  # noqa: E402
from .session import Baseline, Session  # noqa: E402
from .settings import Key, Settings  # noqa: E402
from .sinks import SINKS, Sink  # noqa: E402

__all__ = [
    "DEFAULT_SKIP_CLASSES",
    "LOADERS",
    "NOTIFIERS",
    "REDACTOR",
    "SINKS",
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
    "ExitCode",
    "Failure",
    "HookChain",
    "Key",
    "Notifier",
    "PluginRegistry",
    "Problem",
    "Problems",
    "ProfileError",
    "ProgressLog",
    "Publication",
    "PublicationStatus",
    "Redactor",
    "ResultRecord",
    "RunDir",
    "RunHooks",
    "RunOptions",
    "RunOutcome",
    "RunReport",
    "Runner",
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
    "deliver",
    "run_app",
]
