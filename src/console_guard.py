"""Keep a broken console from killing the app.

Production failure: the app's stdout is a pipe (ConPTY / redirected
console); when the console side disappears, the next write raises
OSError WinError 232/233 ("No process is on the other end of the pipe").
That error used to travel out of rich spinner/table writes and bare
print() calls into the top-level handler in main.py, which showed the
fatal banner and terminated the app.

install_console_guard() wraps sys.stdout once in a ResilientStdout
proxy. While the console is healthy, write()/flush() forward to the
real stream (and every other attribute is forwarded as-is), so output
stays byte-identical. On the first OSError from write() or flush() the
proxy logs exactly one line through the app logger (the `log` callable
from src/logs.py) and from then on swallows every write/flush, so the
rich console, spinners, table draws and print() keep running against a
dead sink while the polling loop, Discord RPC and file logging go on.
"""

import sys


class ResilientStdout:
    """File-like proxy: healthy passthrough, silent no-op once detached."""

    def __init__(self, stream, log):
        self._stream = stream
        self._log = log
        self._detached = False

    @property
    def detached(self):
        return self._detached

    def write(self, text):
        if self._detached:
            return len(text)
        try:
            return self._stream.write(text)
        except OSError as e:
            self._detach("write", e)
            return len(text)

    def flush(self):
        if self._detached:
            return None
        try:
            return self._stream.flush()
        except OSError as e:
            self._detach("flush", e)
            return None

    def _detach(self, action, error):
        if self._detached:
            return
        self._detached = True
        try:
            self._log(
                f"console detached: broken pipe on {action} "
                f"(OSError: {error}); continuing without screen output"
            )
        except Exception:
            # Losing the screen must never escalate into a new failure;
            # if even the log file is gone there is nothing left to do.
            pass

    def __getattr__(self, name):
        # Underscore names raise instead of recursing when the instance
        # is not fully initialized (e.g. during copy/pickle).
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._stream, name)


def install_console_guard(log, stream=None):
    """Wrap sys.stdout (or an explicit stream) so a broken pipe never raises.

    Idempotent: an already guarded stream is returned unchanged, and
    sys.stdout is only replaced when it is the stream being wrapped.
    """
    target = sys.stdout if stream is None else stream
    if isinstance(target, ResilientStdout):
        return target
    guarded = ResilientStdout(target, log)
    if stream is None:
        sys.stdout = guarded
    return guarded
