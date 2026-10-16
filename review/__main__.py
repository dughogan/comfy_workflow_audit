"""``python -m comfyaudit.review`` - serve ComfyAudit Review. Configured from the environment."""

from __future__ import annotations

import os
import sys

from ..server import history_service, shotgrid
from . import app as app_mod
from .server import make_server


def main() -> int:
    review = app_mod.Review()
    cfg = review.config
    httpd = make_server(review)
    host, port = httpd.server_address[:2]
    keys = app_mod._studio_keys()  # noqa: SLF001
    if app_mod.per_person():
        signing = (f"each person's own key ({len(keys.people_names)} in the keyring), "
                   f"identified by the {os.environ[history_service.TRUSTED_USER_HEADER]} header")
    elif keys.sign_key:
        signing = "the studio key; reviewers give their name"
    else:
        signing = "NONE - read-only until COMFYAUDIT_HISTORY_KEY_FILE is set"
    print(f"ComfyAudit Review on http://{host}:{port}/", flush=True)
    for label, value in (("packages kept in", cfg.root), ("inbox", cfg.inbox or "(none)"),
                         ("signing with", signing),
                         ("ledger", os.environ.get("COMFYAUDIT_HISTORY_LEDGER", "") or "(none)"),
                         ("answers", history_service.answers_store().path),
                         ("ShotGrid", "configured" if shotgrid.configured() else "(not configured)")):
        print(f"  {label:17} {value}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
