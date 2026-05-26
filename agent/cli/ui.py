"""Rich UI utilities for the derad-agent CLI."""

from rich.console import Console
from rich.status import Status
from rich.theme import Theme

console = Console(theme=Theme({
    "info": "dim cyan",
    "warning": "yellow",
    "error": "bold red",
    "success": "bold green",
    "query": "cyan",
}), force_terminal=True)


class RichLogger:
    """Routes structured step-logging calls to a Rich Status spinner."""

    def __init__(self, status: Status, verbose: bool = False):
        self.status = status
        self.verbose = verbose

    def log_step(self, step_name: str, message: str, **kwargs):
        self.status.update(f"[bold blue]{message}[/bold blue]")
        if self.verbose:
            console.log(f"[bold]{step_name.upper()}:[/bold] {message}")
            for k, v in kwargs.items():
                console.log(f"  • {k}: {v}", style="dim")

    def log_info(self, message: str, **kwargs):
        if self.verbose:
            console.log(f"INFO: {message}", style="info")
            for k, v in kwargs.items():
                console.log(f"  {k}: {v}", style="dim")

    def log_warning(self, message: str):
        console.log(f"WARN: {message}", style="warning")

    def log_error(self, message: str, exception=None):
        console.log(f"ERROR: {message}", style="error")
        if exception and self.verbose:
            console.log(str(exception), style="error")

    def log_debug(self, message: str, **kwargs):
        if self.verbose:
            console.log(f"DEBUG: {message}", style="dim")


def create_status(text: str):
    return console.status(text, spinner="dots")
