# pexafy-mcp

[![CI](https://github.com/Pexafy/pexafy-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/Pexafy/pexafy-mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

**Stock photo search for AI assistants.** An [MCP](https://modelcontextprotocol.io)
server that lets Claude, ChatGPT or any MCP client find real, royalty-free
photographs — by describing a scene in plain language, from an example image, or
"more like this one" — and show them as an interactive grid **inside the
conversation**.

> Remote MCP, OAuth or an API key, 5 tools, results drawn inline where the host renders MCP Apps.

The product page, with the same steps in twenty-three languages, is at
[pexafy.com/mcp](https://pexafy.com/mcp/).

---

## Use it (nothing to install)

A hosted server runs at:

```
https://mcp.pexafy.com/mcp
```

It speaks Streamable HTTP and authenticates with **OAuth 2.1** — you sign in to
Pexafy in a browser window and the connector receives its own credentials. There is
no API key to generate, paste into a JSON file, or rotate later.

### Claude (web and desktop)

1. Open **Customize → Connectors** (on Team/Enterprise, an owner adds it once under
   **Organization settings → Connectors**).
2. Click **Add custom connector**.
3. Paste `https://mcp.pexafy.com/mcp` and confirm. Under **Authentication**, keep
   **Sign in now** (Claude detects it): on Claude, Pexafy is used with an account.
4. Sign in to Pexafy in the window that opens. Done — ask Claude for a photo.

### Claude Code

```bash
claude mcp add --transport http pexafy https://mcp.pexafy.com/mcp
```

### Any other MCP client

Point it at the same URL with the `streamable-http` transport. Clients that don't
implement OAuth can authenticate instead with a Pexafy API key sent as
`Authorization: Bearer <key>` or `x-api-key: <key>` — get one from the
[dashboard](https://pexafy.com/dashboard/api-keys/).

Liveness: [`GET /health`](https://mcp.pexafy.com/health) (public, no auth).

Also listed in the [official MCP registry](https://registry.modelcontextprotocol.io)
as `com.pexafy/pexafy-mcp`, and on
[Smithery](https://smithery.ai/servers/pexafy/pexafy-mcp) — where a hosted
gateway URL is available for clients that prefer it.

### What it costs

The Free plan covers 5,000 searches a month with one connector — enough for regular
use, no card required. Higher tiers are on the [pricing page](https://pexafy.com/pricing/).
Close to the end of an allowance the grid shows how many searches are left; at the end,
the assistant says so in the chat — the limit and when it resets — instead of failing
with an opaque error.

---

## Tools

Five tools, all read-only: they search the catalogue or read what the grid holds, and
change nothing in anybody's account. `tools/list` presents them in this order. The
hosted server lists all five; a self-hosted one lists those its configuration enables
(see [Self-host](#self-host)).

| Tool | What it is for |
|---|---|
| `search_photos` | Find photographs by describing them in words |
| `search_photos_by_image` | Find photographs that look like a reference: an image, a link to one, or a Pexafy photo |
| `get_photo_file_by_photo_id` | Hand one photograph over as an image file, to place in a document or edit |
| `get_grid_selected_photos` | Read the photographs the person liked in the grid |
| `connect_account` | Ask the host to connect a Pexafy account |

The two searches keep the names 0.4.x published, so an installed connector keeps them;
what 0.4.x hosts still send is covered in [Upgrading from 0.4.x](#upgrading-from-04x).

### `search_photos` — describe the photograph

| Parameter | Type | Notes |
|---|---|---|
| `english_search_sentence` | string | **Required.** One concise English sentence about what should be in the photograph; keywords work too. 1–250 characters: a longer one is refused, with a message saying why, before anything is searched. |
| `explicit_orientation_filter` | array | Any of `landscape`, `portrait`, `square`. Several values widen the result (they OR together). A hard filter applied before ranking, so it is sent only when a shape was asked for; any other value is refused. |

The match is semantic, so a sentence beats a list of tags. Every other filter the REST
API offers — colour, source, licence, photographer, date, paging — is left off the tool
on purpose: an assistant fills in whatever it is offered, and a filter nobody asked for
narrows the catalogue for nothing. They all remain available on the
[REST API](https://docs.pexafy.com) for code that needs them.

### `search_photos_by_image` — start from a picture

Give **exactly one** reference; two are refused before anything is fetched.

| Parameter | Type | Notes |
|---|---|---|
| `photo_id` | string | The UUID of a Pexafy photo from an earlier result — "more like this one". |
| `image_url` | string | A public http(s) URL of an image. The server downloads it: 10 MB at most, JPEG, PNG, WebP or AVIF, up to 3 redirects, public addresses only. |
| `image_file` | object | Filled in by hosts that upload a file into the call (ChatGPT): `download_url` and `file_id`, optionally `mime_type` and `file_name`. |
| `image_base64` | string | The image's bytes in base64, or a `data:` URL, for programmatic clients. Same limits: 10 MB, JPEG, PNG, WebP or AVIF. |
| `english_search_sentence` | string | Optional, up to 250 characters. With a `photo_id`, the words that photo was found under, which keep the results on its subject; with an image, only what should change or stay ("at night"). The picture stays the main signal. |
| `explicit_orientation_filter` | array | As on `search_photos`. |

### `get_photo_file_by_photo_id` — the photograph itself

| Parameter | Type | Notes |
|---|---|---|
| `photo_id` | string | **Required.** The UUID of a photo from an earlier result; a rank sent in its place is refused. |

A link shows a photograph; an assistant asked to put one on a slide or in a document
needs the file. The answer carries a line naming it (file name, size, the dimensions
served, licence, source, `photo_id`, its page at the source and the credit line to
display), the image itself — at most 1280 pixels wide — as MCP image content, and, for
ChatGPT, the same bytes as an embedded resource (`pexafy://photo/…`), the form a host can
attach (`PEXAFY_PHOTO_FILE_RESOURCE`).
Unsplash and Pexels files come from their own CDN at 1280 pixels, the others from
Pexafy's thumbnail proxy. A file over 6 MB is refused rather than truncated. The tool
exists only on a server configured with that proxy (see [Configuration](#configuration)).

### `get_grid_selected_photos` — what the person liked

No parameters. Returns the photographs the person liked with the ♥ in the grid, in the
order they liked them — `rank` #1..#n, the numbers the grid draws on them — each with its
`photo_id`, photographer, source, licence, pixel size, orientation, description, Pexafy
page, image link and credit line, plus `selection_count` and a `note` that tells "nothing
liked yet" from "a later search replaced that grid".

The grid posts the selection to the server itself (`POST /selection`, with a short-lived
token signed by the server and carried on the result it is drawing), because the host's
own channel for widget state is not reliably passed back to the model. Selections are
kept in the server's memory for an hour. The tool is served only alongside the grid (see
[Configuration](#configuration)).

### `connect_account` — attach an account

| Parameter | Type | Notes |
|---|---|---|
| `check_only` | boolean | Report whether an account is connected, and the allowance in force, without opening anything. The grid sets it; it stays unset when the person wants to connect or sign in. |

Searches nothing. Its answer is a refusal carrying the OAuth challenge
(`_meta["mcp/www_authenticate"]`), which is what makes a host such as ChatGPT open its
own "connect account" flow. A caller who is already signed in is told so. Registered
only when account linking is on (`PEXAFY_ACCOUNT_LINKING`).

The server also serves a prompt, `find_photos` (one argument, `scene`), that a person
can pick from their client's menu.

### What a search returns

Up to 16 photographs in `data`, each with:

- `rank` — its place in this answer, the handle for "the second one" (the grid draws no number on a result);
- `photo_id` — what every other tool takes;
- `urls.regular` — the link to the photograph, and `source_image_url`, its page at the source library;
- `attribution.plain` — the credit line to display, with `photographer_full_name`, `source` and `license_type`;
- `width`, `height`, `orientation` and `alt_description`;
- `preview_url` and `preview_url_large` — signed thumbnails for the grid (480 pixels, valid 30 days; 1280 pixels, no expiry), when the thumbnail proxy is configured.

Around them: `budget` (only close to the end of an allowance) and `notice` (why an
answer is empty). Every field is declared in the tools' output schema. Nothing else
rides along: no request id, no timing, no plan name. What only the grid needs travels
in the result's `_meta`, the half of an answer a host keeps from the model: the image
links (`pexafy/previews`), who is asking and the links to sign in or to the account
(`pexafy/account`),
the link that opens the search on pexafy.com (`pexafy/cta`), and the question to ask
again (`pexafy/origin`).

---

## The grid

In hosts that render [MCP Apps](https://modelcontextprotocol.io) — Claude and ChatGPT
among them — both searches draw their results as a grid in the conversation:

- **Thumbnails**: 6, then 6 more, then 4, in two or three columns depending on the width,
  then an **Open in Pexafy** link. Each thumbnail carries a mark for its shape and a ♥.
- **The ♥** likes a photograph. Liked photos stay under the grid, numbered #1..#n in the
  order they were liked; the assistant reads them with `get_grid_selected_photos`, and the
  grid also names them to the model through the host's context channel.
- **The deck**: tap a thumbnail and the photos open one at a time in the grid's place,
  with their credit, licence and size — swipe right to like, left to skip, up for more
  like this one, down to close, or press the same verbs as buttons. On a phone the deck
  asks the host for full screen.
- **≈ (more like this one)** runs `search_photos_by_image` from inside the grid, with that
  photo's `photo_id` and the words of the search, and repaints the grid in place where the
  host lets the frame call a tool (otherwise it asks the assistant). Each step joins a
  trail across the head, six deep at most, and every step on it is a way back.
- **The shape filter** in the head asks the same question again for one or more shapes,
  where the host lets the frame call a tool; "Every shape" goes back to the page already
  in hand, with no new call.
- **The account button** in the corner: the way to sign in, or what is left of the
  allowance. When an allowance is spent, the grid says so where the results would be.

Everything the grid shows is in the tool result. Thumbnails load from Pexafy's signed
thumbnail CDN, the one image origin its content security policy allows, and the signing
secret never reaches the frame. The same HTML is served at `GET /widget` for a web page
that embeds the grid. Where no MCP App is rendered, the results are the structured data
above.

---

## Accounts and allowances

Every search spends an allowance, and the server works out whose:

- **OAuth.** A client that speaks OAuth (Claude, ChatGPT) follows the 401's
  `WWW-Authenticate` header to the authorization server — Pexafy's, for the hosted
  server. The token is resolved to the user's own API key on every request.
- **API key.** `Authorization: Bearer pexafy_…` or `x-api-key: pexafy_…`, forwarded to
  the API as the caller's own key.
- **No credential — off by default.** With `PEXAFY_ANON_ENABLED=1`, a caller who sends
  nothing is served on the API's daily allowance for callers without an account,
  provided the server can tell who they are: ChatGPT's `openai/subject` (marked as
  attested when the request comes from an address OpenAI publishes), or else the
  caller's IP address. An address that stands for a crowd — Claude.ai's egress range by
  default — is not an identity: that caller gets the 401 that starts OAuth. The identity
  reaches the API as a signed, short-lived `X-Pexafy-Principal` header next to the
  server's own key (`PEXAFY_API_KEY`), and the API verifies it before counting anything.
  Neither the subject nor the address travels: only an HMAC of it, keyed by
  `PEXAFY_ANON_SECRET`, which names the caller without revealing them.

With account linking on (`PEXAFY_ACCOUNT_LINKING`, the default), every tool declares both
`noauth` and `oauth2` in `securitySchemes` — usable without an account, and able to take
one — and `connect_account` lets a person attach their account through the host at any
time.

Whatever the way in, a request to the API carries the credential chosen here — the
caller's key, or the service key and the signed principal — `X-Source`, and HTTP's own
headers, on an allow-list. Nothing of the request the host sent is passed on: not its
address, user agent, cookies, `origin` or `referer`, nor ChatGPT's `x-openai-*` headers.
The API counts searches per address (60 a minute); so that it goes on counting each
caller apart rather than this server as one, the caller's address is named in
`X-Forwarded-For` by a pseudonym — an HMAC under a key the process draws at start and
stores nowhere, written as an address of `fd70:6578:6166::/48`, which the API cannot turn
back into the address ([`outbound.py`](src/pexafy_mcp/outbound.py)).

Limits come back as sentences an assistant can act on, and they state the limit without
selling anything: a rate limit says how long to wait before one retry; a spent daily or
monthly allowance says when it resets and comes back as an empty result with a `notice`,
which the grid draws in place of the photographs.

---

## Upgrading from 0.4.x

A directory keeps the tool list it scanned and sends calls to the live server, so a host
can go on sending what 0.4.x published after the server has moved on. All of it is still
accepted, translated before anything else reads the call, and logged — the rate of those
log lines says when the translation can go ([`compat.py`](src/pexafy_mcp/compat.py)):

| Sent by a 0.4.x host | Handled as |
|---|---|
| `get_similar_photos(photo_id)` | `search_photos_by_image` with that `photo_id` |
| `q`, or `query` (never published, sent by some assistants) | `english_search_sentence`, on both searches |
| `orientation` (a single string) | `explicit_orientation_filter` (a one-item list) |
| `color_name`, `color_hex`, `color_tolerance`, `source`, `license_type`, `after_date`, `photographer`, `cursor` | ignored — the search runs without them |
| `per_page`, `limit` (never published, sent by some assistants) | ignored — the page size is the server's |
| `text_alpha` (by-image search) | ignored — the API's own balance applies |

A published ChatGPT app is different: OpenAI re-scans its `tools/list` and removes a tool
the list no longer names. `get_similar_photos` stayed in the list OpenAI hosts read until
the app's 1.0.0 tools were live; no host sees it now, and a call under its name is still
answered.

ChatGPT also keeps the grid it was served, under the same URI, for up to an hour after a
deploy, and draws the new answers with it. The 0.4.12 grid reads only the answer's
structured half, where 1.0.0 gives the previews as a path without address and no `urls`
wherever a grid is drawn: it would show no thumbnail. So until `PEXAFY_LEGACY_GRID_UNTIL`
— an instant in UTC, as epoch seconds or ISO 8601 (`2026-10-05T03:00:00Z`) — an OpenAI
host's answer keeps everything that grid reads: the previews as whole links, `urls` in
every size, the photographer's link, the colour, the date, the long description and
`cta`. The text the model reads keeps no link, the tool list does not change, and no
other host sees a difference. Unset, empty or `0`, there is no window; a value that is
not an instant opens none and is logged as a warning. On deployment, set it to the time
of the deploy plus two hours.

A 0.4.x text search sent without `q` — legal then, when filters alone could search — gets
a message saying the sentence is required. The names briefly served by a preview server
(`search_photos_from_unsplash_pexels_pixabay_by_text` and `…_by_image`, `get_photo_file`,
`get_selected_photos`) are translated the same way. What changed and why is in the
[changelog](CHANGELOG.md).

---

## Self-host

You don't need to — the hosted server above is the intended way in. But the server is a
plain client of the [Pexafy API](https://api.pexafy.com/schema.json), so you can run your
own against your own key.

A self-hosted server lists the two searches, plus `connect_account` while account
linking is on (`PEXAFY_ACCOUNT_LINKING`, on by default; the compose example below turns
it off): three tools, or two. `get_photo_file_by_photo_id`, `get_grid_selected_photos`
and the grid need a thumbnail proxy whose signing secret you hold
(`PEXAFY_THUMB_BASE_URL`, `PEXAFY_THUMB_HMAC_SECRET`); with one, it lists four tools or
five.

Requires Python 3.12+.

```bash
git clone https://github.com/Pexafy/pexafy-mcp.git && cd pexafy-mcp
./run.sh setup        # venv + editable install + seed .env
# edit .env — set PEXAFY_API_KEY
./run.sh dev          # stdio, for Claude Desktop / Claude Code
```

With the console script that `pip install .` installs. Installed outside the clone, it
does not read the clone's `.env`, so its settings come from the environment:

```bash
export PEXAFY_API_BASE_URL=https://api.pexafy.com PEXAFY_API_KEY=pexafy_api_…
pexafy-mcp                             # stdio (default)
PEXAFY_MCP_TRANSPORT=http pexafy-mcp   # remote Streamable HTTP
```

Claude Desktop / Claude Code, over stdio:

```json
{
  "mcpServers": {
    "pexafy": {
      "command": "/path/to/pexafy-mcp/.venv/bin/pexafy-mcp",
      "env": {
        "PEXAFY_API_BASE_URL": "https://api.pexafy.com",
        "PEXAFY_API_KEY": "pexafy_api_…"
      }
    }
  }
}
```

`command` is the absolute path of the console script, since the client does not start
it from your shell: `.venv/bin/pexafy-mcp` in the clone after `./run.sh setup`, or what
`which pexafy-mcp` prints where you ran `pip install .`. Without `PEXAFY_API_BASE_URL`,
here or in a `.env` the server finds, it calls `http://localhost:8000`, its default, and
no search succeeds.

Docker, over HTTP — see [`docker-compose.example.yml`](docker-compose.example.yml):

```bash
docker compose -f docker-compose.example.yml up -d
curl localhost:8765/health
```

The image itself defaults to **stdio**, the transport an MCP client uses to drive a
container, so it also works directly:

```bash
docker run -i --rm pexafy-mcp
```

That answers `initialize` and `tools/list` with no API key and no network — the text
search is generated from the vendored OpenAPI snapshot. A key is only needed to run a
search. Serving over HTTP is a matter of setting the transport, which the compose example
does.

### Configuration

Every setting is an environment variable, and every one of them is optional: with none
set, `pexafy-mcp` starts on stdio and answers `initialize` and `tools/list` offline. The
complete list, grouped, with each default, is [`.env.example`](.env.example). These are
the ones that change what the server is:

| Variable | Default | Purpose |
|---|---|---|
| `PEXAFY_MCP_TRANSPORT` | `stdio` | `stdio` for a local client, `http` to serve remotely |
| `PEXAFY_API_BASE_URL` | `http://localhost:8000` | Pexafy API root — `https://api.pexafy.com`, or your own deployment |
| `PEXAFY_API_KEY` | *(empty)* | The key used when a request brings none: stdio, and anonymous access |
| `PEXAFY_MCP_PUBLIC_URL` | `http://<host>:<port>` | This server's public URL: OAuth metadata, Claude's widget domain and — only when it is set — the grid's selection channel |
| `PEXAFY_THUMB_BASE_URL`, `PEXAFY_THUMB_HMAC_SECRET` | *(empty)* | The signed thumbnail proxy. Without both there is no grid — so neither the selection tool nor the file tool — and results stay structured data |
| `PEXAFY_OAUTH_RESOLVE_URL`, `PEXAFY_OAUTH_AS_URL`, `MCP_RESOLVE_SECRET` | *(empty)* | Run the HTTP transport as an OAuth resource server |
| `PEXAFY_ANON_ENABLED`, `PEXAFY_ANON_SECRET` | off | Serve callers with no credential (see above) |
| `PEXAFY_ACCOUNT_LINKING` | `1` | Declare `noauth` + `oauth2` on every tool and register `connect_account` |
| `PEXAFY_SIGN_IN_BY_401` | `1` | Outside ChatGPT, asking to connect is answered 401 so the client runs its own OAuth; the editor that signed in is then asked to at every start from that machine (OAuth on only) |
| `PEXAFY_COACH_ONCE` | `1` | The grid's first-steps coach plays once per person, not in every grid |
| `PEXAFY_REDIS_URL` | *(empty)* | Where the coach and editor sign-ins are remembered; empty = this process's memory, forgotten on a restart |
| `PEXAFY_LEGACY_GRID_UNTIL` | *(empty)* | Until this UTC instant (epoch seconds or ISO 8601), an OpenAI host's answers keep what the 0.4.12 grid, still in ChatGPT's cache, reads; set it to the deploy time + 2 hours |

An empty value is not always the same as an unset one — `PEXAFY_WEB_URL=` blanks the
site's address rather than keeping the default — so leave a line out, or commented, to
keep a default.

---

## How it works

```
src/pexafy_mcp/
├── server.py        # build_server(): the API client and its hooks, the hand-written tools, the HTTP routes
├── tooling.py       # tunes the OpenAPI-generated search for an LLM; tool-name registry; output schema
├── compat.py        # accepts the tool names and parameters 0.4.x published; the 0.4.12 grid's window
├── guards.py        # refuses a malformed photo_id, sentence or shape with a message saying what to send
├── previews.py      # signs the grid's thumbnail URLs and moves them to the result's _meta
├── origin.py        # the replayable form of a search, for the grid's shape filter
├── selection.py     # the photos liked in the grid: POST /selection and get_grid_selected_photos; the grid's view (POST /view)
├── coach.py         # the grid's first-steps coach, once per person (POST /coach)
├── store.py         # what outlives a process: the coach seen, editor sign-ins (Redis, or memory)
├── widget.py        # the MCP Apps UI resource: the grid, one self-contained HTML document
├── widget_i18n.py   # the grid's texts in the site's languages
├── anonymous.py     # who a caller without a credential is; signs X-Pexafy-Principal
├── outbound.py      # what a request to the API carries: an allow-list, the caller's address as a pseudonym
├── budget.py        # allowance headers → the grid's account button, notice and wall
├── linking.py       # connect_account, securitySchemes and the account-link challenge
├── limits.py        # plan-limit messages: rate, daily, monthly, API-key limit
├── auth.py          # OAuth resource server: token → the user's API key; a raw key as bearer
├── unauthorized.py  # readable 401 bodies that name both ways in
├── sessions.py      # answers the sessionless server/discover probe; session gauge for /metrics
├── hosts.py         # which host is calling (clientInfo): the grid's ui.domain, the file's second copy
├── observe.py       # logs which _meta keys a host sends (openai/subject as a digest)
├── env.py           # on/off settings read from the environment
├── __main__.py      # python -m pexafy_mcp
└── assets/          # vendored, shipped with the package:
    ├── openapi.json                    # snapshot of the Pexafy API spec the text search is generated from
    └── ext_apps_bundle.js              # @modelcontextprotocol/ext-apps SDK, inlined into the grid
```

- **One generated tool, four written by hand.** `search_photos` is generated from the
  Pexafy OpenAPI spec with `FastMCP.from_openapi()`, so the API stays the source of truth
  for the request. `tooling.py` then narrows it with an allow-list — `q` and `orientation`,
  shown to the model as `english_search_sentence` and `explicit_orientation_filter` — so a
  parameter the API gains later cannot reach the tool, and an endpoint that moves stops
  the server at load time instead of exposing the raw REST surface. The other four tools
  are written in `server.py`, `selection.py` and `linking.py`. `search_photos_by_image`
  posts to the same endpoint through the same HTTP client, so authentication, the page
  size of 16 and the shaping of each result apply to both.
- **No side effects on import.** `build_server()` assembles everything; importing the
  package does no network I/O and reads the vendored `assets/openapi.json`. `prepare.sh`
  regenerates it and the ext-apps bundle.
- **The grid is an MCP Apps UI resource** (`ui://pexafy/grid.html`). The ext-apps client
  is inlined, because the host's sandboxed iframe cannot fetch external scripts at
  runtime. The grid sets its text in the system font and ships no typeface.

Over HTTP, the server also answers:

| Route | What it is |
|---|---|
| `/mcp` | The MCP endpoint (Streamable HTTP) |
| `GET /health` | Liveness: status, tool count, OAuth on or off, transport |
| `GET /.well-known/mcp/server-card.json` | The tool catalogue, for directories that cannot authenticate |
| `GET /.well-known/oauth-protected-resource` (and `…/mcp`) | OAuth protected-resource metadata, when OAuth is on |
| `GET /.well-known/openai-apps-challenge` | OpenAI's domain-verification token, when one is set |
| `POST /selection` | Where the grid writes the photos the person liked |
| `POST /view` | Where the grid keeps its view, and reads it back, in a host that keeps none for it |
| `POST /coach` | Where the grid says it has shown its first-steps coach, which plays once per person |
| `GET /widget` | The grid as a web page, for a site that embeds it |
| `GET /favicon.ico`, `GET /favicon.svg` | The connector's icon, an SVG |
| `GET /metrics` | Prometheus metrics: sessions held, discovery probes answered; guarded by a bearer token when one is set |

## Development

```bash
./run.sh setup        # venv + editable install (with dev tools) + seed .env
./run.sh dev          # run over stdio
./run.sh run          # run the HTTP server locally
./run.sh inspect      # MCP Inspector
./run.sh test         # offline test suite (pytest)
./prepare.sh          # maintainers: regenerate the vendored assets/
```

The tests never reach the network: `tests/conftest.py` forces a harmless configuration,
ignores any `.env`, replaces the Pexafy API in memory, and fails any test that opens a
connection. CI runs them with `ruff check src/ tests/`. Contributions welcome — see
[CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT — see [LICENSE](LICENSE).

The package also redistributes third-party code (the `@modelcontextprotocol/ext-apps`
browser bundle and the libraries bundled into it), each under its own licence — see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
