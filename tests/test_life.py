"""The life-of-the-shot page: graph replay, story and log from the chain."""

from __future__ import annotations

import copy
import json
import os
import re

from comfyaudit.core import history as H
from comfyaudit.core.report import life_viewer

HERE = os.path.dirname(os.path.abspath(__file__))
ANA = H.Actor(name="Ana Ruiz", role="compositor")
DOUG = H.Actor(name="Doug Hogan", role="VFX supervisor")


def load(name="beauty-pass.json"):
    with open(os.path.join(HERE, "fixtures", name), encoding="utf-8") as fh:
        return json.load(fh)


def node(wf, kind):
    return next(n for n in wf["nodes"] if n["type"] == kind)


def life():
    k = H.Keys()
    wf = H.stamp(load(), event="created", actor=ANA, keys=k, name="SH0120").workflow
    node(wf, "CLIPTextEncode")["widgets_values"][0] += ", warmer"
    wf = H.stamp(wf, actor=ANA, keys=k).workflow
    node(wf, "CheckpointLoaderSimple")["widgets_values"][0] = "flux1-dev.safetensors"
    wf = H.stamp(wf, actor=DOUG, keys=k).workflow
    wf = H.stamp(wf, event="rendered", actor=DOUG, keys=k).workflow
    removed = wf["nodes"].pop()
    wf["links"] = [l for l in wf["links"] if removed["id"] not in (l[1], l[3])]
    wf = H.stamp(wf, actor=DOUG, keys=k).workflow
    return wf, removed


def test_each_entry_carries_the_graph_as_it_stood_and_what_changed():
    wf, removed = life()
    d = life_viewer.data(H.read(wf))
    rows = d["rows"]
    assert len(rows) == 5 and all(d["graphs"][r["graph"]]["nodes"] for r in rows)
    # Positions and wiring are recorded, so the graph can be redrawn.
    g0 = d["graphs"][rows[0]["graph"]]
    assert g0["links"] and all(len(n["pos"]) == 2 for n in g0["nodes"])
    prompt = str(node(wf, "CLIPTextEncode")["id"])
    ckpt = str(node(wf, "CheckpointLoaderSimple")["id"])
    assert prompt in rows[1]["changed"]
    assert ckpt in rows[2]["changed"] and ckpt in rows[2]["blocking"]
    assert rows[2]["flag"]["status"] == "blocked"
    assert str(removed["id"]) in rows[4]["removed"]
    # A render with no edits marks nothing as changed.
    assert rows[3]["changed"] == [] and rows[3]["added"] == []


def test_the_story_is_told_from_the_entries():
    wf, _ = life()
    story = [r["story"] for r in life_viewer.data(H.read(wf))["rows"]]
    assert story[0].startswith("Ana Ruiz (compositor) started the workflow")
    assert "rewrote the prompt" in story[1]
    assert "flux1-dev.safetensors" in story[2] and "could not be used commercially" in story[2]
    assert "rendered" in story[3] or "ran the workflow" in story[3]


def test_protected_prompts_stay_protected_in_the_story():
    k = H.Keys(confidentiality="redact")
    wf = H.stamp(load(), event="created", actor=ANA, keys=k).workflow
    secret = node(wf, "CLIPTextEncode")["widgets_values"][0]
    node(wf, "CLIPTextEncode")["widgets_values"][0] = "codename NIGHTJAR, hero close-up"
    wf = H.stamp(wf, actor=ANA, keys=k).workflow
    page = life_viewer.render(H.read(wf))
    assert "NIGHTJAR" not in page and secret not in page


def test_text_from_the_workflow_cannot_become_markup():
    k = H.Keys()
    evil = H.Actor(name="<img src=x onerror=alert(1)>", role="</script><script>alert(2)</script>")
    wf = H.stamp(load(), event="created", actor=evil, keys=k, name="%DATA% </script>").workflow
    wf = H.stamp(wf, event="note", actor=evil, keys=k,
                 note="</script><script>alert(3)</script>").workflow
    page = life_viewer.render(H.read(wf))
    script = page.split("<script>", 1)[1]
    assert "</script>" not in script.rsplit("</script>", 1)[0]
    assert "<img src=x" not in page.split("<script>", 1)[0]
    # The page holds exactly one copy of the data blob.
    assert len(re.findall(r"const D=", page)) == 1


def _through_the_browser(doc):
    """What a JSON document looks like after the canvas has held it.

    JavaScript has one number type, so a float with nothing after the point
    (450.0) is written back as an integer (450).
    """
    return json.loads(json.dumps(doc),
                      parse_float=lambda s: int(float(s)) if float(s).is_integer() else float(s))


def test_a_history_survives_a_round_trip_through_the_browser():
    # Every entry is hashed in Python and stored by the browser. Anything that
    # serialises differently after the trip would read as tampering.
    k = H.Keys.from_environment()
    wf = load()
    for n in wf["nodes"]:
        n["pos"] = [float(n["pos"][0]) + 0.0, float(n["pos"][1]) + 0.5]
    wf = H.stamp(wf, event="created", actor=ANA, keys=k).workflow
    wf = _through_the_browser(wf)
    node(wf, "CLIPTextEncode")["widgets_values"][0] += ", warmer"
    wf = H.stamp(wf, actor=ANA, keys=k).workflow
    wf = H.stamp(_through_the_browser(wf), event="rendered", actor=ANA, keys=k).workflow
    v = H.verify(_through_the_browser(wf), keys=k)
    assert v.assurance != "none", v.problems


def test_an_empty_canvas_is_told_apart_from_a_layout_that_was_not_recorded():
    k = H.Keys()
    blank = {"last_node_id": 0, "last_link_id": 0, "nodes": [], "links": [], "version": 0.4}
    wf = H.stamp(blank, event="created", actor=ANA, keys=k).workflow
    filled = load()
    filled["extra"] = wf["extra"]
    wf = H.stamp(filled, actor=ANA, keys=k).workflow
    rows = life_viewer.data(H.read(wf))["rows"]
    assert rows[0]["empty"] and not rows[1]["empty"]
    # Every node that appears with the first real graph is marked as added there.
    assert len(rows[1]["added"]) == len(filled["nodes"])
    page = life_viewer.render(H.read(wf))
    for control in ('id="play"', 'id="prev"', 'id="next"', 'id="speed"', 'id="cap"', "canvas was empty"):
        assert control in page
