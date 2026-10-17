"""Comfy Audit: a small web app for studio legal and production staff.

People who clear shots for delivery do not use ComfyUI or a command line. This
app lets them open a delivery package in a browser, see what was decided and
what is still open, answer what is theirs to answer, and clear the shot or send
it back - with every decision signed into the shot's own history, exactly as if
it had been made in ComfyUI.

Run it on an internal studio server (standard library only, no ComfyUI):

    python -m comfyaudit.review

Configuration comes from the environment only; see ``app.Config``.
"""

from .app import Config, Review, ReviewError  # noqa: F401

__all__ = ["Config", "Review", "ReviewError"]
