"""Export a shot's history as MovieLabs OMC-JSON v2.8, so studio pipelines read it natively.

The Ontology for Media Creation (github.com/MovieLabs/OMC) is how studio asset
systems describe what was made, by whom, from what. This maps the history on
to it:

* each person who touched the shot becomes a Participant with a Person, and
  ComfyUI a Participant with a Service (software and version);
* each render becomes a Task with a Context that *uses* the shot's source
  inputs and *produces* its output Assets;
* each output Asset carries its SHA-256 as a second identifier (OMC 2.8 has no
  checksum field), its file details, ``provenance`` (created by ComfyUI, when,
  from which input) and ``version.DerivationOf`` its source;
* the shot's own Context carries the studio IDs (ShotGrid, ...) as identifiers.

OMC 2.8 has no fields for AI provenance - models, licences, human break points,
the delivery clearance - so those go in ``customData`` under the ``comfyaudit``
domain, where an OMC reader keeps them without having to understand them.
Prompts are never exported, whatever the confidentiality setting.
"""

from __future__ import annotations

import mimetypes
import os
import re
from typing import Any

from .. import __version__
from .. import history as H

SCHEMA = "https://movielabs.com/omc/json/schema/v2.8"
SCOPE = "comfyaudit"


def _ref(value: str) -> dict[str, Any]:
    return {"identifier": [{"identifierScope": SCOPE, "identifierValue": value}]}


def _entity(kind: str, ident: str, **fields: Any) -> dict[str, Any]:
    return {"schemaVersion": SCHEMA, "entityType": kind,
            "identifier": [{"identifierScope": SCOPE, "identifierValue": ident}],
            **{k: v for k, v in fields.items() if v not in (None, "", [], {})}}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-") or "unknown"


def _data(namespace: str, value: Any) -> list[dict[str, Any]]:
    return [{"domain": SCOPE, "namespace": namespace, "value": value}]


def export(history: dict[str, Any]) -> list[dict[str, Any]]:
    """The history as a list of OMC-JSON v2.8 entities."""
    entries = history.get("entries", [])
    lin = H.lineage(history)
    shot = lin["shot"]
    sid = shot.get("id") or "shot"
    out: list[dict[str, Any]] = []

    # -- who: ComfyUI as a service, each person as a person -------------------
    comfy = f"ptc/comfyui"
    out.append(_entity("Participant", comfy, name="ComfyUI", ParticipantSC=_ref("svc/comfyui")))
    out.append(_entity("Service", "svc/comfyui", structuralType="service",
                       serviceName={"fullName": "ComfyUI"},
                       software=[{"softwareName": "ComfyUI"},
                                 {"softwareName": "comfyaudit", "softwareVersion": __version__}]))
    people: dict[str, str] = {}
    for p in lin["people"]:
        pid = f"ptc/{_slug(p['name'])}"
        people[p["name"]] = pid
        out.append(_entity("Participant", pid, name=p["name"],
                           ParticipantSC=_ref(f"per/{_slug(p['name'])}"),
                           participantFC={"jobTitle": p["role"]} if p.get("role") else None))
        out.append(_entity("Person", f"per/{_slug(p['name'])}", structuralType="person",
                           personName={"fullName": p["name"]}))

    # -- what went in: the shot's supplied inputs -----------------------------
    sources: list[str] = []
    for s in lin["sources"]:
        aid = f"ast/src-{_slug(s['node'])}"
        sources.append(aid)
        name = s.get("value") if isinstance(s.get("value"), str) else f"input node {s['node']}"
        origin = H.ORIGINS.get(s.get("origin") or "unknown", s.get("origin") or "")
        out.append(_entity("Asset", aid, name=name,
                           assetFC={"functionalType": "creativeReferenceMaterial"},
                           annotation=[{"author": s.get("declared_by") or "comfyaudit",
                                        "text": f"Origin: {origin}"
                                                + (f"; asset {s['asset_id']}" if s.get("asset_id") else "")}],
                           customData=_data("source", {k: s.get(k) for k in (
                               "node", "origin", "asset_id", "owner", "first_seq", "from_entry")
                               if s.get(k) not in (None, "")})))

    # -- each render: a task, its context, its outputs ------------------------
    shot_produced: list[dict[str, Any]] = []
    for e in entries:
        if e.get("event") != "rendered" or not e.get("outputs"):
            continue
        seq = e["seq"]
        who = (e.get("actor") or {}).get("name", "")
        snap = H.snapshot_of(entries, e) or {}
        models = [{"filename": m["filename"], "licence": m.get("licence", ""),
                   "commercial_use": m.get("commercial_use", "")}
                  for m in snap.get("models", []) if m.get("enabled", True)]
        produced = []
        for i, o in enumerate(e["outputs"]):
            file = o.get("file", "")
            key = (o.get("sha256") or f"{seq}-{i}")[:16]
            aid, scid = f"ast/out-{key}", f"asc/out-{key}"
            produced.append(_ref(aid))
            ident = [{"identifierScope": SCOPE, "identifierValue": aid}]
            if o.get("sha256"):
                ident.append({"identifierScope": "sha256", "identifierValue": o["sha256"]})
            asset = _entity("Asset", aid, name=file, assetFC={"functionalType": "shot.vfx"},
                            AssetSC=_ref(scid), Context=[_ref(f"cxt/render-{seq}")],
                            provenance={"CreatedBy": _ref(comfy), "createdOn": e.get("at"),
                                        "reason": f"Rendered in ComfyUI, shot history entry {seq}"
                                                  + (f", queued by {who}" if who else ""),
                                        **({"Origin": _ref(sources[0])} if sources else {})},
                            version={"versionNumber": seq, "DerivationOf": _ref(sources[0])}
                            if sources else None)
            asset["identifier"] = ident
            out.append(asset)
            ext = os.path.splitext(file)[1].lstrip(".").lower()
            mtype = mimetypes.guess_type(file)[0] or ""
            out.append(_entity("AssetSC", scid,
                               structuralType="digital.movingImage" if mtype.startswith("video")
                               else "digital.image",
                               structuralProperties={"fileDetails": {k: v for k, v in {
                                   "fileName": file, "fileExtension": ext, "mediaType": mtype,
                                   "filePath": o.get("subfolder", "")}.items() if v}},
                               software=[{"softwareName": "ComfyUI"}],
                               customData=_data("render", {
                                   "entry": seq, "entry_hash": e.get("hash"),
                                   "sha256": o.get("sha256", ""), "bytes": o.get("bytes"),
                                   "models": models})))
        shot_produced += produced
        has = [_ref(comfy)] + ([_ref(people[who])] if who in people else [])
        out.append(_entity("Task", f"tsk/render-{seq}", name=f"ComfyUI render, entry {seq}",
                           taskFC={"functionalType": "generativeRender"},
                           Context=[_ref(f"cxt/render-{seq}")]))
        out.append(_entity("Context", f"cxt/render-{seq}", contextType="production",
                           uses={"Asset": [_ref(a) for a in sources]} if sources else None,
                           produces={"Asset": produced}, has={"Participant": has}))

    # -- the shot itself -------------------------------------------------------
    ctx = _entity("Context", f"cxt/{sid}", name=shot.get("name") or "Shot",
                  contextType="production",
                  uses={"Asset": [_ref(a) for a in sources]} if sources else None,
                  produces={"Asset": shot_produced} if shot_produced else None,
                  has={"Participant": [_ref(comfy)] + [_ref(v) for v in people.values()]},
                  customData=_data("shot", {
                      "record": sid, "stage": shot.get("stage"), "entries": len(entries),
                      "head": entries[-1]["hash"] if entries else "",
                      "human_breakpoints": lin["breakpoints"],
                      "clearance": H.delivery_clearance(history),
                      "parent": shot.get("parent")}))
    for system, value in (shot.get("external_ids") or {}).items():
        ctx["identifier"].append({"identifierScope": str(system), "identifierValue": str(value)})
    out.append(ctx)
    return out
