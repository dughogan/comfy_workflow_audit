# ComfyAudit: notes for Claude Code

**Start by reading `HANDOFF.md`.** It explains what this branch adds, why, the
design decisions, what was learned and what's left. Priority 1 is testing
`web/shothistory.js` inside a live ComfyUI, which has never been done.

## Ground rules for this repo

- **Standard library only.** No runtime dependencies; `anthropic` and
  `cryptography` are optional extras. Don't add any.
- **The pack imports as `comfyaudit`.** The folder must be named `comfyaudit`
  (or symlinked) for the tests and the CLI to run.
- **Tests:** `python -m pytest -q --ignore=tests/test_agent.py`. Everything
  must pass before committing. Add a test for any change to `core/history.py`
  integrity logic.
- **Generated file:** never hand-edit `core/knowledge/data/regulations.json`.
  Change `tools/build_regulations.py` (`MAP`) or
  `tools/research/regulations.research.json`, then run
  `python tools/build_regulations.py`.
- **Snapshots:** always read them via `history.snapshot_of(entries, e)`, because
  entries may carry `snapshot_ref` instead of `snapshot`.
- **Secrets and file paths** (signing key, ledger) come from environment
  variables only, never from ComfyUI settings.
- **Legal-facing wording must be sourced.** Don't state a legal or licence claim
  the knowledge bases don't support. When sources conflict, flag it (licence
  `conflicts`) rather than picking a side silently.
- **Commit on the `shot-history` branch** until it is tested live; then open a
  PR into `main`.

## Doug's machine

- Primary workstation: Windows, RTX PRO 6000.
- ComfyUI is at `C:\ai\ComfyUI\` (venv `.venv`). Custom nodes are in
  `C:\ai\ComfyUI\custom_nodes\`; this pack lives at
  `C:\ai\ComfyUI\custom_nodes\comfyaudit`.
- He also uses ComfyUI Desktop.
- There is a Rocky Linux ComfyUI box at Bind (pip needs `sudo` with the full
  venv path).
