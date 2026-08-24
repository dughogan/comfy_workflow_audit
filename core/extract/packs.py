"""Work out which custom node packs a workflow needs, and how pinned they are.

The ComfyUI frontend writes provenance into each node's ``properties``:

* ``cnr_id``  - Comfy Registry pack id, or the literal ``comfy-core``
* ``ver``     - the exact pack version that was installed when this was saved
* ``aux_id``  - ``owner/repo`` for packs installed straight from git

That metadata is the difference between "install ComfyUI-KJNodes" and "install
ComfyUI-KJNodes 1.0.4", so it is treated as first-class evidence here and its
absence is a reproducibility finding rather than a silent gap.
"""

from __future__ import annotations

from .. import catalog
from ..graph import Workflow
from ..records import PackRef

CORE_REGISTRY_IDS = {"comfy-core", "comfyui", "core"}


def extract(wf: Workflow) -> tuple[list[PackRef], list[str], list[str]]:
    """Return ``(packs, core_node_types, api_node_types)``."""
    by_repo: dict[str, PackRef] = {}
    core_types: set[str] = set()
    api_types: set[str] = set()
    unknown: dict[str, PackRef] = {}

    for node in wf.nodes.values():
        if not node.type:
            continue
        if node.type in wf.subgraph_ids:
            # A subgraph instance: its contents were expanded and audited
            # separately, and there is nothing to install for it.
            continue

        props = node.properties or {}
        cnr_id = str(props.get("cnr_id") or "")
        aux_id = str(props.get("aux_id") or "")
        version = str(props.get("ver") or "")

        if catalog.is_api_node(node.type):
            api_types.add(node.type)

        # The workflow itself may assert the node is core - trust that over our
        # catalog, which is pinned to one ComfyUI release.
        if cnr_id.lower() in CORE_REGISTRY_IDS or catalog.is_core_node(node.type):
            core_types.add(node.type)
            continue

        pack = _resolve_pack(node.type, cnr_id, aux_id)

        if pack is None:
            ref = unknown.setdefault(node.type, PackRef(
                title=node.type, identified=False, registry_id=cnr_id,
                pinned_version=version, aux_id=aux_id,
                notes=["node class is not in the ComfyUI-Manager registry index"],
            ))
            _add_type(ref, node.type)
            continue

        repo = pack.get("repo", "") or pack.get("reference", "")
        ref = by_repo.get(repo)
        if ref is None:
            ref = PackRef(
                repo=repo,
                title=pack.get("title", "") or repo.split("/")[-1],
                author=pack.get("author", ""),
                reference=pack.get("reference", "") or (f"https://{repo}" if repo else ""),
                install_type=pack.get("install_type", ""),
                description=pack.get("description", ""),
                stars=pack.get("stars"),
                last_update=pack.get("last_update", ""),
                pip=list(pack.get("pip") or []),
                apt=list(pack.get("apt") or []),
                collisions=list(pack.get("collisions") or []),
            )
            by_repo[repo] = ref

        _add_type(ref, node.type)
        if cnr_id and not ref.registry_id:
            ref.registry_id = cnr_id
        if aux_id and not ref.aux_id:
            ref.aux_id = aux_id
        if version and version not in ("", "nightly") and not ref.pinned_version:
            ref.pinned_version = version
        elif version == "nightly" and not ref.pinned_version:
            ref.pinned_version = "nightly"

    packs = sorted(by_repo.values(), key=lambda p: (-p.node_count, p.title.lower()))
    packs.extend(sorted(unknown.values(), key=lambda p: p.title.lower()))

    for pack in packs:
        if pack.collisions:
            pack.notes.append(
                "node class names also exported by: " + ", ".join(pack.collisions[:4])
            )

    return packs, sorted(core_types), sorted(api_types)


def _resolve_pack(node_type: str, cnr_id: str, aux_id: str) -> dict | None:
    """Prefer what the workflow declares, fall back to the class-name index."""
    if aux_id:
        found = catalog.pack_by_repo(aux_id)
        if found:
            return found
    if cnr_id:
        found = catalog.pack_by_id(cnr_id)
        if found:
            return found
    return catalog.find_pack(node_type)


def _add_type(ref: PackRef, node_type: str) -> None:
    if node_type not in ref.node_types:
        ref.node_types.append(node_type)
    ref.node_count += 1
