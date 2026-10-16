"""Sign and embed C2PA Content Credentials with the studio's own c2patool.

ComfyAudit ships no signer and no certificate: signing needs credentials a
studio holds, and the open-source ``c2patool`` (github.com/contentauth/c2patool)
does the signing and embedding. When it is installed and the studio has given
it credentials, delivery signs each final render into a copy inside the
package; the originals are never touched.

Configured from the environment only (never from browser-writable settings):

* ``COMFYAUDIT_C2PATOOL`` - the c2patool executable (default: on PATH).
* ``COMFYAUDIT_C2PA_SIGNER`` - a signer command for ``--signer-path`` (keeps
  the private key out of c2patool; works with a KMS or HSM), or
* ``COMFYAUDIT_C2PA_KEY`` and ``COMFYAUDIT_C2PA_CERT`` - PEM file paths.
* ``COMFYAUDIT_C2PA_ALG`` - es256 (default), es384, es512, ps256..., ed25519.

c2patool signs with a built-in development certificate when given none, and
does not fail. That is not signing anyone can rely on, so without studio
credentials nothing is signed, and a run that reports the development
certificate is refused.

c2patool cannot embed into OpenEXR; those renders keep the provenance sidecar
and the manifest definition, and the result says so.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Any

#: Formats c2patool can embed into (c2pa-rs docs/supported-formats.md, 2026-10).
EMBEDDABLE = {".avi", ".avif", ".dng", ".flac", ".gif", ".heic", ".heif", ".jpg", ".jpeg",
              ".jxl", ".m4a", ".mp3", ".mp4", ".mov", ".pdf", ".png", ".svg", ".tif",
              ".tiff", ".wav", ".webp"}

#: What c2patool prints when it falls back to its development certificate.
DEV_CERT = "only valid for development"


def tool() -> list[str]:
    """The command that runs c2patool, or [] when it is not available."""
    path = os.environ.get("COMFYAUDIT_C2PATOOL", "") or shutil.which("c2patool") or ""
    if not path or not os.path.isfile(path):
        return []
    return [sys.executable, path] if path.endswith(".py") else [path]


def credentials() -> dict[str, str]:
    signer = os.environ.get("COMFYAUDIT_C2PA_SIGNER", "")
    key, cert = os.environ.get("COMFYAUDIT_C2PA_KEY", ""), os.environ.get("COMFYAUDIT_C2PA_CERT", "")
    if signer:
        return {"signer": signer}
    if key and cert and os.path.isfile(key) and os.path.isfile(cert):
        return {"private_key": key, "sign_cert": cert}
    return {}


def status() -> dict[str, Any]:
    """Whether delivery can sign, and if not, why - for the panel and the README."""
    if not tool():
        return {"available": False, "reason": "c2patool is not installed (set COMFYAUDIT_C2PATOOL "
                                               "or put it on PATH)"}
    if not credentials():
        return {"available": False, "reason": "c2patool is installed but the studio has not "
                                               "given it signing credentials"}
    return {"available": True, "reason": ""}


def definition(manifest: dict[str, Any], version: str) -> tuple[dict[str, Any], str]:
    """Turn ComfyAudit's manifest into a c2patool definition, plus the --create source type.

    The creation action comes from ``--create``, which c2patool adds itself,
    so it is lifted out of the assertions to avoid a second one.
    """
    source = "trainedAlgorithmicMedia"
    assertions = []
    for a in manifest.get("assertions", []):
        if a.get("label") in ("c2pa.actions", "c2pa.actions.v2"):
            for act in (a.get("data") or {}).get("actions", []):
                if act.get("action") == "c2pa.created" and act.get("digitalSourceType"):
                    source = act["digitalSourceType"].rstrip("/").rsplit("/", 1)[-1]
            continue
        assertions.append(a)
    out = {"claim_generator_info": [{"name": "comfyaudit", "version": version}],
           "title": manifest.get("title", ""), "assertions": assertions}
    creds = credentials()
    if "private_key" in creds:
        out.update({"alg": os.environ.get("COMFYAUDIT_C2PA_ALG", "es256"),
                    "private_key": creds["private_key"], "sign_cert": creds["sign_cert"]})
    return out, source


def sign(path: str, manifest: dict[str, Any], out_path: str, version: str) -> dict[str, Any]:
    """Sign ``path`` into ``out_path``. Never raises; the result says what happened."""
    name = os.path.basename(path)
    ext = os.path.splitext(path)[1].lower()
    if ext not in EMBEDDABLE:
        return {"file": name, "signed": False,
                "reason": f"c2patool cannot embed into {ext or 'this'} files; the provenance "
                          "sidecar and manifest definition cover it"}
    st = status()
    if not st["available"]:
        return {"file": name, "signed": False, "reason": st["reason"]}
    defn, source = definition(manifest, version)
    with tempfile.TemporaryDirectory() as tmp:
        mpath = os.path.join(tmp, "manifest.json")
        with open(mpath, "w", encoding="utf-8") as fh:
            json.dump(defn, fh)
        cmd = tool() + [path, "-m", mpath, "-o", out_path, "-f", "--create", source]
        signer = credentials().get("signer")
        if signer:
            cmd += ["--signer-path", signer]
        try:
            run = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        except (OSError, subprocess.SubprocessError) as exc:
            return {"file": name, "signed": False, "reason": f"c2patool did not run: {exc}"}
    if DEV_CERT in (run.stderr + run.stdout):
        if os.path.exists(out_path):
            os.remove(out_path)
        return {"file": name, "signed": False,
                "reason": "c2patool fell back to its development certificate, which no one "
                          "should rely on; check the studio's signing credentials"}
    if run.returncode != 0 or not os.path.isfile(out_path):
        return {"file": name, "signed": False,
                "reason": f"c2patool failed: {(run.stderr or run.stdout).strip()[:300]}"}
    state = ""
    try:
        state = json.loads(run.stdout).get("validation_state", "")
    except ValueError:
        pass
    if state == "Invalid":
        os.remove(out_path)
        return {"file": name, "signed": False, "reason": "c2patool reported the result invalid"}
    return {"file": name, "signed": True, "signed_file": os.path.basename(out_path),
            "validation_state": state or "not reported"}
