"""RuntimeLogger — structured logging for the landscape pipeline."""

from typing import Optional


class RuntimeLogger:
    _PREFIX = {
        'start': 'START', 'planner': 'PLANNER', 'retrieval': 'RETRIEVAL',
        'augmentation': 'AUGMENTATION', 'formatting': 'FORMATTING',
        'evaluation': 'EVALUATION', 'answerer': 'ANSWERER',
        'complete': 'COMPLETE', 'error': 'ERROR', 'warning': 'WARNING',
    }
    _COLOR = {
        'start': '\033[1;34m', 'planner': '\033[1;35m', 'retrieval': '\033[1;36m',
        'augmentation': '\033[1;36m', 'formatting': '\033[1;36m',
        'evaluation': '\033[1;33m', 'answerer': '\033[1;32m',
        'complete': '\033[1;32m', 'error': '\033[1;31m', 'warning': '\033[1;33m',
    }
    _RESET = '\033[0m'

    def __init__(self, verbose: bool = False):
        self.verbose = verbose

    def log_step(self, step_name: str, message: str, **kwargs):
        key = step_name.lower()
        prefix = self._PREFIX.get(key, 'STEP')
        color = self._COLOR.get(key, '\033[0m')
        print(f"\n{color}[{prefix}] {step_name.upper()}: {message}{self._RESET}")
        if kwargs and self.verbose:
            for k, v in kwargs.items():
                print(f"  • {k}: {v}")

    def log_info(self, message: str, **kwargs):
        if self.verbose:
            print(f"\033[90m[INFO] {message}{self._RESET}")
            if kwargs:
                for k, v in kwargs.items():
                    print(f"  {k}: {v}")

    def log_warning(self, message: str):
        print(f"\033[33m[WARN] {message}{self._RESET}")

    def log_error(self, message: str, exception: Optional[Exception] = None):
        print(f"\033[31m[ERROR] {message}{self._RESET}")
        if exception and self.verbose:
            print(f"  Exception: {exception}")

    def log_debug(self, message: str, **kwargs):
        if self.verbose:
            print(f"\033[90m[DEBUG] {message}{self._RESET}")
            if kwargs:
                for k, v in kwargs.items():
                    print(f"    {k}: {v}")
