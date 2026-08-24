"""Parser tests: the two workflow formats, widget alignment, and subgraphs."""

from __future__ import annotations

import json
import os
import struct
import zlib

import pytest

from comfyaudit.core import graph

EXAMPLES = os.path.join(os.path.dirname(__file__), "fixtures")


def load_example(name: str) -> graph.Workflow:
    return graph.load(os.path.join(EXAMPLES, name))


# -- format detection -------------------------------------------------------


def test_ui_format_is_detected():
    wf = load_example("beauty-pass.json")
    assert wf.source_format == "ui"
    assert len(wf) == 18


def test_api_format_is_detected():
    wf = load_example("beauty-pass-api.json")
    assert wf.source_format == "api"
    assert wf.nodes["6"].type == "KSampler"


def test_wrapped_prompt_payload_is_unwrapped():
    with open(os.path.join(EXAMPLES, "beauty-pass-api.json"), encoding="utf-8") as fh:
        inner = json.load(fh)
    wf = graph.from_dict({"prompt": inner})
    assert wf.source_format == "api"
    assert len(wf) == len(inner)


def test_unrecognised_payload_raises():
    with pytest.raises(ValueError):
        graph.from_dict({"not": "a workflow"})


# -- widget naming ----------------------------------------------------------


def test_positional_widgets_get_their_real_names():
    wf = load_example("beauty-pass.json")
    sampler = next(n for n in wf if n.type == "KSampler")
    assert sampler.widgets["seed"] == 874512336
    assert sampler.widgets["control_after_generate"] == "fixed"
    assert sampler.widgets["steps"] == 30
    assert sampler.widgets["cfg"] == 6.5
    assert sampler.widgets["sampler_name"] == "dpmpp_2m"
    assert sampler.widgets["denoise"] == 0.45


def test_upload_widget_state_does_not_shift_the_filename():
    """LoadImage stores ["file.png", "image"]; the trailing value is UI state."""
    wf = load_example("beauty-pass.json")
    plate = next(n for n in wf if n.title == "Plate")
    assert plate.widgets["image"] == "hero_plate_v012.exr"
    assert not plate.unaligned


def test_api_format_separates_links_from_widget_values():
    wf = load_example("beauty-pass-api.json")
    ksampler = wf.nodes["6"]
    assert ksampler.widgets["steps"] == 25
    assert "model" not in ksampler.widgets
    assert ksampler.inputs["model"].source_node == "2"


def test_unknown_node_widgets_are_kept_anonymously():
    wf = load_example("beauty-pass.json")
    faceid = next(n for n in wf if n.type == "IPAdapterFaceID")
    values = list(faceid.widgets.values())
    assert "ip-adapter-faceid-plusv2_sdxl.bin" in values


# -- modes ------------------------------------------------------------------


def test_muted_nodes_are_reported_but_not_active():
    wf = load_example("beauty-pass.json")
    muted = [n for n in wf if n.mode == graph.MODE_NEVER]
    assert len(muted) == 1
    assert muted[0].mode_name == "muted"
    assert muted[0] not in wf.active()


# -- links and traversal ----------------------------------------------------


def test_links_resolve_to_source_nodes():
    wf = load_example("beauty-pass.json")
    sampler = next(n for n in wf if n.type == "KSampler")
    positives = wf.upstream(sampler, "positive")
    assert len(positives) == 1
    assert positives[0].title == "Positive"


def test_find_producer_walks_back_through_the_graph():
    wf = load_example("beauty-pass.json")
    sampler = next(n for n in wf if n.type == "KSampler")
    ckpt = wf.find_producer(sampler, "model", ["CheckpointLoaderSimple"])
    assert ckpt is not None and ckpt.title == "Base checkpoint"


# -- subgraphs --------------------------------------------------------------


SUBGRAPH_DOC = {
    "nodes": [
        {"id": 1, "type": "sub-uuid", "mode": 0, "inputs": [], "outputs": [],
         "properties": {}, "widgets_values": []},
    ],
    "links": [],
    "definitions": {"subgraphs": [{
        "id": "sub-uuid",
        "name": "Inner",
        "nodes": [
            {"id": 5, "type": "CheckpointLoaderSimple", "mode": 0, "inputs": [],
             "outputs": [{"name": "MODEL", "type": "MODEL", "links": [1]}],
             "properties": {}, "widgets_values": ["inner_model.safetensors"]},
            {"id": 6, "type": "VAEDecode", "mode": 0,
             "inputs": [{"name": "samples", "type": "LATENT", "link": 1}],
             "outputs": [], "properties": {}, "widgets_values": []},
        ],
        "links": [{"id": 1, "origin_id": 5, "origin_slot": 0,
                   "target_id": 6, "target_slot": 0, "type": "LATENT"}],
    }]},
    "version": 0.4,
}


def test_subgraph_nodes_are_expanded_with_namespaced_ids():
    wf = graph.from_dict(SUBGRAPH_DOC)
    assert wf.subgraph_count == 1
    assert "1:5" in wf.nodes
    assert wf.nodes["1:5"].path == ("Inner",)
    assert wf.nodes["1:5"].label.startswith("Inner/")


def test_subgraph_internal_links_stay_inside_the_subgraph():
    """A link inside a subgraph must not resolve to a same-numbered outer node."""
    wf = graph.from_dict(SUBGRAPH_DOC)
    decode = wf.nodes["1:6"]
    assert decode.inputs["samples"].source_node == "1:5"


def test_subgraph_boundary_input_is_not_mistaken_for_an_upstream_feed():
    doc = json.loads(json.dumps(SUBGRAPH_DOC))
    doc["definitions"]["subgraphs"][0]["nodes"][1]["inputs"][0]["link"] = 99
    doc["definitions"]["subgraphs"][0]["links"].append(
        {"id": 99, "origin_id": -10, "origin_slot": 0, "target_id": 6,
         "target_slot": 0, "type": "LATENT"}
    )
    wf = graph.from_dict(doc)
    decode = wf.nodes["1:6"]
    assert decode.inputs["samples"].source_node == "1:-10"
    assert not decode.driven_input("samples", wf.nodes)


# -- embedded metadata ------------------------------------------------------


def _png_with_text(payload: dict) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    text = b"workflow\x00" + json.dumps(payload).encode("utf-8")
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", b"\x00" * 13)
            + chunk(b"tEXt", text) + chunk(b"IEND", b""))


def test_workflow_is_recovered_from_png_metadata(tmp_path):
    with open(os.path.join(EXAMPLES, "clean-batch.json"), encoding="utf-8") as fh:
        payload = json.load(fh)
    png = tmp_path / "render.png"
    png.write_bytes(_png_with_text(payload))

    wf = graph.load(str(png))
    assert wf.source_format == "ui"
    assert any(n.type == "UNETLoader" for n in wf)


def test_png_without_metadata_is_an_error(tmp_path):
    png = tmp_path / "plain.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    with pytest.raises(ValueError):
        graph.load(str(png))
