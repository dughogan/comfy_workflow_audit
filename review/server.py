"""HTTP for ComfyAudit Review: standard library ``http.server``, no JavaScript needed.

Routes (every GET page also answers ``?format=json`` or ``Accept: application/json``
with the data it is drawn from; every POST also takes a JSON body and answers JSON):

    GET  /                      shots, shows, the inbox, and opening a package
    POST /open                  a package: multipart ``package`` file, ``inbox`` name, or a raw zip body
    GET  /p/<id>                the shot: decision, open items, inherited answers, integrity
    POST /p/<id>/answer         tag, note (scope is the item's own: shot, show or studio)
    POST /p/<id>/decide         decision=clear|send-back, reason, accept_open
    GET  /p/<id>/workflow.json  the workflow with every decision made here, to give the artist
    GET  /p/<id>/file/<path>    a file from the package as received (HTML sandboxed)
    GET  /show/<production>     a show's shots and its answers
    GET|POST /me                who is reviewing (only when names are declared, not signed in)

Who someone is comes from the studio's sign-in header when
``COMFYAUDIT_TRUSTED_USER_HEADER`` is set (then their own key signs), and is
otherwise the name they give, kept in a cookie (then the studio key signs).
The same deployment rule as ComfyUI applies: the proxy must replace that
header, and this server must be reachable only through it.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
from email.parser import BytesParser
from email.policy import HTTP
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlsplit

from ..core.report import machine
from ..server import history_service
from . import app as app_mod
from . import pages

DONE = {
    "opened": "Package opened and checked.",
    "answered-shot": "Recorded in this shot's history, signed.",
    "answered-show": "Recorded once for the whole show. Every shot of the show inherits it.",
    "answered-studio": "Recorded once for the whole studio. Every shot inherits it.",
    "clear": "Shot cleared. The decision is signed into its history.",
    "send-back": "Shot sent back. The decision is signed into its history.",
    "me": "Saved. Your name is recorded with what you decide here.",
}

#: The app's own pages run no script and load nothing from elsewhere.
PAGE_CSP = ("default-src 'none'; style-src 'unsafe-inline'; img-src data:; form-action 'self'; "
            "frame-ancestors 'none'; base-uri 'none'")
#: Package documents are the studio's own reports, but their content came from
#: a workflow: they run sandboxed, in an origin of their own, unable to reach
#: this app or post anything.
FILE_CSP = ("sandbox allow-scripts allow-popups allow-popups-to-escape-sandbox; default-src 'none'; "
            "script-src 'unsafe-inline'; style-src 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src https://fonts.gstatic.com data:; img-src data: blob:; media-src data: blob:")


def cors_origins() -> set[str]:
    """Origins allowed to call this server from a browser: the studio's own copy of the
    Review web front end (``review-web/``), e.g. ``https://review.studio.example``.
    ``COMFYAUDIT_REVIEW_CORS_ORIGINS``, comma-separated, exact. None by default."""
    return {o.strip().rstrip("/") for o in os.environ.get("COMFYAUDIT_REVIEW_CORS_ORIGINS", "").split(",")
            if o.strip()}


class Handler(BaseHTTPRequestHandler):
    review: app_mod.Review
    server_version = "ComfyAuditReview"

    # -- plumbing ---------------------------------------------------------------
    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: D102
        sys.stderr.write(f"[review] {self.address_string()} {fmt % args}\n")

    def _cookies(self) -> cookies.SimpleCookie:
        jar = cookies.SimpleCookie()
        try:
            jar.load(self.headers.get("Cookie", ""))
        except cookies.CookieError:
            pass
        return jar

    def _cookie(self, name: str) -> str:
        c = self._cookies().get(name)
        return unquote(c.value) if c else ""

    def _csrf_token(self) -> str:
        token = self._cookie("ca_csrf")
        if not token:
            token = getattr(self, "_new_csrf", "") or secrets.token_urlsafe(24)
            self._new_csrf = token
        return token

    def _signed_in(self) -> str:
        name = os.environ.get(history_service.TRUSTED_USER_HEADER, "")
        if not name:
            return ""
        values = self.headers.get_all(name) or []
        if len(values) > 1:
            raise app_mod.ReviewError(f"The sign-in header {name} came more than once, so who is "
                                      "signed in cannot be told. Check that the studio's proxy "
                                      "replaces it.")
        return str(values[0]).strip() if values else ""

    def _me(self) -> dict[str, Any]:
        try:
            return app_mod.identity(self._signed_in(), self._cookie("ca_name"))
        except app_mod.ReviewError as exc:
            return {"mode": "sign-in", "name": "", "role": "", "can_sign": False, "key": "",
                    "problem": str(exc)}

    def _person(self, fields: dict[str, str] | None = None) -> app_mod.Person:
        # Names typed by the reviewer (no studio sign-in): the cookie on this
        # server's own pages, or ``reviewer`` in a JSON body from the studio's
        # front end, where a cross-site cookie would not come back. Either is
        # only a declared name; with a sign-in header it is ignored.
        declared = self._cookie("ca_name") or (fields or {}).get("reviewer", "")
        return app_mod.person(self._signed_in(), declared)

    def _wants_json(self) -> bool:
        query = parse_qs(urlsplit(self.path).query)
        if query.get("format", [""])[0] == "json" or self.headers.get("Origin", "") in cors_origins():
            return True
        accept = self.headers.get("Accept", "")
        return accept.split(",")[0].strip().lower() == "application/json" or \
            self.headers.get("Content-Type", "").lower().startswith("application/json")

    def _send(self, status: int, body: bytes, ctype: str, extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Cache-Control", "no-store")
        origin = self.headers.get("Origin", "")
        if origin and origin in cors_origins():
            # The studio's front end, on its own origin. Credentials so the
            # studio's sign-in proxy (and its header) applies to its calls too.
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Credentials", "true")
            self.send_header("Access-Control-Expose-Headers", "Content-Disposition")
            self.send_header("Vary", "Origin")
        if getattr(self, "_new_csrf", ""):
            self.send_header("Set-Cookie", f"ca_csrf={self._new_csrf}; Path=/; HttpOnly; SameSite=Strict")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, kind: str, data: Any) -> None:
        payload = machine.stamp(kind, data) if isinstance(data, dict) else data
        self._send(status, json.dumps(payload, indent=1, default=str).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _html(self, status: int, text: str) -> None:
        self._send(status, text.encode("utf-8"), "text/html; charset=utf-8",
                   {"Content-Security-Policy": PAGE_CSP, "X-Frame-Options": "DENY"})

    def _redirect(self, where: str) -> None:
        self._send(303, b"", "text/plain", {"Location": where})

    def _fail(self, status: int, message: str) -> None:
        if self._wants_json():
            self._json(status, "review-error", {"error": message, "status": status})
        else:
            self._html(status, pages.error_page(message, self._me(), status))

    def _msg(self) -> dict[str, str]:
        done = parse_qs(urlsplit(self.path).query).get("done", [""])[0]
        return {"flash": DONE.get(done, "")}

    # -- reading requests ---------------------------------------------------------
    def _body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        if length > self.review.config.max_bytes + 1024 * 1024:
            raise app_mod.ReviewError("That upload is larger than this server accepts "
                                      "(COMFYAUDIT_REVIEW_MAX_MB).")
        return self.rfile.read(length) if length else b""

    def _form(self) -> tuple[dict[str, str], dict[str, tuple[str, bytes]]]:
        """Fields and files of a POST, whatever it was sent as."""
        ctype = self.headers.get("Content-Type", "")
        raw = self._body()
        low = ctype.lower()
        if low.startswith("application/json"):
            try:
                data = json.loads(raw or b"{}")
            except ValueError as exc:
                raise app_mod.ReviewError("The request body is not JSON.") from exc
            return {k: str(v) if not isinstance(v, bool) else ("1" if v else "") for k, v in data.items()}, {}
        if low.startswith("application/zip") or low.startswith("application/octet-stream"):
            name = parse_qs(urlsplit(self.path).query).get("filename", [""])[0]
            return {}, {"package": (name, raw)}
        if low.startswith("multipart/form-data"):
            msg = BytesParser(policy=HTTP).parsebytes(
                b"Content-Type: " + ctype.encode("latin-1") + b"\r\n\r\n" + raw)
            fields, files = {}, {}
            for part in msg.iter_parts():
                name = part.get_param("name", header="content-disposition")
                if not name:
                    continue
                payload = part.get_payload(decode=True) or b""
                if part.get_filename() is not None:
                    files[name] = (part.get_filename(), payload)
                else:
                    fields[name] = payload.decode("utf-8", "replace")
            return fields, files
        parsed = parse_qs(raw.decode("utf-8", "replace"), keep_blank_values=True)
        return {k: v[-1] for k, v in parsed.items()}, {}

    def _check_post(self, fields: dict[str, str]) -> None:
        """Only this app's own pages, or a JSON client, may change anything."""
        origin = self.headers.get("Origin", "")
        if origin and origin in cors_origins():
            if not self.headers.get("Content-Type", "").lower().startswith(
                    ("application/json", "application/zip", "application/octet-stream")):
                raise PermissionError("The Review front end sends JSON or a zip, not a form.")
            return
        if origin and origin != "null" and urlsplit(origin).netloc != self.headers.get("Host", ""):
            raise PermissionError("This request came from another site.")
        if origin == "null":
            raise PermissionError("This request came from a sandboxed page.")
        ctype = self.headers.get("Content-Type", "").lower()
        if ctype.startswith("application/json") or ctype.startswith("application/zip") \
                or ctype.startswith("application/octet-stream"):
            # Browsers cannot send these cross-site without a preflight this server never allows.
            return
        sent, have = fields.get("csrf", ""), self._cookie("ca_csrf")
        if not have or not secrets.compare_digest(sent, have):
            raise PermissionError("This form has expired. Go back, reload the page and try again.")

    # -- routes ---------------------------------------------------------------------
    def do_OPTIONS(self) -> None:  # noqa: N802
        """CORS preflight, for the studio's front end only."""
        origin = self.headers.get("Origin", "")
        if origin not in cors_origins():
            return self._send(403, b"", "text/plain")
        extra = {"Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                 "Access-Control-Allow-Headers": "Content-Type, Accept",
                 "Access-Control-Max-Age": "600"}
        if self.headers.get("Access-Control-Request-Private-Network", "").lower() == "true":
            # A public page calling a server on the studio network (Chrome's
            # Private Network Access); only ever for an origin listed above.
            extra["Access-Control-Allow-Private-Network"] = "true"
        self._send(204, b"", "text/plain", extra)

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_GET(self) -> None:  # noqa: N802
        path = unquote(urlsplit(self.path).path)
        try:
            self._route_get(path)
        except app_mod.ReviewError as exc:
            self._fail(404 if "no such" in str(exc).lower() or "not part" in str(exc) else 400, str(exc))
        except Exception as exc:  # noqa: BLE001
            self.log_message("error on %s: %r", path, exc)
            self._fail(500, f"Something went wrong on the server: {exc}")

    def _route_get(self, path: str) -> None:
        me, csrf = self._me(), self._csrf_token()
        parts = [p for p in path.split("/") if p]
        if not parts:
            data = self.review.home()
            return self._json(200, "review-home", data) if self._wants_json() else \
                self._html(200, pages.home(data, me, csrf, **self._msg()))
        if parts == ["status"]:
            return self._json(200, "review-status", app_mod.status(me))
        if parts == ["me"]:
            return self._json(200, "review-identity", me) if self._wants_json() else \
                self._html(200, pages.me_page(me, csrf, app_mod.per_person(), **self._msg()))
        if parts[0] == "show" and len(parts) == 2:
            data = self.review.show(parts[1])
            return self._json(200, "review-show", data) if self._wants_json() else \
                self._html(200, pages.show(data, me, csrf, **self._msg()))
        if parts[0] == "p" and len(parts) >= 2:
            pid = parts[1]
            if len(parts) == 2:
                view = self.review.shot(pid)
                return self._json(200, "review-shot", view) if self._wants_json() else \
                    self._html(200, pages.shot(view, me, csrf, **self._msg()))
            if parts[2:] == ["workflow.json"]:
                name = (self.review.meta(pid).get("workflow_file") or "workflow.json")
                body = json.dumps(self.review.workflow(pid), indent=1, default=str).encode("utf-8")
                return self._send(200, body, "application/json; charset=utf-8", {
                    "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})
            if parts[2] == "file" and len(parts) > 3:
                member = "/".join(parts[3:])
                data, media = self.review.file(pid, member)
                inline = media in ("text/html", "application/json", "text/plain")
                ctype = media + ("; charset=utf-8" if media.startswith("text/") or media.endswith("json") else "")
                return self._send(200, data, ctype, {
                    "Content-Security-Policy": FILE_CSP,
                    "Content-Disposition": ("inline" if inline else "attachment")
                    + f"; filename*=UTF-8''{quote(os.path.basename(member))}"})
        self._fail(404, "There is no such page.")

    def do_POST(self) -> None:  # noqa: N802
        path = unquote(urlsplit(self.path).path)
        parts = [p for p in path.split("/") if p]
        fields: dict[str, str] = {}
        try:
            fields, files = self._form()
            self._check_post(fields)
            self._route_post(parts, fields, files)
        except PermissionError as exc:
            self._fail(403, str(exc))
        except app_mod.ReviewError as exc:
            if self._wants_json():
                return self._json(400, "review-error", {"error": str(exc), "status": 400})
            # Show the page again with what went wrong, so nothing typed is far away.
            me, csrf = self._me(), self._csrf_token()
            try:
                if len(parts) >= 2 and parts[0] == "p":
                    return self._html(400, pages.shot(self.review.shot(parts[1]), me, csrf, error=str(exc),
                                                      draft=fields if parts[2:] == ["decide"] else None))
                if parts == ["me"]:
                    return self._html(400, pages.me_page(me, csrf, app_mod.per_person(), error=str(exc)))
                return self._html(400, pages.home(self.review.home(), me, csrf, error=str(exc)))
            except app_mod.ReviewError:
                self._fail(400, str(exc))
        except Exception as exc:  # noqa: BLE001
            self.log_message("error on %s: %r", path, exc)
            self._fail(500, f"Something went wrong on the server: {exc}")

    def _route_post(self, parts: list[str], fields: dict[str, str],
                    files: dict[str, tuple[str, bytes]]) -> None:
        if parts == ["me"]:
            if app_mod.per_person():
                raise app_mod.ReviewError("This server takes who you are from the studio's sign-in.")
            name = " ".join(fields.get("name", "").split())[:120]
            if not name:
                raise app_mod.ReviewError("Give your name.")
            if self._wants_json():
                return self._json(200, "review-identity", app_mod.identity("", name))
            self._send(303, b"", "text/plain", {
                "Location": "/?done=me",
                "Set-Cookie": f"ca_name={quote(name)}; Path=/; HttpOnly; SameSite=Strict; Max-Age=31536000"})
            return
        if parts == ["open"]:
            me = self._me()
            by = me.get("name", "")
            if fields.get("inbox"):
                pid = self.review.add_from_inbox(fields["inbox"], by)
            elif "package" in files and files["package"][1]:
                pid = self.review.add(files["package"][1], files["package"][0], by)
            else:
                raise app_mod.ReviewError("Choose a delivery package to open.")
            if self._wants_json():
                return self._json(200, "review-opened", {"id": pid, "url": f"/p/{pid}"})
            return self._redirect(f"/p/{pid}?done=opened")
        if len(parts) == 3 and parts[0] == "p" and parts[2] in ("answer", "decide"):
            pid, who = parts[1], self._person(fields)
            if parts[2] == "answer":
                out = self.review.answer(pid, tag=fields.get("tag", ""), note=fields.get("note", ""),
                                         scope=fields.get("scope", ""), who=who)
                done = f"answered-{out['scope']}"
            else:
                out = self.review.decide(pid, decision=fields.get("decision", ""),
                                         reason=fields.get("reason", ""), who=who,
                                         accept_open=bool(fields.get("accept_open")))
                done = out["recorded"]
            if self._wants_json():
                return self._json(200, "review-recorded", out)
            return self._redirect(f"/p/{pid}?done={done}")
        self._fail(404, "There is no such page.")


def make_server(review: app_mod.Review | None = None, host: str = "", port: int | None = None) -> ThreadingHTTPServer:
    review = review or app_mod.Review()
    handler = type("ReviewHandler", (Handler,), {"review": review})
    return ThreadingHTTPServer((host or review.config.host,
                                review.config.port if port is None else port), handler)
