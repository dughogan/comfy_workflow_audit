# ComfyAudit Review (web)

The legal team's view of a ComfyAudit delivery package, as a static web page in Comfy's
look. Studio legal and production staff open the `.delivery.zip` that **Package for
delivery** made in ComfyUI, see what the artist's sign-off decided and what it left for
legal, answer what is theirs to answer, and clear the shot or send it back.

There is no hosted copy to rely on: **each studio deploys its own**. It is plain HTML, CSS
and JavaScript with no build step and no dependencies.

## How it keeps studio data in the studio

- **The page reads packages in the browser.** The zip is opened with the browser's own
  `DecompressionStream`, every file is fingerprinted (WebCrypto SHA-256) against
  `<shot>.package.json`, and the shot history's hash chain is recomputed (`package.js`).
  Nothing is uploaded and nothing is stored.
- **Anything written goes to the studio's own server.** Answers, shot evidence notes and
  decisions are signed into the shot's history by `python -m comfyaudit.review` running on
  the studio's network (the same signing key, keyring, ledger and answers file as its
  ComfyUI machines). The page only talks to the address a reviewer gives it in
  *Studio server*; with none, it is read-only.
- **Package documents open sandboxed.** The legal report, licence audit and life page run
  in a sandboxed frame with an origin of their own, so their content cannot reach the page,
  its settings or the studio server.
- **Demo mode** (`?demo`, or *Try the demo show*) opens two packages of a fictional show
  bundled in `demo/`. Answers and decisions there stay in the tab and are never signed.

## Deploy the page

Any static host works: Vercel, Netlify, S3 + CloudFront, nginx on an internal box, or the
studio's intranet. Serve this folder as it is.

- **Vercel:** `vercel deploy --prod` from this folder. `vercel.json` sets the headers.
- **Elsewhere:** copy the `Content-Security-Policy` and other headers from `vercel.json`
  into your server's configuration. Inline scripts are allowed only because the sandboxed
  document frame inherits the page's policy; the page itself builds everything with
  `textContent`, never from HTML strings.
- **Air-gapped:** it runs with no internet access at all. Only the Geist font comes from
  Google Fonts, and the page falls back to the system font without it.

Then tell ComfyUI where it is, so **Package for delivery** points artists and the package
README at it: set `COMFYAUDIT_REVIEW_URL=https://<your review page>` in the ComfyUI
server's environment. With it unset, nothing links anywhere.

## Run the review server

On a machine on the studio network:

```bash
COMFYAUDIT_REVIEW_CORS_ORIGINS=https://<your review page>
COMFYAUDIT_HISTORY_KEYRING=/secure/keyring.json        # each person's own signing key
COMFYAUDIT_TRUSTED_USER_HEADER=X-Studio-User           # set by your sign-in proxy
COMFYAUDIT_HISTORY_LEDGER=/shared/comfyaudit/ledger.jsonl
COMFYAUDIT_ANSWERS_FILE=/shared/comfyaudit/answers.json
python -m comfyaudit.review                             # 127.0.0.1:8095 by default
```

- **HTTPS:** serve it over HTTPS, behind the studio's sign-in proxy. Browsers block an
  https page from calling plain http, except on `localhost`.
- **Sign-in header:** the proxy must replace that header on every request, and the server
  must be reachable only through the proxy.
- **Without a keyring:** reviewers type their name, and the studio key
  (`COMFYAUDIT_HISTORY_KEY_FILE`) signs.
- **Without any key:** the server is read-only.
- **More variables:** `COMFYAUDIT_REVIEW_DIR`, `_HOST`, `_PORT`, `_INBOX` and `_MAX_MB`
  are described in `review/app.py`.
- **ShotGrid (beta):** with `COMFYAUDIT_SHOTGRID_*` set, decisions are also posted as a
  Note on the shot.

## Rebuild the bundled data

`python tools/build_review_web.py` (from the pack folder) regenerates `data/rules.json`
from the rules knowledge base and the demo packages in `demo/`. Run it after the
regulations knowledge base changes. The demo packages are signed with a demo key that is
published in that script, so their signatures prove nothing.

## Local preview

```bash
python -m http.server 8096 --directory review-web
```

Add `http://localhost:8096` to `COMFYAUDIT_REVIEW_CORS_ORIGINS` to try it against a local
review server.
