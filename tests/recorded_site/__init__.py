"""A small real website used to test recording and replay against real browser behaviour.

Every test that claims a recorded action works drives a real browser against
these pages. A fake that returns whatever the test wants would prove only that
the fake agrees with itself; the things that actually break — a select that needs
a change event, a popup that is a second Page, an iframe whose contents are not
in the main document, a download that arrives while another is still saving —
only show up against a real one.

The pages deliberately cover the awkward cases rather than the easy ones:
a value that only appears after a delay, an element inside an iframe, a button
that responds to Enter rather than a click, and two separate downloads in one
task.
"""

from __future__ import annotations

import http.server
import threading
from pathlib import Path

SITE_DIR = Path(__file__).parent / "public"


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, directory=str(SITE_DIR), **kwargs)

    def log_message(self, *args) -> None:  # keep the test output readable
        pass


class LocalSite:
    """Serves the fixture pages on a free port for the lifetime of one test."""

    def __init__(self) -> None:
        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _QuietHandler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self) -> "LocalSite":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
