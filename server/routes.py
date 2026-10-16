"""HTTP routes so the current graph can be audited without wiring a node up.

Adding a node and running the queue is the right flow when the audit is part of
a pipeline. When you just want to know whether the thing on your canvas is
deliverable, a menu button is the right flow, and that needs somewhere to POST
the graph to.

Route registration is best effort: if the ComfyUI server object is not there,
the plugin still loads and the nodes still work.
"""

from __future__ import annotations

import asyncio
import json
import os
import traceback
from typing import Any

from ..agent import reviewer as reviewer_mod
from ..core import catalog
from ..core.knowledge import licences as licences_mod
from ..core.report import html as html_report
from ..core.report import markdown as md_report
from . import delivery
from . import history_service
from . import live
from . import settings as settings_mod

PREFIX = "/comfyaudit"


def register() -> bool:
    """Attach the routes to the running ComfyUI server. Returns success."""
    try:
        from aiohttp import web
        from server import PromptServer
    except Exception:
        return False

    instance = getattr(PromptServer, "instance", None)
    if instance is None or not hasattr(instance, "routes"):
        return False
    routes = instance.routes

    @routes.get(f"{PREFIX}/status")
    async def status(request):  # noqa: ANN001
        agent_ok, agent_why = reviewer_mod.available()
        return web.json_response({
            "ok": True,
            "knowledge": {
                "comfyui_catalog": catalog.comfyui_version(),
                "licences": licences_mod.kb_metadata(),
                "node_packs_indexed": len(catalog.node_packs()["packs"]),
                "live_introspection": catalog.has_live_provider(),
            },
            "environment": live.environment(),
            # So the panel can say whether a verdict is even possible before
            # anyone waits on an audit to find out.
            "studio_profile": settings_mod.describe(),
            "claude": {"available": agent_ok, "reason": agent_why,
                       "models": reviewer_mod.MODELS, "modes": reviewer_mod.MODES},
        })

    @routes.post(f"{PREFIX}/audit")
    async def audit_route(request):  # noqa: ANN001
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "request body must be JSON"}, status=400)

        workflow = body.get("workflow")
        if not isinstance(workflow, dict):
            return web.json_response(
                {"error": "expected a 'workflow' object (the serialised graph)"},
                status=400)

        options = body.get("options") or {}
        try:
            payload = await _run(_audit_sync, workflow, options)
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            return web.json_response(
                {"error": f"{type(exc).__name__}: {exc}"}, status=500)
        return web.json_response(payload)

    @routes.post(f"{PREFIX}/review")
    async def review_route(request):  # noqa: ANN001
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "request body must be JSON"}, status=400)

        workflow = body.get("workflow")
        if not isinstance(workflow, dict):
            return web.json_response({"error": "expected a 'workflow' object"}, status=400)

        try:
            payload = await _run(_review_sync, workflow, body)
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            return web.json_response(
                {"error": f"{type(exc).__name__}: {exc}"}, status=500)
        return web.json_response(payload)

    # -- shot history --------------------------------------------------------

    def _history_route(path: str, fn):
        @routes.post(f"{PREFIX}/history/{path}")
        async def handler(request):  # noqa: ANN001
            try:
                body = await request.json()
            except Exception:
                return web.json_response({"error": "request body must be JSON"}, status=400)
            if not isinstance(body.get("workflow"), dict):
                return web.json_response({"error": "expected a 'workflow' object"}, status=400)
            # Who is signed in comes only from the studio's sign-in proxy, by the
            # header the studio names - never from the request body.
            body.pop("_signed_in_user", None)
            header = os.environ.get("COMFYAUDIT_TRUSTED_USER_HEADER", "")
            if header:
                body["_signed_in_user"] = request.headers.get(header, "")
            try:
                payload = await _run(fn, body)
            except ValueError as exc:
                return web.json_response({"error": str(exc)}, status=400)
            except Exception as exc:  # noqa: BLE001
                traceback.print_exc()
                return web.json_response({"error": f"{type(exc).__name__}: {exc}"}, status=500)
            return web.json_response(payload, dumps=lambda o: json.dumps(o, default=str))
        return handler

    _history_route("stamp", history_service.stamp)
    _history_route("record", history_service.record)
    _history_route("manifest", history_service.manifest)
    _history_route("relocate", history_service.relocate)
    _history_route("life", history_service.life)
    _history_route("check", delivery.check)
    _history_route("deliver", delivery.deliver)
    _history_route("summary", lambda body: history_service.summary(
        _read_history(body["workflow"]), workflow=body["workflow"]))

    @routes.get(f"{PREFIX}/licensing-guide")
    async def guide_route(request):  # noqa: ANN001
        html = await _run(history_service.guide)
        return web.Response(text=html, content_type="text/html")

    return True


def _read_history(workflow: dict[str, Any]):
    from ..core import history as history_mod
    return history_mod.read(workflow)


async def _run(fn, *args) -> Any:
    """Run blocking work off the event loop so the UI stays responsive."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, fn, *args)


def _audit_sync(workflow: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
    return _payload(_audit(workflow, options))


def _review_sync(workflow: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    from ..nodes.audit_nodes import render_review

    options = body.get("options") or {}
    report = _audit(workflow, options)

    lister = None
    try:
        from ..nodes.audit_nodes import _local_model_lister
        lister = _local_model_lister()
    except Exception:
        lister = None

    resolver = None
    if bool((body.get("options") or {}).get("online")) or body.get("model_lookups", True):
        from ..core.resolve.http import Credentials, HttpClient
        from ..core.resolve.resolver import Resolver
        from ..nodes.audit_nodes import parse_sources
        resolver = Resolver(http=HttpClient(),
                            credentials=Credentials.from_environment(),
                            sources=parse_sources(str(options.get("sources", "") or "")),
                            enabled=True)

    result = reviewer_mod.review(
        report,
        mode=str(body.get("mode", "full")),
        model=str(body.get("model", reviewer_mod.DEFAULT_MODEL)),
        effort=str(body.get("effort", "high")),
        api_key=str(body.get("api_key", "") or ""),
        web_search=bool(body.get("web_search", True)),
        question=str(body.get("question", "") or ""),
        local_models=lister,
        resolver=resolver,
    )
    reviewer_mod.apply_to_report(report, result)

    payload = _payload(report)
    payload["review"] = result.as_dict()
    payload["review_markdown"] = render_review(result)
    return payload


def _audit(workflow: dict[str, Any], options: dict[str, Any]):
    from ..nodes.audit_nodes import run_audit

    return run_audit(
        workflow,
        online=bool(options.get("online")),
        check_local_models=bool(options.get("check_local_models", True)),
        licences_path=str(options.get("licences", "") or ""),
        sources=str(options.get("sources", "") or ""),
        hash_models=bool(options.get("hash_models")),
        # The canvas knows what the open workflow is called; nothing on the
        # server does, because a posted graph has no file behind it.
        name=str(options.get("name", "") or ""),
    )


def _payload(report) -> dict[str, Any]:
    return {
        "html": html_report.render(report),
        "markdown": md_report.render(report),
        "report": json.loads(json.dumps(report.to_dict(), default=str)),
        "summary": {
            "licences": report.licensing.headline,
            "licence_counts": report.licensing.counts,
            "risk": report.risk.score,
            "risk_band": report.risk.band,
            "automation": report.automation.index,
            "automation_band": report.automation.band,
            "models": len(report.models),
            "packs": len([p for p in report.packs if p.identified]),
            "counts": report.risk.counts(),
            "top": [
                {"severity": f.severity, "title": f.title,
                 "recommendation": f.recommendation}
                for f in report.risk.findings if f.severity in ("critical", "high")
            ][:3],
        },
    }
