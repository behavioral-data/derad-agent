"""
RuntimeLogger — structured logging for the landscape pipeline.
"""

from typing import Optional


class RuntimeLogger:
    """Custom logger for landscape pipeline steps with structured output."""

    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.steps_completed = []

    def log_step(self, step_name: str, message: str, **kwargs):
        """Log a pipeline step with structured output."""
        prefix_map = {
            'start': 'START',
            'planner': 'PLANNER',
            'retrieval': 'RETRIEVAL',
            'augmentation': 'AUGMENTATION',
            'formatting': 'FORMATTING',
            'evaluation': 'EVALUATION',
            'answerer': 'ANSWERER',
            'complete': 'COMPLETE',
            'error': 'ERROR',
            'warning': 'WARNING',
        }

        prefix = prefix_map.get(step_name.lower(), 'STEP')

        color_map = {
            'start': '\033[1;34m',        # Blue
            'planner': '\033[1;35m',       # Magenta
            'retrieval': '\033[1;36m',     # Cyan
            'augmentation': '\033[1;36m',  # Cyan
            'formatting': '\033[1;36m',    # Cyan
            'evaluation': '\033[1;33m',    # Yellow
            'answerer': '\033[1;32m',      # Green
            'complete': '\033[1;32m',      # Green
            'error': '\033[1;31m',         # Red
            'warning': '\033[1;33m',       # Yellow
        }
        color = color_map.get(step_name.lower(), '\033[0m')
        reset = '\033[0m'

        print(f"\n{color}[{prefix}] {step_name.upper()}: {message}{reset}")

        if kwargs and self.verbose:
            for key, value in kwargs.items():
                print(f"  • {key}: {value}")

        self.steps_completed.append(step_name)

    def log_info(self, message: str, **kwargs):
        """Log informational message (silent by default)."""
        pass

    def log_warning(self, message: str):
        """Log warning message."""
        print(f"\033[33m[WARN] {message}\033[0m")

    def log_error(self, message: str, exception: Optional[Exception] = None):
        """Log error message."""
        print(f"\033[31m[ERROR] {message}\033[0m")
        if exception and self.verbose:
            print(f"  Exception: {exception}")

    def log_debug(self, message: str, **kwargs):
        """Log debug message (only in verbose mode)."""
        if self.verbose:
            print(f"\033[90m[DEBUG] {message}\033[0m")
            if kwargs:
                for key, value in kwargs.items():
                    print(f"    {key}: {value}")
