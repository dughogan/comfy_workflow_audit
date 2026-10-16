"""Signing delivered renders with the studio's c2patool, tested against a stand-in.

The stand-in behaves as c2patool v0.28 does (contentauth/c2patool docs, 2026-10):
``c2patool <in> -m <manifest> -o <out> -f --create <type>`` writes the signed
file, prints the manifest store as JSON, and falls back to a development
certificate - with a note, and exit 0 - when it has no credentials.
"""

from __future__ import annotations

import json
import os
import sys
import textwrap

import pytest

from comfyaudit.server import c2pa

FAKE = textwrap.dedent('''
    import json, shutil, sys, os
    args = sys.argv[1:]
    src, out = args[0], args[args.index("-o") + 1]
    manifest = json.load(open(args[args.index("-m") + 1]))
    log = os.environ.get("FAKE_C2PA_LOG")
    if log:
        json.dump({"args": args, "manifest": manifest}, open(log, "w"))
    if not src.lower().endswith((".png", ".jpg")):
        print("Error: unsupported file type", file=sys.stderr); sys.exit(1)
    if "private_key" not in manifest and "--signer-path" not in args:
        print("Using default private key and signing certificate. This is only valid for development.", file=sys.stderr)
    shutil.copy(src, out)
    open(out, "ab").write(b"C2PA")
    print(json.dumps({"active_manifest": "urn:x", "validation_state": "Valid"}))
''')

MANIFEST = {"claim_generator": "comfyaudit/0.3.0", "title": "SH0120", "assertions": [
    {"label": "c2pa.actions", "data": {"actions": [{"action": "c2pa.created",
     "digitalSourceType": "http://cv.iptc.org/newscodes/digitalsourcetype/compositeWithTrainedAlgorithmicMedia"}]}},
    {"label": "com.comfyaudit.history", "data": {"head": "ab"}}]}


@pytest.fixture
def tool(tmp_path, monkeypatch):
    script = tmp_path / "c2patool.py"
    script.write_text(FAKE)
    monkeypatch.setenv("COMFYAUDIT_C2PATOOL", str(script))
    monkeypatch.setenv("FAKE_C2PA_LOG", str(tmp_path / "call.json"))
    for var in ("COMFYAUDIT_C2PA_SIGNER", "COMFYAUDIT_C2PA_KEY", "COMFYAUDIT_C2PA_CERT"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


def credentials(tmp_path, monkeypatch):
    key, cert = tmp_path / "k.pem", tmp_path / "c.pem"
    key.write_text("KEY")
    cert.write_text("CERT")
    monkeypatch.setenv("COMFYAUDIT_C2PA_KEY", str(key))
    monkeypatch.setenv("COMFYAUDIT_C2PA_CERT", str(cert))


def test_nothing_is_signed_without_studio_credentials(tool):
    frame = tool / "f.png"
    frame.write_bytes(b"png")
    res = c2pa.sign(str(frame), MANIFEST, str(tool / "out.png"), "0.3.0")
    assert not res["signed"] and "credentials" in res["reason"]


def test_a_render_is_signed_into_a_copy_with_the_creation_type(tool, monkeypatch):
    credentials(tool, monkeypatch)
    frame = tool / "f.png"
    frame.write_bytes(b"png")
    res = c2pa.sign(str(frame), MANIFEST, str(tool / "out.png"), "0.3.0")
    assert res["signed"] and res["validation_state"] == "Valid"
    assert frame.read_bytes() == b"png"            # the original is untouched
    call = json.load(open(tool / "call.json"))
    assert call["args"][call["args"].index("--create") + 1] == "compositeWithTrainedAlgorithmicMedia"
    labels = [a["label"] for a in call["manifest"]["assertions"]]
    assert labels == ["com.comfyaudit.history"]    # c2patool adds the creation action itself
    assert call["manifest"]["private_key"].endswith("k.pem")


def test_the_development_certificate_is_refused(tool, monkeypatch):
    # Credentials configured but not handed to the tool: it falls back silently.
    monkeypatch.setattr(c2pa, "definition", lambda m, v: ({"title": "x", "assertions": []}, "trainedAlgorithmicMedia"))
    credentials(tool, monkeypatch)
    frame = tool / "f.png"
    frame.write_bytes(b"png")
    res = c2pa.sign(str(frame), MANIFEST, str(tool / "out.png"), "0.3.0")
    assert not res["signed"] and "development certificate" in res["reason"]
    assert not (tool / "out.png").exists()


def test_exr_is_left_to_the_sidecar(tool, monkeypatch):
    credentials(tool, monkeypatch)
    frame = tool / "beauty.exr"
    frame.write_bytes(b"exr")
    res = c2pa.sign(str(frame), MANIFEST, str(tool / "out.exr"), "0.3.0")
    assert not res["signed"] and "sidecar" in res["reason"]
