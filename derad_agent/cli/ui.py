"""
UI utilities for the derad-agent CLI using Rich.
"""
from rich.console import Console
from rich.theme import Theme
from rich.status import Status

# Custom theme for the CLI
custom_theme = Theme({
    "info": "dim cyan",
    "warning": "yellow",
    "error": "bold red",
    "success": "bold green",
    "reasoning": "italic grey50",
    "query": "cyan",
})

console = Console(theme=custom_theme, force_terminal=True)

class RichLogger:
    """
    Adapter that routes runtime logger calls to a Rich Status spinner or Console.
    Matches the interface of RuntimeLogger in shared/logging.py.
    """
    def __init__(self, status: Status, verbose: bool = False):
        self.status = status
        self.verbose = verbose
        self.steps_completed = []
        
    def log_step(self, step_name: str, message: str, **kwargs):
        """Update the spinner text for major steps."""
        self.steps_completed.append(step_name)
        
        # Update spinner text
        self.status.update(f"[bold blue]{message}[/bold blue]")
        
        # In verbose mode, also print to console above the spinner
        if self.verbose:
            console.log(f"[bold]{step_name.upper()}:[/bold] {message}")
            for k, v in kwargs.items():
                console.log(f"  • {k}: {v}", style="dim")

    def log_info(self, message: str, **kwargs):
        """Log info - usually updates spinner detail or prints dim text."""
        if self.verbose:
            console.log(f"INFO: {message}", style="info")
        else:
            # Just update the spinner label temporarily if needed, or ignore
            # We generally want to keep the main step message on the spinner
            pass

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
    """Create a standard spinner."""
    return console.status(text, spinner="dots")
