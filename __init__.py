"""comfyaudit - a ComfyUI workflow audit system.

This module is the ComfyUI custom-node entry point. ComfyUI imports the pack
directory and reads ``NODE_CLASS_MAPPINGS`` and ``WEB_DIRECTORY`` from here.

Everything ComfyUI-specific is optional at import time: the same package is used
by the command line auditor and the test suite, neither of which has a ComfyUI
to talk to, so a missing ComfyUI degrades to the bundled catalog rather than
raising.
"""

from __future__ import annotations

import logging

from .core import __version__

log = logging.getLogger("comfyaudit")

NODE_CLASS_MAPPINGS: dict = {}
NODE_DISPLAY_NAME_MAPPINGS: dict = {}

#: Served to the browser by ComfyUI; adds the menu entry and report panel.
WEB_DIRECTORY = "./web"

try:
    from .nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS
except Exception as exc:  # noqa: BLE001 - logged before it is re-raised
    # Deliberately fatal for this pack. ComfyUI already isolates a failing
    # custom node - it catches this, prints the traceback and carries on
    # without us - so raising costs nothing and buys a real diagnosis. The
    # alternative, loading with an empty mapping, is worse: ComfyUI counts
    # that as a successful import, so the pack appears installed, contributes
    # no nodes, and gives nobody a reason why.
    log.exception("comfyaudit: nodes failed to load (%s)", exc)
    raise


def _bootstrap() -> None:
    """Wire into the running ComfyUI, quietly doing nothing if there isn't one."""
    try:
        from .server import live, routes
    except Exception as exc:  # noqa: BLE001
        log.debug("comfyaudit: server integration unavailable (%s)", exc)
        return

    detail = []
    try:
        if live.install():
            detail.append("live node schemas")
        else:
            detail.append("bundled catalog")
    except Exception as exc:  # noqa: BLE001
        log.warning("comfyaudit: live introspection failed (%s)", exc)
        detail.append("bundled catalog")

    try:
        detail.append("web routes" if routes.register() else "no web routes")
    except Exception as exc:  # noqa: BLE001
        log.warning("comfyaudit: route registration failed (%s)", exc)
        detail.append("no web routes")

    log.info("comfyaudit %s loaded (%s)", __version__, ", ".join(detail))


_bootstrap()

__all__ = [
    "NODE_CLASS_MAPPINGS",
    "NODE_DISPLAY_NAME_MAPPINGS",
    "WEB_DIRECTORY",
    "__version__",
]
