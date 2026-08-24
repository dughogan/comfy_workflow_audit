#!/usr/bin/env python3
"""Generate the example workflows used by the tests and the README.

These are deliberately realistic rather than tidy: the point of the fixtures is
to exercise the paths a real production workflow trips over - a non-commercial
face model bolted onto a commercial base, an unpinned custom pack, an absolute
path from somebody's workstation, a muted A/B branch, and a note telling the
next artist which switch to flip.
"""

from __future__ import annotations

import json
import os
from typing import Any


class GraphBuilder:
    """Minimal builder for ComfyUI UI-format graphs."""

    def __init__(self) -> None:
        self.nodes: list[dict[str, Any]] = []
        self.links: list[list[Any]] = []
        self._next_node = 1
        self._next_link = 1

    def node(self, node_type: str, widgets: list[Any] | None = None, *,
             inputs: list[tuple[str, str]] | None = None,
             outputs: list[str] | None = None,
             title: str | None = None, mode: int = 0,
             properties: dict[str, Any] | None = None) -> int:
        node_id = self._next_node
        self._next_node += 1
        self.nodes.append({
            "id": node_id,
            "type": node_type,
            "pos": [220 * (node_id % 7), 160 * (node_id // 7)],
            "size": [300, 120],
            "flags": {},
            "order": node_id,
            "mode": mode,
            "inputs": [{"name": n, "type": t, "link": None} for n, t in (inputs or [])],
            "outputs": [{"name": o, "type": o, "links": [], "slot_index": i}
                        for i, o in enumerate(outputs or [])],
            "properties": properties if properties is not None else {"Node name for S&R": node_type},
            "widgets_values": widgets if widgets is not None else [],
        })
        if title:
            self.nodes[-1]["title"] = title
        return node_id

    def link(self, src: int, src_slot: int, dst: int, dst_input: str, link_type: str) -> None:
        dst_node = self._find(dst)
        slot_index = next((i for i, s in enumerate(dst_node["inputs"])
                           if s["name"] == dst_input), None)
        if slot_index is None:
            raise KeyError(f"node {dst} has no input '{dst_input}'")
        link_id = self._next_link
        self._next_link += 1
        dst_node["inputs"][slot_index]["link"] = link_id
        src_node = self._find(src)
        if src_slot < len(src_node["outputs"]):
            src_node["outputs"][src_slot]["links"].append(link_id)
        self.links.append([link_id, src, src_slot, dst, slot_index, link_type])

    def _find(self, node_id: int) -> dict[str, Any]:
        for node in self.nodes:
            if node["id"] == node_id:
                return node
        raise KeyError(node_id)

    def build(self, groups: list[str] | None = None) -> dict[str, Any]:
        return {
            "id": "example-workflow",
            "revision": 0,
            "last_node_id": self._next_node - 1,
            "last_link_id": self._next_link - 1,
            "nodes": self.nodes,
            "links": self.links,
            "groups": [{"title": g, "bounding": [0, 0, 400, 300]} for g in (groups or [])],
            "config": {},
            "extra": {"frontendVersion": "1.28.4"},
            "version": 0.4,
        }


def core(node_type: str, version: str = "0.3.60") -> dict[str, Any]:
    return {"Node name for S&R": node_type, "cnr_id": "comfy-core", "ver": version}


def pack(node_type: str, cnr_id: str, version: str = "", aux_id: str = "") -> dict[str, Any]:
    props: dict[str, Any] = {"Node name for S&R": node_type, "cnr_id": cnr_id}
    if version:
        props["ver"] = version
    if aux_id:
        props["aux_id"] = aux_id
    return props


# --------------------------------------------------------------------------


def beauty_pass() -> dict[str, Any]:
    """A messy but plausible character beauty-pass workflow."""
    g = GraphBuilder()

    ckpt = g.node("CheckpointLoaderSimple", ["juggernautXL_v9Rundiffusion.safetensors"],
                  outputs=["MODEL", "CLIP", "VAE"], title="Base checkpoint",
                  properties=core("CheckpointLoaderSimple"))

    lora = g.node("LoraLoader", ["studio_skin_detail_v3.safetensors", 0.75, 0.75],
                  inputs=[("model", "MODEL"), ("clip", "CLIP")],
                  outputs=["MODEL", "CLIP"], title="House skin LoRA",
                  properties=core("LoraLoader"))

    pos = g.node("CLIPTextEncode",
                 ["cinematic portrait of the hero character, __lighting_setups__, "
                  "shallow depth of field, {golden hour|overcast} key, "
                  "embedding:studio_look_v2, shot on Alexa 35"],
                 inputs=[("clip", "CLIP")], outputs=["CONDITIONING"],
                 title="Positive", properties=core("CLIPTextEncode"))
    neg = g.node("CLIPTextEncode",
                 ["plastic skin, oversharpened, watermark, embedding:easynegative"],
                 inputs=[("clip", "CLIP")], outputs=["CONDITIONING"],
                 title="Negative", properties=core("CLIPTextEncode"))

    plate = g.node("LoadImage", ["hero_plate_v012.exr", "image"],
                   outputs=["IMAGE", "MASK"], title="Plate",
                   properties=core("LoadImage"))
    ref = g.node("LoadImage", ["D:/shows/DEMO_SHOW/ref/actor_reference_lookdev.png", "image"],
                 outputs=["IMAGE", "MASK"], title="Actor reference",
                 properties=core("LoadImage"))

    vae_encode = g.node("VAEEncode", [], inputs=[("pixels", "IMAGE"), ("vae", "VAE")],
                        outputs=["LATENT"], properties=core("VAEEncode"))

    sampler = g.node("KSampler", [874512336, "fixed", 30, 6.5, "dpmpp_2m", "karras", 0.45],
                     inputs=[("model", "MODEL"), ("positive", "CONDITIONING"),
                             ("negative", "CONDITIONING"), ("latent_image", "LATENT")],
                     outputs=["LATENT"], title="Beauty sampler",
                     properties=core("KSampler"))

    decode = g.node("VAEDecode", [], inputs=[("samples", "LATENT"), ("vae", "VAE")],
                    outputs=["IMAGE"], properties=core("VAEDecode"))

    # Face identity transfer: the licensing landmine.
    faceid = g.node("IPAdapterFaceID",
                    ["ip-adapter-faceid-plusv2_sdxl.bin", "ip-adapter-faceid_sdxl_lora.safetensors",
                     "CUDA", 0.8, "linear", 0.0, 1.0, "V only"],
                    inputs=[("model", "MODEL"), ("image", "IMAGE")],
                    outputs=["MODEL"], title="Face identity",
                    properties=pack("IPAdapterFaceID", "comfyui_ipadapter_plus",
                                    aux_id="cubiq/ComfyUI_IPAdapter_plus"))

    restore = g.node("FaceRestoreCFWithModel",
                     ["codeformer-v0.1.0.pth", "retinaface_resnet50", 0.5, True],
                     inputs=[("image", "IMAGE")], outputs=["IMAGE"],
                     title="Face restore",
                     properties=pack("FaceRestoreCFWithModel", "facerestore_cf"))

    detector = g.node("UltralyticsDetectorProvider", ["bbox/face_yolov8m.pt"],
                      outputs=["BBOX_DETECTOR", "SEGM_DETECTOR"],
                      title="Face detector",
                      properties=pack("UltralyticsDetectorProvider", "comfyui-impact-pack",
                                      version="8.15.3"))

    upscale_model = g.node("UpscaleModelLoader", ["4x-UltraSharp.pth"],
                           outputs=["UPSCALE_MODEL"], properties=core("UpscaleModelLoader"))
    upscale = g.node("ImageUpscaleWithModel", [],
                     inputs=[("upscale_model", "UPSCALE_MODEL"), ("image", "IMAGE")],
                     outputs=["IMAGE"], properties=core("ImageUpscaleWithModel"))

    # A muted alternative branch - the classic "A/B we forgot to delete".
    alt_upscale_model = g.node("UpscaleModelLoader", ["RealESRGAN_x4plus.pth"],
                               outputs=["UPSCALE_MODEL"], mode=2,
                               title="ALT upscaler (muted)",
                               properties=core("UpscaleModelLoader"))

    preview = g.node("PreviewImage", [], inputs=[("images", "IMAGE")],
                     properties=core("PreviewImage"))
    save = g.node("SaveImage", ["DEMO_SHOW/sh0120/beauty_v"], inputs=[("images", "IMAGE")],
                  properties=core("SaveImage"))

    g.node("Note",
           ["SETUP: change the checkpoint to the shot-specific merge before rendering.\n"
            "Remember to swap the upscaler if the plate is over 4K - mute the UltraSharp "
            "node and enable the RealESRGAN one.\n"
            "You need to bump the seed by hand between takes."],
           title="Read me first")

    g.link(ckpt, 0, lora, "model", "MODEL")
    g.link(ckpt, 1, lora, "clip", "CLIP")
    g.link(lora, 1, pos, "clip", "CLIP")
    g.link(lora, 1, neg, "clip", "CLIP")
    g.link(lora, 0, faceid, "model", "MODEL")
    g.link(ref, 0, faceid, "image", "IMAGE")
    g.link(plate, 0, vae_encode, "pixels", "IMAGE")
    g.link(ckpt, 2, vae_encode, "vae", "VAE")
    g.link(faceid, 0, sampler, "model", "MODEL")
    g.link(pos, 0, sampler, "positive", "CONDITIONING")
    g.link(neg, 0, sampler, "negative", "CONDITIONING")
    g.link(vae_encode, 0, sampler, "latent_image", "LATENT")
    g.link(sampler, 0, decode, "samples", "LATENT")
    g.link(ckpt, 2, decode, "vae", "VAE")
    g.link(decode, 0, restore, "image", "IMAGE")
    g.link(restore, 0, upscale, "image", "IMAGE")
    g.link(upscale_model, 0, upscale, "upscale_model", "UPSCALE_MODEL")
    g.link(upscale, 0, preview, "images", "IMAGE")
    g.link(upscale, 0, save, "images", "IMAGE")
    del detector, alt_upscale_model  # present in the graph but not wired, as in life

    return g.build(groups=["Base generation", "Face pipeline", "Finishing"])


def clean_batch() -> dict[str, Any]:
    """A commercially clean, fully automated batch workflow, for contrast."""
    g = GraphBuilder()

    unet = g.node("UNETLoader", ["flux1-schnell.safetensors", "fp8_e4m3fn"],
                  outputs=["MODEL"], properties=core("UNETLoader"))
    clip = g.node("DualCLIPLoader",
                  ["clip_l.safetensors", "t5xxl_fp16.safetensors", "flux", "default"],
                  outputs=["CLIP"], properties=core("DualCLIPLoader"))
    vae = g.node("VAELoader", ["ae.safetensors"], outputs=["VAE"],
                 properties=core("VAELoader"))

    text_src = g.node("PrimitiveStringMultiline",
                      ["product on a seamless white background, studio softbox lighting"],
                      outputs=["STRING"], title="Prompt feed",
                      properties=core("PrimitiveStringMultiline"))
    pos = g.node("CLIPTextEncode", [""], inputs=[("clip", "CLIP"), ("text", "STRING")],
                 outputs=["CONDITIONING"], properties=core("CLIPTextEncode"))
    neg = g.node("ConditioningZeroOut", [], inputs=[("conditioning", "CONDITIONING")],
                 outputs=["CONDITIONING"], properties=core("ConditioningZeroOut"))

    latent = g.node("EmptySD3LatentImage", [1024, 1024, 4], outputs=["LATENT"],
                    properties=core("EmptySD3LatentImage"))
    sampler = g.node("KSampler", [0, "randomize", 4, 1.0, "euler", "simple", 1.0],
                     inputs=[("model", "MODEL"), ("positive", "CONDITIONING"),
                             ("negative", "CONDITIONING"), ("latent_image", "LATENT")],
                     outputs=["LATENT"], properties=core("KSampler"))
    decode = g.node("VAEDecode", [], inputs=[("samples", "LATENT"), ("vae", "VAE")],
                    outputs=["IMAGE"], properties=core("VAEDecode"))
    save = g.node("SaveImage", ["batch/product_"], inputs=[("images", "IMAGE")],
                  properties=core("SaveImage"))

    g.link(clip, 0, pos, "clip", "CLIP")
    g.link(text_src, 0, pos, "text", "STRING")
    g.link(pos, 0, neg, "conditioning", "CONDITIONING")
    g.link(unet, 0, sampler, "model", "MODEL")
    g.link(pos, 0, sampler, "positive", "CONDITIONING")
    g.link(neg, 0, sampler, "negative", "CONDITIONING")
    g.link(latent, 0, sampler, "latent_image", "LATENT")
    g.link(sampler, 0, decode, "samples", "LATENT")
    g.link(vae, 0, decode, "vae", "VAE")
    g.link(decode, 0, save, "images", "IMAGE")
    return g.build()


def beauty_pass_api() -> dict[str, Any]:
    """The same shape in API format, to exercise the other parser path."""
    return {
        "1": {"class_type": "CheckpointLoaderSimple",
              "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"},
              "_meta": {"title": "Base"}},
        "2": {"class_type": "LoraLoader",
              "inputs": {"lora_name": "detail_tweaker_xl.safetensors",
                         "strength_model": 0.6, "strength_clip": 0.6,
                         "model": ["1", 0], "clip": ["1", 1]}},
        "3": {"class_type": "CLIPTextEncode",
              "inputs": {"text": "wide establishing shot of a rain-soaked street",
                         "clip": ["2", 1]}},
        "4": {"class_type": "CLIPTextEncode",
              "inputs": {"text": "blurry, low quality", "clip": ["2", 1]}},
        "5": {"class_type": "EmptyLatentImage",
              "inputs": {"width": 1344, "height": 768, "batch_size": 1}},
        "6": {"class_type": "KSampler",
              "inputs": {"seed": 12345, "steps": 25, "cfg": 7.0,
                         "sampler_name": "dpmpp_2m", "scheduler": "karras", "denoise": 1.0,
                         "model": ["2", 0], "positive": ["3", 0], "negative": ["4", 0],
                         "latent_image": ["5", 0]}},
        "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["1", 2]}},
        "8": {"class_type": "SaveImage",
              "inputs": {"filename_prefix": "street/plate_", "images": ["7", 0]}},
    }


def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    out_dir = os.path.join(here, "..", "examples")
    os.makedirs(out_dir, exist_ok=True)

    for name, payload in (
        ("beauty-pass.json", beauty_pass()),
        ("clean-batch.json", clean_batch()),
        ("beauty-pass-api.json", beauty_pass_api()),
    ):
        path = os.path.join(out_dir, name)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=1)
        print(f"wrote {os.path.relpath(path, here)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
