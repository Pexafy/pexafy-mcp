"""The grid's link to the full results, read from `_meta`.

It used to ride in `structuredContent`, the model's half of the answer, where it was one
more link handed to a reader with no use for it. The server now puts it on the result's
`_meta` (origin.CTA_META_KEY), and the frame reads it there: `learnLinks` takes it from
each answer, `buildPhotos` prefers it — and still reads `sc.cta`, where an answer from an
earlier server, kept in an older conversation, carries it.

The code is read statically, so this holds without node; where node is installed the
two functions run against the answers a host hands over.
"""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from pexafy_mcp import origin, widget

JS = widget._with_tool_names(widget._WIDGET_JS)
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _between(start: str, end: str) -> str:
    return JS[JS.index(start):JS.index(end)]


def test_the_link_is_read_off_meta_under_the_server_s_key():
    links = _between("function learnLinks(meta) {", "let ORIGIN = null;")
    assert f'meta["{origin.CTA_META_KEY}"]' in links
    assert "let ctaMeta = null;" in links
    photos = _between("function buildPhotos(sc, lead)", "function likedIn(n)")
    assert "const cta = ctaMeta || (sc && sc.cta) || null;" in photos
    # The site is still adopted from it, before any page URL is written.
    assert photos.index("adoptSite(cta.url)") < photos.index("fields(photo")


_HARNESS = r"""
const plan = JSON.parse(require("fs").readFileSync(0, "utf8"));
const GRID_MAX = 16;
const adopted = [];
function adoptSite(url) { adopted.push(url); }
function normShapes(v) { return Array.isArray(v) ? v : []; }
function fields(photo) { return { id: photo.photo_id, thumb: "t" }; }
eval(plan.links + "\n" + plan.photos);
const out = [];
for (const [meta, sc] of plan.answers) {
  learnLinks(meta);
  out.push(buildPhotos(sc, null).cta);
}
process.stdout.write(JSON.stringify({ ctas: out, adopted: adopted }));
"""


def _run(answers):
    plan = {
        "links": _between("const LINKS = Object.create(null);", "let ORIGIN = null;"),
        "photos": _between("function buildPhotos(sc, lead)", "function likedIn(n)"),
        "answers": answers,
    }
    r = subprocess.run([NODE, "-e", _HARNESS], input=json.dumps(plan),
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@needs_node
def test_each_answer_brings_its_own_link_and_an_old_answer_still_has_one():
    new = {"label": "Open in Pexafy", "url": "https://preprod.pexafy.com/?q=cats"}
    old = {"label": "Full results at Pexafy", "url": "https://pexafy.com/?q=dogs"}
    photos = {"data": [{"photo_id": "p1"}]}
    out = _run([
        [{origin.CTA_META_KEY: new}, photos],        # this server: the link is on `_meta`
        [{}, dict(photos, cta=old)],                 # an earlier server's answer
        [None, photos],                              # a host that passed no `_meta` at all
    ])
    assert out["ctas"] == [new, old, None]
    # Never the previous answer's link: the third answer had none, and says so.
    assert out["adopted"] == [new["url"], old["url"]]
