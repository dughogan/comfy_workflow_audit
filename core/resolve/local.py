"""Check a workflow's models against a real ComfyUI installation.

Pointing the audit at ``--models-dir`` answers two questions the JSON alone
cannot: is this weight actually present on the machine that has to render, and
what is its hash?  The hash then unlocks an exact Civitai lookup, which is the
only reliable way to identify a community checkpoint whose filename has been
renamed a dozen times on the way to your drive.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from typing import Iterable

from .http import cache_dir

WEIGHT_EXTENSIONS = (".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".gguf",
                     ".sft", ".onnx", ".engine")


@dataclass
class LocalFile:
    path: str
    size: int
    folder: str          # the models/<folder> it was found under
    sha256: str = ""


@dataclass
class ModelIndex:
    """Filenames found under a ComfyUI ``models/`` tree."""

    root: str = ""
    by_name: dict[str, list[LocalFile]] = field(default_factory=dict)
    scanned: int = 0
    available: bool = False

    def find(self, filename: str, folder: str = "") -> LocalFile | None:
        """Locate a referenced weight, preferring one in the expected folder."""
        base = os.path.basename((filename or "").replace("\\", "/")).lower()
        matches = self.by_name.get(base)
        if not matches:
            return None
        if folder:
            for candidate in matches:
                if candidate.folder == folder:
                    return candidate
        return matches[0]

    def total_bytes(self, names: Iterable[tuple[str, str]]) -> int:
        seen: set[str] = set()
        total = 0
        for filename, folder in names:
            found = self.find(filename, folder)
            if found and found.path not in seen:
                seen.add(found.path)
                total += found.size
        return total


def scan(models_dir: str, max_files: int = 20000) -> ModelIndex:
    index = ModelIndex(root=models_dir)
    if not models_dir or not os.path.isdir(models_dir):
        return index

    index.available = True
    root_depth = models_dir.rstrip(os.sep).count(os.sep)

    for dirpath, dirnames, filenames in os.walk(models_dir):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        rel_parts = os.path.relpath(dirpath, models_dir).split(os.sep)
        folder = rel_parts[0] if rel_parts and rel_parts[0] != "." else ""
        for name in filenames:
            if not name.lower().endswith(WEIGHT_EXTENSIONS):
                continue
            full = os.path.join(dirpath, name)
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            index.by_name.setdefault(name.lower(), []).append(
                LocalFile(path=full, size=size, folder=folder)
            )
            index.scanned += 1
            if index.scanned >= max_files:
                return index
        del root_depth
    return index


# --------------------------------------------------------------------------
# Hashing
# --------------------------------------------------------------------------


def _hash_db_path() -> str:
    return os.path.join(cache_dir(), "hashes.json")


def _load_hash_db() -> dict[str, dict]:
    try:
        with open(_hash_db_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
            return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_hash_db(db: dict[str, dict]) -> None:
    try:
        with open(_hash_db_path(), "w", encoding="utf-8") as fh:
            json.dump(db, fh)
    except OSError:
        pass


def sha256(path: str, chunk: int = 1 << 20) -> str:
    """Hash a weight file, memoised on (size, mtime) so it happens once.

    Checkpoints run to tens of gigabytes; rehashing them on every audit would
    make the tool unusable, so the result is cached against the file's stat.
    """
    try:
        stat = os.stat(path)
    except OSError:
        return ""

    db = _load_hash_db()
    key = os.path.abspath(path)
    entry = db.get(key)
    if entry and entry.get("size") == stat.st_size and entry.get("mtime") == int(stat.st_mtime):
        return str(entry.get("sha256", ""))

    digest = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            while True:
                block = fh.read(chunk)
                if not block:
                    break
                digest.update(block)
    except OSError:
        return ""

    value = digest.hexdigest()
    db[key] = {"size": stat.st_size, "mtime": int(stat.st_mtime),
               "sha256": value, "at": int(time.time())}
    _save_hash_db(db)
    return value
