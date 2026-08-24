"""The Claude review, exercised against a stand-in Messages endpoint.

The agent loop is the part of this plugin most likely to be wrong in a way that
only shows up in front of a user: tool schemas built from function signatures,
a multi-turn loop, and results collected through tool calls rather than parsed
out of prose. So rather than mock the SDK, these tests point a real client at a
local HTTP server that speaks the Messages API, which exercises the SDK's own
schema generation and loop handling.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

anthropic = pytest.importorskip("anthropic")

from comfyaudit.agent import reviewer  # noqa: E402
from comfyaudit.core.audit import AuditReport  # noqa: E402
from comfyaudit.core.records import LicenseInfo, ModelRef, PromptRef  # noqa: E402


class FakeAPI(BaseHTTPRequestHandler):
    """Replays a scripted sequence of Messages API responses."""

    script: list = []
    seen: list = []
    headers_seen: list = []

    def do_POST(self):  # noqa: N802 - required by BaseHTTPRequestHandler
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        FakeAPI.seen.append(body)
        FakeAPI.headers_seen.append(dict(self.headers))

        index = min(len(FakeAPI.seen) - 1, len(FakeAPI.script) - 1)
        payload = json.dumps(FakeAPI.script[index]).encode()

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):  # silence the test output
        return


def _message(content, stop_reason):
    return {
        "id": "msg_test", "type": "message", "role": "assistant",
        "model": "claude-opus-5", "content": content, "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 120, "output_tokens": 40},
    }


def _tool_use(name, payload, uid="tu_1"):
    return {"type": "tool_use", "id": uid, "name": name, "input": payload}


@pytest.fixture()
def fake_api():
    FakeAPI.seen = []
    FakeAPI.headers_seen = []
    server = HTTPServer(("127.0.0.1", 0), FakeAPI)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


def _report() -> AuditReport:
    report = AuditReport()
    report.source = {"name": "test.json", "format": "ui", "nodes_total": 3}
    report.models = [
        ModelRef(filename="mystery_merge_v4.safetensors", folder="checkpoints",
                 role="Checkpoint", node_label="Loader", node_type="CheckpointLoaderSimple",
                 widget="ckpt_name",
                 license=LicenseInfo(name="Unknown", commercial_use="unknown")),
    ]
    report.prompts = [
        PromptRef(text="a portrait in the style of a named living artist",
                  polarity="positive", node_label="Positive",
                  node_type="CLIPTextEncode", widget="text"),
    ]
    return report


# --------------------------------------------------------------------------


def test_agent_records_results_through_tool_calls(fake_api, monkeypatch):
    _, base_url = fake_api
    FakeAPI.script = [
        _message([_tool_use("list_models", {"only_unidentified": True})], "tool_use"),
        _message([_tool_use("record_model_identification", {
            "filename": "mystery_merge_v4.safetensors",
            "family": "An SDXL community merge", "base_model": "SDXL 1.0",
            "licence": "unclear", "commercial_use": "unknown",
            "confidence": "low", "reasoning": "The name follows the merge convention.",
            "verify_at": "https://civitai.com",
        }, "tu_2")], "tool_use"),
        _message([{"type": "text", "text": "One unidentified checkpoint; verify it."}],
                 "end_turn"),
    ]
    monkeypatch.setenv("ANTHROPIC_BASE_URL", base_url)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    result = reviewer.review(_report(), mode="identify", web_search=False)

    assert result.ran is True, result.error
    assert result.error == ""
    assert result.summary.startswith("One unidentified checkpoint")
    assert len(result.identifications) == 1
    assert result.identifications[0]["confidence"] == "low"
    assert "list_models(only_unidentified=True)" in result.tool_calls
    assert result.turns == 3
    assert result.input_tokens == 360


def test_the_request_carries_the_documented_options(fake_api, monkeypatch):
    """Guards the parameters most likely to drift with an SDK upgrade."""
    _, base_url = fake_api
    FakeAPI.script = [_message([{"type": "text", "text": "done"}], "end_turn")]
    monkeypatch.setenv("ANTHROPIC_BASE_URL", base_url)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    reviewer.review(_report(), mode="clearance", effort="medium", web_search=True)

    sent = FakeAPI.seen[0]
    assert sent["model"] == "claude-opus-5"
    assert sent["thinking"] == {"type": "adaptive"}
    assert sent["output_config"]["effort"] == "medium"
    assert "budget_tokens" not in json.dumps(sent)      # removed on this model
    # Refusal fallbacks so a declined request reroutes instead of coming back
    # empty. The flag rides in the anthropic-beta header, not the body.
    assert sent.get("fallbacks") == "default"
    beta_header = FakeAPI.headers_seen[0].get("anthropic-beta", "")
    assert reviewer.FALLBACK_BETA in beta_header
    # The system prompt is cached: it renders ahead of the volatile user turn.
    assert sent["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_web_search_is_offered_and_can_be_withheld(fake_api, monkeypatch):
    _, base_url = fake_api
    FakeAPI.script = [_message([{"type": "text", "text": "done"}], "end_turn")]
    monkeypatch.setenv("ANTHROPIC_BASE_URL", base_url)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    reviewer.review(_report(), web_search=True)
    with_search = [t.get("type") for t in FakeAPI.seen[0]["tools"]]
    assert "web_search_20260209" in with_search

    FakeAPI.seen = []
    FakeAPI.headers_seen = []
    reviewer.review(_report(), web_search=False)
    without = [t.get("type") for t in FakeAPI.seen[0]["tools"]]
    assert "web_search_20260209" not in without


def test_every_audit_tool_reaches_the_model_with_a_schema(fake_api, monkeypatch):
    _, base_url = fake_api
    FakeAPI.script = [_message([{"type": "text", "text": "done"}], "end_turn")]
    monkeypatch.setenv("ANTHROPIC_BASE_URL", base_url)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    reviewer.review(_report(), web_search=False)

    tools = {t["name"]: t for t in FakeAPI.seen[0]["tools"]}
    for expected in ("list_models", "get_prompts", "list_findings",
                     "describe_workflow", "search_licence_knowledge_base",
                     "list_models_available_locally", "record_model_identification",
                     "record_content_risk", "record_substitution", "record_action"):
        assert expected in tools, f"{expected} was not sent to the model"
        assert tools[expected]["input_schema"]["type"] == "object"
        assert tools[expected]["description"]


def test_a_paused_turn_is_resumed_rather_than_silently_truncated(fake_api, monkeypatch):
    """A long web-search turn can pause; the runner exits, so we restart it."""
    _, base_url = fake_api
    FakeAPI.script = [
        _message([{"type": "text", "text": "still working"}], "pause_turn"),
        _message([{"type": "text", "text": "finished"}], "end_turn"),
    ]
    monkeypatch.setenv("ANTHROPIC_BASE_URL", base_url)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    result = reviewer.review(_report(), web_search=True)

    assert len(FakeAPI.seen) >= 2, "the paused turn was not resumed"
    assert result.summary == "finished"


def test_the_loop_is_bounded(fake_api, monkeypatch):
    """A model that never stops calling tools must not spin forever."""
    _, base_url = fake_api
    FakeAPI.script = [_message([_tool_use("describe_workflow", {})], "tool_use")]
    monkeypatch.setenv("ANTHROPIC_BASE_URL", base_url)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    result = reviewer.review(_report(), web_search=False, max_turns=4)

    assert result.turns == 4
    assert result.ran is True


def test_a_question_replaces_the_mode_prompt(fake_api, monkeypatch):
    _, base_url = fake_api
    FakeAPI.script = [_message([{"type": "text", "text": "answered"}], "end_turn")]
    monkeypatch.setenv("ANTHROPIC_BASE_URL", base_url)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    reviewer.review(_report(), question="Can this go on the farm overnight?")

    first_turn = FakeAPI.seen[0]["messages"][0]["content"]
    assert "farm overnight" in json.dumps(first_turn)


def test_an_api_error_is_explained_not_raised(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:1")  # nothing listening
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    result = reviewer.review(_report(), web_search=False)

    assert result.ran is False
    assert "Could not reach the Anthropic API" in result.error


def test_tool_calls_read_the_real_audit(fake_api, monkeypatch):
    """The tool results must be the audit, not a summary of it."""
    _, base_url = fake_api
    FakeAPI.script = [
        _message([_tool_use("get_prompts", {"polarity": "positive"})], "tool_use"),
        _message([{"type": "text", "text": "read"}], "end_turn"),
    ]
    monkeypatch.setenv("ANTHROPIC_BASE_URL", base_url)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    reviewer.review(_report(), mode="clearance", web_search=False)

    tool_result = json.dumps(FakeAPI.seen[1]["messages"])
    assert "in the style of a named living artist" in tool_result


# --------------------------------------------------------------------------
# Upstream lookups reach the model
# --------------------------------------------------------------------------


def _resolver_stub():
    from comfyaudit.core.resolve.http import Credentials, HttpClient
    from comfyaudit.core.resolve.resolver import Resolver

    return Resolver(http=HttpClient(timeout=2.0), credentials=Credentials(),
                    enabled=True)


def test_lookup_tools_are_offered_when_resolution_is_enabled(fake_api, monkeypatch):
    _, base_url = fake_api
    FakeAPI.script = [_message([{"type": "text", "text": "done"}], "end_turn")]
    monkeypatch.setenv("ANTHROPIC_BASE_URL", base_url)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    reviewer.review(_report(), web_search=False, resolver=_resolver_stub())

    names = {t["name"] for t in FakeAPI.seen[0]["tools"]}
    assert {"lookup_huggingface", "lookup_civitai", "lookup_github"} <= names


def test_lookup_tools_are_withheld_when_resolution_is_off(fake_api, monkeypatch):
    _, base_url = fake_api
    FakeAPI.script = [_message([{"type": "text", "text": "done"}], "end_turn")]
    monkeypatch.setenv("ANTHROPIC_BASE_URL", base_url)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    reviewer.review(_report(), web_search=False, resolver=None)

    names = {t["name"] for t in FakeAPI.seen[0]["tools"]}
    assert not any(n.startswith("lookup_") for n in names)


def test_a_disabled_source_says_so_rather_than_failing(fake_api, monkeypatch):
    """The model must be told a source is off, not left to guess from silence."""
    from comfyaudit.core.resolve.http import Credentials, HttpClient
    from comfyaudit.core.resolve.resolver import Resolver

    _, base_url = fake_api
    FakeAPI.script = [
        _message([_tool_use("lookup_civitai", {"filename_or_hash": "x.safetensors"})],
                 "tool_use"),
        _message([{"type": "text", "text": "noted"}], "end_turn"),
    ]
    monkeypatch.setenv("ANTHROPIC_BASE_URL", base_url)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    resolver = Resolver(http=HttpClient(timeout=2.0), credentials=Credentials(),
                        sources=("huggingface",), enabled=True)
    reviewer.review(_report(), web_search=False, resolver=resolver)

    results = json.dumps(FakeAPI.seen[1]["messages"])
    assert "not enabled for this audit" in results


def test_the_model_is_told_its_licence_recall_may_be_stale(fake_api, monkeypatch):
    _, base_url = fake_api
    FakeAPI.script = [_message([{"type": "text", "text": "done"}], "end_turn")]
    monkeypatch.setenv("ANTHROPIC_BASE_URL", base_url)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    reviewer.review(_report(), web_search=False, resolver=_resolver_stub())

    system = FakeAPI.seen[0]["system"][0]["text"]
    assert "may be out of date" in system
    assert "hash lookup on Civitai is exact" in system


def test_the_cli_offers_every_review_mode_the_agent_supports():
    """The CLI lists modes literally so it need not import the agent at start-up.

    That means the two can drift, and a mode the agent grew would simply be
    unreachable from the command line without anyone noticing.
    """
    from comfyaudit.agent import reviewer
    from comfyaudit.core import cli

    parser = cli.build_parser()
    action = next(a for a in parser._subparsers._group_actions[0].choices["audit"]._actions
                  if "--claude" in getattr(a, "option_strings", []))
    assert set(action.choices) - {""} == set(reviewer.MODES)


def test_the_narrative_mode_is_told_not_to_invent_a_verdict():
    """Its whole job is explaining a determination, never making one."""
    from comfyaudit.agent import reviewer

    prompt = reviewer.MODE_PROMPTS["narrative"]
    assert "read_determination" in prompt
    assert "do not invent" in prompt.lower()
    assert "must not contradict" in prompt.lower()
