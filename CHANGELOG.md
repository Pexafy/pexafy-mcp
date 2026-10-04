# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- **The server instructions open on when to use the server.** OpenAI asks for the most
  important details in the first 512 characters of this field. The sentence on what
  Pexafy is used to come first and took 167 of them, leaving room for one whole bullet
  of triggers. It now follows the bullets: three fit whole in the 512, with the start of
  the fourth. No word changed, and the length is the same.

### Fixed
- **≈ still finds similar photos when the host cannot run the grid's own call.** ChatGPT
  runs a call from the grid with code that it loads on first use. In some browsers that
  load fails every time (an extension, a stale cache; a private window works), and the
  grid showed the host's error as its toast: "MCP error -32000: Failed to fetch
  dynamically imported module: https://chatgpt.com/cdn/assets/…". The request now goes
  to the conversation instead, with the photo's `photo_id` and the words of the search,
  and the host runs it on its servers. After that failure the grid makes no call of its
  own: the next ≈ goes to the conversation at once, the shape filter is hidden, and
  "Sign in" asks the assistant. No toast shows the host's error text; it goes to the
  console.

## [1.0.0] — 2026-10-03

In short: five tools where there were three, a search surface of one sentence and one
shape, a grid that becomes the place to choose photographs, and optional access for
callers with no account. A host still holding the 0.4.x tool list keeps working — see
**Deprecated**.

### Added
- **`get_photo_file_by_photo_id` — the photograph itself.** A link is enough to show a
  photo, not to work on one: asked to crop a chosen photograph, an assistant holding only
  its URL said the file was not available and asked the person to send it. The tool
  returns one photo as a file — a line naming it (file name, size, the dimensions
  served, licence, source, `photo_id`, its page at the source, the credit line to
  display), the image as MCP image content, at most 1280 pixels wide, and — for ChatGPT —
  the same bytes as an embedded resource, the form a host can attach. Other hosts get one
  copy: Claude caps a tool result (about 150,000 characters on claude.ai, 25,000 tokens
  in Claude Code). `PEXAFY_PHOTO_FILE_RESOURCE` (`auto` by default) forces the copy for
  every host or for none. Unsplash and Pexels files come from their own CDN at 1280 pixels,
  since the thumbnail proxy holds Unsplash at 1080; the others from the proxy's
  permanent 1280-pixel file. A file over 6 MB is refused rather than truncated.
  Registered only when the thumbnail proxy is configured.
- **`get_grid_selected_photos` — what the person liked.** The grid tells the model what
  the reader likes through the host's own channels (`ui/update-model-context`, and
  `modelContent` in ChatGPT's `setWidgetState`), and ChatGPT still answered that the
  selection was not available to it (openai/openai-apps-sdk-examples#221). So the grid
  also posts the selection to the server — `POST /selection`, with a write token the
  server signs and hands to that grid in the `_meta` of the answer it draws — and this
  tool reads it back: every liked photo in order, `rank` #1..#n as drawn on them, with
  its metadata, plus `selection_count` and a `note`. A selection older than the last grid
  sent to the same caller is not served: keyed on the person alone, a shortlist from an
  earlier conversation was once returned as the current one. Kept in memory for an hour;
  `PEXAFY_SELECTION_TOOL=0` removes the tool. Registered only when the thumbnail proxy is
  configured: without it there is no grid, and nothing to like.
- **`connect_account` — attach an account without running out first.** It searches
  nothing: its answer is a refusal carrying `_meta["mcp/www_authenticate"]`, which is
  what makes ChatGPT open its own OAuth flow. An already connected caller is told so.
  With `check_only` — set by the grid, or for a question about the connection — it
  reports the connection and the current allowance without opening anything and without
  spending a search.
- **`photo_id` as a reference of `search_photos_by_image`.** "More like this one" is a
  search whose reference is a Pexafy photo: the tool posts `photo_id` and the words that
  photo was found under to `POST /api/v1/search/photos`, which keeps the neighbours on
  the subject. `GET /photos/{id}/similar`, behind 0.4.x's `get_similar_photos`, took the
  photo alone and drifted a step at a time — a red bicycle against a wall became walls.
  Exactly one reference is taken among `photo_id`, `image_url`, `image_file` and
  `image_base64`; two are refused before anything is fetched.
- **Access without an account, off by default.** With `PEXAFY_ANON_ENABLED` and
  `PEXAFY_ANON_SECRET`, a caller who sends no credential is served on the API's daily
  allowance for callers without an account. The server works out who they are —
  ChatGPT's `openai/subject`, marked as attested when the request comes from an address
  on OpenAI's published list, or else the caller's IP address — and tells the API in a
  signed, short-lived `X-Pexafy-Principal` header next to its service key
  (`PEXAFY_API_KEY`); the API verifies it before counting. An address that stands for a
  crowd names nobody: Claude.ai's egress range gets the 401 that starts OAuth rather than
  one allowance shared by every Claude user. Such a request is let past FastMCP's
  authentication with a per-process synthetic bearer that no client can present. The
  identity sources are ordered and configurable (`PEXAFY_ANON_SOURCES`; `mcp-session` is
  opt-in).
- **Account linking.** Every tool declares both `noauth` and `oauth2` in
  `securitySchemes` — and in `_meta["securitySchemes"]`, OpenAI's mirror for clients that
  only read `_meta`: usable without an account, and able to take one. On by default;
  `PEXAFY_ACCOUNT_LINKING=0` removes it and `connect_account`. The challenge is attached
  when someone asks for it, not at every spent allowance (`PEXAFY_LINK_AT_WALL`): at the
  wall, ChatGPT's dialog told someone who had never connected that their connection had
  expired, and after "Not now" the model invented results. Its `error` code is a setting
  (`PEXAFY_LINK_ERROR`), because the host writes its own words from it; an empty one
  falls back to `insufficient_scope`, since OpenAI requires both `error` and
  `error_description`.
- **The allowance, before it is gone.** The API's `X-Plan`, `X-Quota-*` and
  `X-Daily-Quota-*` headers become `budget` on the answer (what is left, once 80% is
  spent, `PEXAFY_BUDGET_WARN_AT`: the numbers and one sentence) and, for the grid
  alone, `_meta["pexafy/account"]` (who is asking — the way to sign in, or a signed-in
  state — and the wording and button of the allowance panel). A spent daily or monthly
  allowance comes back as an empty result with a `notice`, which the grid draws in
  place of the photos (`PEXAFY_BUDGET_WALL_GRID=0` returns a tool error instead). The
  wording follows OpenAI's rules: it states the limit, says once that a free account
  lifts the daily one, and sells nothing; a signed-in caller who runs out is offered no
  plan, only an informational page when `PEXAFY_ACCOUNT_OPTIONS_URL` names one.
- **Server `instructions`.** 0.4.x sent none. They say which needs should reach this
  server, what it is not for, and which tool answers what — under 2,048 characters,
  where Claude Code cuts them. They name no other service and state no preference
  against one.
- **A prompt, `find_photos`** (argument `scene`), that a person picks from their
  client's menu.
- **Model-facing metadata:** `openai/toolInvocation/invoking` and `invoked` status texts
  on every tool, `openai/widgetDescription` on the grid, and `anthropic/alwaysLoad` on
  `search_photos`, so that Claude Code loads the entry point without searching for it.
- **Two HTTP routes:** `POST /selection`, where the grid writes the selection (open CORS,
  `text/plain` so that no preflight is needed, body capped), and `GET /widget`, the grid
  as a web page for a site that embeds it (`frame-ancestors` from
  `PEXAFY_WIDGET_FRAME_ANCESTORS`).
- **What a host sends, in the log** (`observe.py`): one line per message with the names
  of the `_meta` keys the host sent, and a digest — never the value — of
  `openai/subject`, the identifier OpenAI sends "for the purposes of rate limiting and
  identification". Nothing of `openai/session` or `openai/organization` beyond their
  names: this server has no use for a conversation or a workspace id.
- **`constraints.txt`.** The image is built against the dependency set production runs:
  a rebuild would otherwise have pulled fastmcp 4 and mcp 2. `fastmcp` is also capped
  below 4 in `pyproject.toml`; the first build after fastmcp 4 shipped died at import.
  `redis`, which the store needs (`PEXAFY_REDIS_URL`) and the 0.4.12 image lacks, is
  pinned there to the version the suite runs on.
- **A skill for ChatGPT plugins**, `skills/find-stock-photos/`.

### Changed
- **The search surface is one sentence and one shape.** `q` is `english_search_sentence`
  on the tool surface — the request still sends `q` — required, 1 to 250 characters. A
  longer or blank one is refused by the server with a message saying what to send:
  `maxLength` only binds clients that validate, and a 341-character sentence went
  through whole while past about 500 the API answered an unreadable 422. `orientation`
  is `explicit_orientation_filter`, a list of `landscape`, `portrait` and `square` that
  OR together; a bare string is read as a one-item list, and any other value is refused,
  since the API ignores an unknown shape and would return an unfiltered grid as a
  filtered one. Every other filter left the tools: `color_name`, `color_hex`,
  `color_tolerance`, `source`, `license_type`, `after_date`, `photographer`, `cursor`,
  and `text_alpha` on the by-image search. An assistant fills in whatever a tool offers:
  in September, unrequested filters rode on nearly every search — a brand
  palette read as the photo's dominant colour cut the catalogue to 0.25% and kept none of
  the sixteen unfiltered results, and `license_type=free`, true of 99.8% of the
  catalogue, changed nothing on nearly all of them. The REST API keeps every filter. The kept
  parameters are an allow-list, so a parameter the API gains later cannot reach a tool.
- **Words can go with an image.** `english_search_sentence` is optional on
  `search_photos_by_image`: with a `photo_id`, the words that photo was found under;
  with an image, only the change asked for ("like this but at night"). `text_alpha` is
  not sent, so the API's own balance applies.
- **One page of 16.** The page size is set by the server, and the paging block is gone
  from answers and schemas: the grid is a single page.
- **Leaner results.** Each photo loses the fields nobody read — `blur_hash`,
  `relevance_score`, `color_hex`, `color_name`, `source_photo_id`, `image_url`,
  `description`, `source_description`, `photographer_url`, `photographer_username`,
  `uploaded_on` — `urls` keeps `regular` only and `attribution` keeps `plain` only:
  sixteen photos with 25 fields were 36,784 characters of the model's context on every
  search. The output schema declares exactly what is sent, what the server adds included
  (`rank`, `preview_url`, `preview_url_large`, `budget`, `notice`): a
  host that validates `structuredContent` may drop anything undeclared, and the budget
  notice went missing that way. `search_photos_by_image` declares the same schema as
  `search_photos`.
- **Nothing in an answer that the request does not need** (OpenAI's "response
  minimization"). The API's `meta` (`request_id`, `took_ms`) and the `request_id` of its
  error object leave the answer and the declared schema; `account` and `budget` no longer
  name the plan; `get_grid_selected_photos` no longer returns `selection_revision`. The
  grid's link to the answer on pexafy.com (`cta`) moves from `structuredContent` to the
  result's `_meta`, as `pexafy/cta`, where the grid reads it; so do the `account` block
  and the grid's half of `budget` (`short`, `title`, `detail`, `cta_label`,
  `account_url`), as `pexafy/account` — `budget` keeps its numbers, `state`,
  `signed_in` and `message`.
- **`rank` is data, not a drawing.** Every result still carries its place in the
  answer — the handle a client without the grid uses for "the second one" — but the
  grid no longer paints it on the tiles. The only numbers on screen are the liked
  photos' #1..#n.
- **The grid's image links move to `_meta`** (`pexafy/previews`), the half of a result
  the model does not read; they were 28% of what was left of a search.
  `PEXAFY_PREVIEWS_CHANNEL` picks `both` (the default), `meta` or `content`.
- **Where the person sees the grid, the model reads the photographs without their
  links.** Handed every image link, the model showed the photographs again under the
  grid, one picture after another (Cursor, 2026-10-01). In a host that shows the grid —
  one that declares MCP Apps, or ChatGPT, claude.ai, Claude Desktop, VS Code, Cursor,
  Goose — the text of a search names each photograph by `photo_id`, description, credit
  and licence; `structuredContent`, which some of them hand the model too, loses `urls`
  and keeps its previews as a path without address; `get_grid_selected_photos` leaves out
  `image_url`; `get_photo_file_by_photo_id` returns a photograph with its link. Every
  other client reads the whole answer, links included, as in 0.4.12: Claude Code, a
  terminal, and Codex. Through OpenAI's apps platform Codex calls as ChatGPT does — its
  User-Agent reads `openai-mcp/1.0.0 (Codex)` — and taken for it, it was handed the
  photographs without a single link: `(Codex)` in the User-Agent, or a `clientInfo`
  naming Codex, now tells it apart. That is no small share of the requests that come
  through OpenAI, beside ChatGPT's. Codex shows
  the grid only if it declares MCP Apps itself, keeps what an OpenAI host keeps — the
  `get_similar_photos` alias, the 0.4.12 grid's window — and is the assistant a grid
  names there.
- **Thumbnails outlive the conversation.** The grid's 480-pixel thumbnails were signed
  for four hours, so a result scrolled back to the next day was blank. They now last 30
  days, rounded up to a whole day so a photo keeps one URL all day; the viewer uses the
  proxy's permanent 1280-pixel form.
- **The grid is rebuilt.** Unnumbered thumbnails — 6, then 6, then 4, in two or three
  columns set by the width — each with a ♥ and a mark for its shape. The ♥ likes a
  photo; liked photos stay under the grid, numbered #1..#n, and the model is told on
  every change. A tap opens a swipe deck in the grid's place — right likes, left skips,
  up asks for more like it, down closes — full screen on a phone only. ≈ runs
  `search_photos_by_image` with the photo's `photo_id` and the search's words from
  inside the frame where the host proxies tool calls, repaints the grid and adds a step
  to a trail across the head, six deep; elsewhere it asks the assistant. A shape filter
  in the head asks the same question again for the shapes chosen. An account button sits
  in the corner, a notice appears near the end of an allowance, and a wall takes the
  results' place once it is spent. The grid follows the host's theme, and in ChatGPT
  keeps its view state inside `privateContent` so it survives the frame being reloaded —
  what the answer's `_meta` said included (its link, who is asking, the allowance
  panel's wording), since a reloaded frame may not be handed it again; a result that
  arrives without `_meta` falls back on `window.openai.toolResponseMetadata`. It writes
  in the platform's system font. The button under it reads "Open in Pexafy": "Full
  results" presented a grid of sixteen as a cut-down answer. After a wall it checks for
  two minutes at most whether the allowance came back.
- **Titles and descriptions are rewritten** to say when to call each tool before
  anything else, and kept under Claude Code's 2,048 characters. Every model-readable
  text was re-read against OpenAI's app-submission rules and Anthropic's directory
  criteria: none compares this server to another service or tells the model to prefer
  it over one; every trigger is something the user asks for, and a piece of writing
  that usually carries photographs gets an offer, with the search waiting for the
  user's yes; results, refusals and the grid's context are stated as facts rather than
  orders, and a plan limit is reported by Pexafy instead of in the assistant's voice.
  `tests/test_served_texts_compliance.py` keeps the refused formulas out.
- **`tools/list` puts the text search first.** FastMCP listed the hand-written tools
  ahead of the generated one, so a router reading from the top met the by-image search
  first.
- **`openWorldHint` is false** on the file, selection and account tools, which only
  read Pexafy's own state. The two searches keep it.
- **`idempotentHint` is false on the two searches**: each call spends one search of the
  caller's allowance, so the same call made twice is not free of effect. The file,
  selection and account tools keep it.
- **`serverInfo.version` is this package's version**, no longer FastMCP's.
- **A spent allowance says what happens next**, still without an upgrade pitch. The
  monthly message says the allowance resets on the 1st and not to retry, naming the
  options page when one is configured. A spent daily allowance has its own message —
  when it resets, and that a free account lifts it — instead of the rate-limit one
  ("wait 40000s, then try once more").
- **The 401 links to the form that creates a key.** With no credential, the body said
  "Create a key at https://pexafy.com/dashboard/api-keys" — the list of keys, one click
  short for a caller who has none. It now says "Create an MCP Agent key at
  https://pexafy.com/dashboard/api-keys/create/": the form itself, and the client origin
  to pick there. The refused-credential message links the same form ("create an MCP
  Agent key", where it said "create or copy one"), and so does `keys_url`.

### Deprecated
Accepted for hosts still on the 0.4.x tool list, and to be removed once every directory
has scanned 1.0.0 (`compat.py`). Each translation is logged, and the rate of those lines
says when:
- `get_similar_photos` → `search_photos_by_image`, with the same `photo_id`. It stays
  in the tool list an OpenAI host reads, with the definition 0.4.12 published, byte for
  byte (`assets/get_similar_photos-0.4.12.json`): OpenAI removes from a published app, at
  its next scan, a tool that `tools/list` no longer names, while the by-image search
  keeps its 0.4.12 definition, without `photo_id`, until 1.0.0's is approved: in
  between, the app would have no way left to find photos like one already found. No
  other host sees it, and neither `/health` nor the server card counts it.
  `PEXAFY_SIMILAR_ALIAS=0` takes it off the list once the new tools are approved; calls
  under the old name are answered either way;
- the 0.4.12 grid, which ChatGPT may keep in its cache for up to an hour after the
  deploy, under the same URI, and draw 1.0.0's answers with: it reads `structuredContent`
  alone, where 1.0.0 gives the previews as a path without address and no `urls` wherever
  a grid is drawn, so it would show no thumbnail and no "Open original image". Until
  `PEXAFY_LEGACY_GRID_UNTIL` — an instant in UTC, epoch seconds or ISO 8601
  (`2026-10-05T03:00:00Z`) — an OpenAI host's answer keeps everything that grid reads:
  the previews as whole links, `urls` in every size, `photographer_url`,
  `photographer_username`, `color_name`, `color_hex`, `uploaded_on`, `description`
  (cleaned like the rest of the photograph) and `cta`. The text the model reads keeps no
  link, `tools/list` does not change, and no other host sees a difference. Unset, empty
  or `0`: no window; a value that is not an instant opens none and is logged as a
  warning, and the start-up log says where the window stands. On deployment it is set to
  the time of the deploy plus two hours;
- `q` → `english_search_sentence`, on both searches, and so is `query`, which no tool
  list published but assistants send in its place (`q` wins when both are sent);
- `orientation` → `explicit_orientation_filter`, a single string widened to a list;
- `color_name`, `color_hex`, `color_tolerance`, `source`, `license_type`, `after_date`,
  `photographer`, `cursor` → dropped, and the search still answers; `text_alpha` →
  dropped on the by-image search; `per_page` and `limit` → dropped too: a page size no
  tool list offered, which the server sets itself. On production, 0.4.12 failed calls
  on `query`, `per_page` and `limit`;
- the names briefly served by a preview server —
  `search_photos_from_unsplash_pexels_pixabay_by_text` and `…_by_image`,
  `get_photo_file`, `get_selected_photos` → the current names.

A 0.4.x text search sent without `q` gets a message saying the sentence is required. The
two searches keep their 0.4.x names on purpose: OpenAI removes a tool that disappears
from a published app at once and offers a new name only after its checks pass, so a
rename would leave installed users without the tools in between.

### Removed
- **`get_similar_photos`** from the tool list, but an OpenAI host's (see **Deprecated**).
- **The numbered tiles and the detail panel** of the 0.4.x grid, replaced by the deck
  and the selection.
- **`assets/facets.json` and `PEXAFY_FACET_BOOTSTRAP_KEY`.** With the filters gone there
  is no value set to fetch; `prepare.sh` regenerates the OpenAPI snapshot and the
  ext-apps bundle only.
- **The Inter typeface** (`assets/inter-*.woff2`, its licence and notice): inlined as
  base64, it was 178 KB of every widget served, and OpenAI's UI guidelines say "Don't use
  custom fonts, even in full screen modes." The grid uses the system font stack.

### Fixed
- **A failed text search answers in a sentence** — "Pexafy could not run that search.",
  followed by the API's own message — like the by-image search, instead of FastMCP's raw
  "HTTP error 503: … {body}".
- **A rank sent as a `photo_id`** (`6`, `"6"` or `"#6"`, on the by-image and file tools)
  is refused with the correction: send the `photo_id` that sits beside it. The API
  answered a malformed id with "500 — Search service temporarily unavailable", which an
  assistant reported as an outage. Any other value that is not a UUID is told what
  `photo_id` takes.
- **The grid's domain is the one each host checks.** `_meta.ui.domain` carried the
  website's URL; Claude expects the SHA-256 of this server's `/mcp` endpoint under
  `claudemcpcontent.com`, and refused to render the grid ("Invalid ui.domain format").
  It is now derived that way for a Claude host: a `clientInfo` naming Claude or
  Anthropic — the session's, or the one a request carries in its `_meta` (protocol
  2026-07-28 opens no session) — or a call from Anthropic's outbound network,
  160.79.104.0/21, for a Claude surface whose name says neither. Every other host is
  served `PEXAFY_WIDGET_DOMAIN`, the value `openai/widgetDomain` keeps for every host: an
  OpenAI host, which reads the same field as the app's own origin, and a client that
  cannot be told — OpenAI's review scans with a client whose name is not documented. The
  log line of every `initialize` names the client and its version, which is how a
  directory's scan is told apart.
- **Without the grid, no text names it.** With `PEXAFY_MCP_PREVIEWS=0` or no thumbnail
  proxy, the instructions, both searches, their parameters and `connect_account` no
  longer speak of a grid the person does not see (a photo "liked in the Pexafy grid",
  results that "also appear to the person as a grid", the grid's account button), and
  the output schema no longer declares `preview_url` and `preview_url_large`, which no
  answer carries then. With the grid, every text is unchanged.
- **Likes the grid cannot post still reach the model.** A host may keep the grid from
  reaching this server: in ChatGPT, a developer connector keeps the content security
  policy it was created with, and a published app keeps the reviewed one until its
  updated definition passes review (measured 2026-10-03: the browser refused every
  `POST /selection`, the selection tool read 0 for two photos liked, and the assistant
  said the selection had expired). The grid swallowed the failure. Now a failed post — a
  refusal by the page's policy or the network, or an error status — is logged in the
  frame, and the grid's model context carries the selection itself, numbered in the
  reader's order (rearranged by dragging, renumbered on an unlike), as with the selection
  tool switched off, without naming the tool and without image links. The tool's empty
  answer says that a list in the grid's context is the selection; its "nothing liked in
  the grid" no longer states as fact what only the store knows.
- **The grid's CSP declares what it uses.** The thumbnail CDN is a resource domain only:
  the grid draws thumbnails and no longer reads their bytes, so `connectDomains` names
  this server alone (the selection it posts to `/selection`). The `/widget` page's
  header follows the same split: `img-src`/`media-src` for the CDN, `connect-src` for
  this server.
- **An OpenAPI snapshot whose search endpoint moved stops the server at load time,**
  naming the operation, instead of shipping the raw REST surface; and a parameter made
  required can no longer stay nullable.
- **An image served as `application/octet-stream`** (or `binary/octet-stream`, or with
  no type at all — S3, Drive, many CDNs) is read by its first bytes like any other; it
  was refused as "not an image" before they were looked at.

### Security
- **No search sentence, image, token, e-mail or address in clear in the log.** A refused
  argument is logged by its kind ("a rank", "a URL", "a word", its length), never
  quoted; FastMCP's warning about a call that fails validation loses the `input`
  pydantic quotes (a whole base64 image, the sentence); uvicorn's access line no longer
  starts with the caller's address, nor ends with a query string; the OAuth lines name
  the user by a keyed digest (HMAC-SHA256 under a secret the server holds, label
  `pexafy-log-email:v1:`), neither by e-mail nor by a plain hash, which the list of
  accounts would reverse. httpx no longer writes the URL of every outgoing request — the
  sentence of a text search sat in its query string, and a reference image's link, the
  signature of a ChatGPT upload included, was the image: it is held at WARNING with
  httpcore, and a line of this server's own (`API GET /api/v1/search/photos 200`) keeps
  the count. The MCP SDK, FastMCP and sse_starlette never go below INFO, whatever
  `PEXAFY_MCP_LOG_LEVEL` says (a quieter level quiets them too): their DEBUG lines are
  whole messages.
- **The host's request no longer reaches the API.** FastMCP's generated text search
  copied the incoming request's headers onto the request it sends to the API: the
  caller's address as Cloudflare and the proxies reported it (`cf-connecting-ip`,
  `x-forwarded-for`, `x-real-ip`), its country, ChatGPT's `x-openai-subject` and
  `x-openai-session`, cookies, `origin`, `referer`, trace ids — and any header a client
  added, the API's internal headers and a made-up `X-Pexafy-Principal` included. The
  API writes the address of every call to
  its call log, kept 90 days. Every request to the API is now rebuilt on an allow-list:
  HTTP's own headers and the client's defaults (its user agent, `x-api-key`, `X-Source`),
  then the credential and the signed principal this server sets. The API also counts
  searches per address, 60 a minute, and with no address every MCP caller would have
  shared one count: the caller's address goes as a pseudonym instead, in
  `X-Forwarded-For` — an HMAC under a key the process draws at start and keeps nowhere,
  written as an address of `fd70:6578:6166::/48`. Each caller keeps a count of its own,
  on the by-image search too, which shared the server's until now, and the API's log
  holds a name nobody can turn back into the address.
- **`image_url` can no longer reach the server's own network (SSRF).** 0.4.x downloaded
  any http(s) URL — loopback, private ranges, link-local, cloud metadata — followed
  redirects, read the whole body before checking its size, and echoed the status or
  content type in its error; 1.0.0 makes the tool callable without an account. Now every
  address the host resolves to must be public (IPv4-mapped IPv6 included), the
  connection goes to the address that was checked (so a second DNS answer cannot move
  it), redirects are followed by hand, at most three, each one checked, the body is read
  as a stream and cut at 10 MB — the API's own limit, which the refusal names, less
  16 KB: the API caps the whole multipart request, and the framing around the image
  (its file name cut to 100 characters) must fit too — one deadline covers the whole
  download, and the errors say nothing about what was reached.
  A format the API does not take (anything but JPEG, PNG, WebP and AVIF, a GIF included)
  is refused from its first bytes, from a URL or from base64, before anything is sent. `PEXAFY_IMG_ALLOW_PRIVATE=1` lifts the address
  check for a local test.
- **A key sent in `x-api-key` is the caller's own.** With OAuth on, FastMCP reads only
  `Authorization`, so a key sent the other documented way was refused with a 401 — and,
  once anonymous access exists, would be served with the service key and counted against
  the anonymous allowance. A `pexafy_…` key in `x-api-key` is now resolved exactly like
  the same key sent as a bearer, before anonymous access is considered.
- **OpenAI's address list cannot leave ChatGPT callers pooled.** It is fetched by the
  first call that needs it — not once the host's uptime has passed the refresh interval,
  which would have left every ChatGPT caller sharing OpenAI's address as one identity
  for up to an hour after a reboot. A failed fetch keeps the last good list and waits 60
  seconds before the next, and only one fetch runs at a time.
- **ChatGPT's widget state holds only its three documented keys** — `modelContent`
  (which the model reads), `privateContent` and `imageIds`. The grid's view, and with it
  the token that allows writing the selection, lives inside `privateContent`: a fourth
  top-level key has undocumented handling and could reach the model.
- **Signed, expiring credentials on the new paths.** The selection token names its
  caller by digest and expires; the anonymous principal is HMAC-signed with a
  five-minute lifetime, and the API verifies it rather than trusting it.
- **The anonymous caller's name is a salted hash.** The principal's `s` was
  `sha256("<source>:<subject>")`: for an IPv4 address, 2^32 guesses gave the address back.
  It is now an HMAC-SHA256 keyed by `PEXAFY_ANON_SECRET` (label
  `pexafy-anon-subject:v1:`), and the label in the log and the selection store another one
  (`pexafy-anon-log:v1:`). The API reads `s` as opaque and needs no change; every
  anonymous allowance starts again from zero once, on deployment.
- **Text a third party wrote is cleaned before the model reads it.** A photograph's
  description (`alt_description`), its photographer's name and the credit line built
  from it are cut to 300 characters and lose their control and invisible characters — a
  line break becomes a space; zero-width, bidirectional-override and tag characters go,
  so do variation selectors (a VS15 or VS16 stays after an emoji), the Hangul fillers and
  the rest of what Unicode lists as drawing nothing, and a run of joiners is cut to one,
  kept only between two characters — in search results, in the file tool's line of text,
  and in the selection the grid posts, which `get_grid_selected_photos` reads back.

## [0.4.12] — 2026-08-28

### Fixed
- **The metrics token is no longer resolved as an OAuth token.** Prometheus
  scrapes `/metrics` every 15 s with the monitoring bearer; the auth middleware
  handed that bearer to Django's `/oauth/mcp/resolve` like any other, Django
  answered 401, and the server logged "Token resolution rejected" — hundreds of times
  a day since 26 August, the exact line a real rejection produces. Nothing was
  broken for clients (`/metrics` checks the token itself and answered 200), but
  a genuine failure would have been invisible in that log, and Django took a
  useless authenticated request four times a minute. The verifier now knows the
  metrics token is not an OAuth token and returns without calling anyone.

## [0.4.11] — 2026-08-21

### Fixed
- **The 401 now names both ways in.** The MCP spec's 401 is a header, and a
  client that speaks OAuth follows it. Not every client does, and one that never
  enters the OAuth chain gets no help from either answer: an empty body when it
  sends nothing — the most common response this server gives — and OAuth advice
  when it sends a credential the SDK refuses ("clear the stored tokens and
  reconnect"), which is a dead end for a client with no tokens to clear. This
  server also accepts a plain Pexafy API key as the bearer, and nothing said so.

  `unauthorized.py` fills both, with different words, because "you sent nothing"
  and "what you sent was refused" are different problems. The status, the
  `WWW-Authenticate` header and the SDK's machine-readable `error` code are all
  preserved, so no client behaviour changes — only what a human reads. A 401
  that is neither empty nor one of the SDK's own JSON refusals passes through
  byte for byte, `/metrics` included.

## [0.4.10] — 2026-08-21

### Fixed
- **The 2026-07-28 discovery probe no longer leaks a session.** Clients speaking
  the new protocol revision — Claude Code, Claude-User, ChatGPT's `openai-mcp`,
  a third-party Go client — open with a sessionless `server/discover` POST. The
  SDK this server runs on tops out at 2025-11-25, so it does not know the
  method; but it decides to build a whole transport *before* it reads the body,
  on the sole fact that no `Mcp-Session-Id` was sent, and only then answers
  `400 Bad Request: Missing session ID`. The client falls back to the legacy
  handshake and everything works — except the transport it created is now
  unreachable, and nothing collects it. Production over 21 hours: 120 transports
  created, 4 closed, 49 of them from this path, ~42 KB of RSS each.

  `sessions.py` now answers the probe before the SDK can allocate anything, with
  the same status and the same JSON-RPC body, byte for byte, so no client sees a
  change. The guard is narrow on purpose: POST, `Mcp-Method: server/discover`,
  **no** `Mcp-Session-Id`, and the request body is never read. Claude-User sends
  the same method *inside* an established session — that request keeps its
  session header and is left entirely to the SDK, which is what the tool call
  after it depends on. The dropped `Mcp-Session-Id` response header named the
  transport the SDK had just orphaned, and no client ever sends such an id back.

### Added
- **`/metrics` publishes the live session count.** The SDK only drops a session
  when the client says goodbye, and almost none of them do, so the number held
  in memory only grows between restarts. Nothing is expired yet — the longest
  silence a real client took before coming back was 3 h 36, so any timeout short
  enough to be useful would also cut live users. The gauge comes first; the
  decision follows the curve. Prometheus scrapes it over the Docker network and
  Caddy 404s the path from the outside.

## [0.4.9] — 2026-08-20

### Added
- **The inline grid now declares a widget domain.** OpenAI's plugin portal
  refused the submission — "a unique domain is required for app validation" —
  because the grid resource named no origin of its own and fell back to the
  sandbox everyone shares. Nothing is served from the value: ChatGPT renders the
  widget under `<widgetDomain>.web-sandbox.oaiusercontent.com`, so it is an
  isolation label, and the target of "Open in Pexafy" from the fullscreen view.
  Hence `https://pexafy.com`, the website, rather than the MCP host, which would
  land a visitor on an endpoint instead of a page. Overridable with
  `PEXAFY_WIDGET_DOMAIN` so a deployment that is not pexafy.com does not claim to
  be.
- **The widget CSP is now also written under the name ChatGPT reads.** FastMCP
  writes the spec form (`_meta.ui.csp`); ChatGPT reads `openai/widgetCSP`. Both
  are built from the same two lists, so they cannot drift, and five tests hold
  them together. This one is invisible until it bites: the thumbnails load in
  preview and are blocked by CSP once the app is published.

## [0.4.8] — 2026-08-20

### Changed
- **The registry description now says the two things an agent picks a tool on.**
  It read "Stock photo search: by description, by example image, or more like this"
  — accurate, and silent on both the licence and the breadth, which are the whole
  point: the images are free to use, and one query crosses nine libraries instead
  of one provider's catalogue. That sentence is what the official registry serves
  and what every directory ingesting it repeats, so it is the one line worth
  spending words on. 92 of the 100 characters the schema allows.

## [0.4.7] — 2026-08-20

### Fixed
- **Every parameter of `search_photos_by_image` now says what it is.** The two
  generated tools inherit their parameter descriptions from the spec; the
  hand-written by-image tool inherited what a Python signature carries — nothing —
  and shipped twelve bare parameters, leaving a model to guess `text_alpha` from
  its name while the identical parameter is documented on `search_photos`. The
  filters now take their wording from the operation this tool posts to (excluded
  from tool generation, but still the authority on those parameters), so the two
  cannot drift; only the image inputs are written by hand. Two tests: one fails on
  any undescribed parameter of any tool, the other on any divergence from the spec.
  It is also what directory quality scores measure under "parameter descriptions".

## [0.4.6] — 2026-08-20

### Removed
- **`/.well-known/glama.json` — it fixed nothing, so it goes.** 0.4.5 served it
  because Glama's crawler asks for it and the connector listing complained it was
  missing. Serving it turned that complaint into a different one: their connector
  validator rejects the shape their own published schema defines
  (`maintainers[0]`: *"expected object, received string"*), and no MCP server in
  the wild publishes any other shape — every hosted server checked answers 404
  there, which is what the listing was reporting in the first place. The
  maintainer claim was never read from that URL anyway: it comes from the
  repository file, and the server page still shows the verified-maintainer badge.
  Back to 404. The server card stays — that one demonstrably works: it takes a
  directory scan from a timeout to a successful read.

## [0.4.5] — 2026-08-20

### Added
- **Pre-connect server card at `/.well-known/mcp/server-card.json`.** A directory
  that cannot authenticate has nothing to show: a listing gets created and left
  empty, its scan recorded as a timeout, with no tools and no description — while
  the scanner asks for this exact path before giving up. Directory documentation
  names the card as the escape hatch for that case ("automatic scanning can't
  complete (auth wall, ...)"), and it is the shape the pre-connect discovery draft
  (SEP-2127) and a growing number of scanners probe.
  The endpoint keeps its 401 — that is what makes clients discover OAuth — while
  the card, generated from the live server, states the identity, the auth wall and
  the three tools with their real schemas. Six tests, including one that fails if
  the card ever claims a tool the server does not serve.

### Fixed
- **The maintainer claim is now served where Glama actually reads it.** Its
  connector listing said `glama.json not found (HTTP 404)` while the file sat on
  the default branch of the public repository — because it does not read the
  repository for a hosted connector: it fetches `/.well-known/glama.json` on the
  host serving the MCP server, unauthenticated, every half hour (231 such 404s in
  one prod access log). The same claim is now answered there. It is held in code,
  not read from the repository file, because the image ships `src/` only; a test
  pins the two copies together so they cannot drift.

## [0.4.4] — 2026-08-20

### Added
- **`search_photos_by_image` now describes its results.** The two tools generated
  from the OpenAPI spec inherit an output schema; the hand-written by-image tool
  declared none, and ChatGPT surfaces that gap to the user as a badge on the tool
  itself — not only in the submission form. It now borrows `search_photos`' schema
  rather than restating it: both post to the same endpoint and return the same
  envelope, so a copy would be a second source of truth free to drift from the
  spec every other tool follows. Two tests hold it, one of which fails the moment
  any tool ships without an output schema.

## [0.4.3] — 2026-08-20

### Fixed
- **The version was written in three files and one was left behind.** 0.4.2
  shipped with `src/pexafy_mcp/__init__.py` still reading 0.4.1, which is the
  string the deploy script prints as "version on disk" — so the one place a human
  looks to confirm what production is running was the one place that was wrong.
  Three tests now hold `pyproject.toml`, `__init__.py` and `server.json` together,
  and require the version to have a changelog entry.

## [0.4.2] — 2026-08-20

### Fixed
- **OpenAI's app-directory scan rejected the server** on the schema of
  `search_photos_by_image`'s `image_file` parameter: *"file parameter 'image_file'
  must use the documented file schema"*. The parameter is typed `dict | None`, from
  which Pydantic infers `{"anyOf": [{"type": "object"}, {"type": "null"}]}` —
  declaring no properties at all, where the Apps SDK fixes the shape a host fills
  in: all four of `download_url`, `file_id`, `mime_type` and `file_name` declared,
  the first two required, the other two not, and no additional field. The schema
  is now written out and stamped onto the tool, and the parameter stays optional by
  being absent from `required` rather than by being nullable, since the `anyOf`
  wrapper is exactly what hid the properties. Three tests hold the contract.

## [0.4.1] — 2026-08-19

### Fixed
- **The key-limit message asked for a reconnection that is never needed.** It
  ended with "then reconnect", which is not true: the server resolves the OAuth
  token against Django on every request, with no cache, so the moment a key slot
  frees up the next question simply works. It also called the keys "connectors",
  a word that appears nowhere on the page it links to — users arrived looking for
  a list of connectors and found a list of API keys.

## [0.4.0] — 2026-08-18

### Changed
- **Plan-limit messages no longer sell.** They used to name the next tier, its
  price and link to the pricing page. OpenAI's plugin policy forbids that —
  "Plugins must not display subscription plans, initiate new subscriptions, or
  promote upgrades", with freemium upsells named explicitly — while allowing what
  actually helps: "the plugin may explain that [a feature requires a different
  plan]". So each message now states the limit, the number and the plan it belongs
  to, and stops. The rate-limit message keeps its wait instruction, without which
  an assistant retries in a loop and burns the same budget.

### Removed
- The plan-ladder machinery behind that copy: the cached fetch of
  `/api/v1/billing/plans`, the next-tier lookup, the price and label helpers.
  Nothing reads them now, and the error path no longer makes an HTTP call of its
  own. The module went from 144 lines to 83.

## [0.3.5] — 2026-08-17

### Added
- `/.well-known/openai-apps-challenge`, the domain-ownership check for OpenAI's app
  directory. Their verification fetches it on the host serving this server and
  expects the bare token — no JSON, no wrapper. Served only when
  `OPENAI_APPS_CHALLENGE` is set; the path 404s otherwise, rather than answering
  with an empty body that an ownership check could read as a pass.

## [0.3.4] — 2026-08-17

### Changed
- `photo_similar` is now **`get_similar_photos`**. A tool name should read as the
  action it performs; both connector directories say so, and OpenAI's own example
  is `get_order_status`. The name is overridden where the tools are generated
  rather than in the API's operationId, so regenerating the vendored spec cannot
  silently undo it.

## [0.3.3] — 2026-08-17

### Changed
- Tool descriptions now describe the result instead of instructing the assistant.
  The appended block used to say: always prefix results with their rank,
  proactively offer more like one of them, never blame the browser when the
  images do not show. Anthropic's connector review rejects exactly that —
  "Describe what the tool does. Do not tell Claude how to behave" — and names
  telling an assistant to call a tool the user did not ask for as a
  prompt-injection pattern. The facts stay: what `rank`, `photo_id`,
  `attribution` and `urls` contain, and that some clients render the inline grid
  only inside an expandable panel. An assistant that knows the rank is the handle
  a person uses, and that the matching `photo_id` sits beside it, connects the two
  without being ordered to.

## [0.3.2] — 2026-08-17

### Added
- Every tool now carries MCP annotations: `readOnlyHint`, `destructiveHint`,
  `idempotentHint`, `openWorldHint` and a display title. A host uses these to
  decide whether a call needs the user's confirmation, and Anthropic's connector
  directory rejects a submission whose tools declare none. All three tools search
  and return; not one writes, and the annotations now say so.

## [0.3.1] — 2026-08-17

### Fixed
- The protected-resource metadata (RFC 9728) is now served at the well-known
  **root** as well as under the resource path. Clients that do not implement the
  path insertion probed `/.well-known/oauth-protected-resource`, got a 404, and
  concluded the server had no authentication — a connector directory listed it as
  "No Auth" and failed its connection test on that basis, against a server that
  had answered 401 with a `WWW-Authenticate` header naming the real document.
  One handler, two mounts, so the two cannot drift apart.

## [0.3.0] — 2026-08-17

Packaging and distribution. The server's behaviour is unchanged; the container's
default is not.

### Changed
- **The image now defaults to the `stdio` transport, not `http`.** This is how an
  MCP client drives a containerised server, and `docker run -i --rm pexafy-mcp`
  previously hung — listening on a port nobody was talking to. Both compose files
  set the transport explicitly, so serving over HTTP is unaffected; anyone who
  relied on the old default must now pass `PEXAFY_MCP_TRANSPORT=http`.
- The README leads with the hosted server rather than with self-hosting, and
  documents the three tools with their full parameter signatures.
- `docker-compose.pexafy.yml`, which described Pexafy's own deployment, is
  replaced by a neutral `docker-compose.example.yml`.
- CI moved from GitLab to GitHub Actions, with ruff pinned so a new release of
  the linter cannot fail an untouched tree.

### Added
- `THIRD_PARTY_NOTICES.md` and `licenses/`: the package redistributes the Inter
  typeface (SIL OFL 1.1) and the `@modelcontextprotocol/ext-apps` bundle
  (Apache-2.0, with zod and `@standard-schema/spec` inside it). None of them
  shipped with its notice.
- `server.json`, the manifest the official MCP registry consumes. Published as
  `com.pexafy/pexafy-mcp`, namespace validated by DNS.
- `glama.json`, declaring the maintainer so the Glama listing can be claimed.
- Screenshots of the inline result grid, the rank follow-up and the detail panel.

## [0.2.0] — 2026-06-21

First public release.

### Added
- MCP server exposing Pexafy image search to any MCP client, with three tools:
  `search_photos` (semantic text), `search_photos_by_image` (visual search from an
  image URL), and `photo_similar` ("more like this").
- Inline thumbnail result grid as an MCP Apps UI resource (self-contained widget,
  vendored ext-apps SDK, signed thumbnail URLs).
- `stdio` and remote Streamable `http` transports.
- Per-user auth over HTTP: OAuth Resource Server or a forwarded Pexafy API key.
- In-chat, metric-driven plan-limit messages (rate limit vs monthly quota).
- `build_server()` factory with no import-time side effects or network — tools are
  generated from a vendored OpenAPI snapshot.
- Offline `pytest` test suite (server build, tool tuning, preview signing, limit
  copy) wired into CI alongside `ruff`.

### Changed
- The Compose file is now `docker-compose.pexafy.yml` and documented as Pexafy's own
  production deployment (joins the internal network), not a generic `docker compose up`.
