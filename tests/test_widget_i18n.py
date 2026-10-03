"""Every text the grid shows, in every language of the site (widget_i18n.py).

The grid looks its texts up by their English wording (`tx`, `tn`), so a text added to
the script without a translation shows in English. This file fails instead: every text
the script passes is in the table, every language has every text, each translation
keeps the placeholders the grid fills in, and a text with a count has valid plural
forms. The lookup itself runs in node, in four languages that do plurals differently.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from pexafy_mcp import budget, origin, widget, widget_i18n

JS = widget._with_tool_names(widget._WIDGET_JS)
I18N_JS = widget._with_tool_names(widget._I18N_JS)
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")

CATEGORIES = {"zero", "one", "two", "few", "many", "other"}
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _calls(name: str) -> list[str]:
    """The source of every argument list passed to `name(` in the script."""
    out, start = [], 0
    pattern = re.compile(r"(?<![\w.])" + name + r"\(")
    while True:
        m = pattern.search(JS, start)
        if not m:
            return out
        i = m.end()
        depth, quote, j = 0, None, i
        while j < len(JS):
            c = JS[j]
            if quote:
                if c == "\\":
                    j += 2
                    continue
                if c == quote:
                    quote = None
            elif c in "\"'":
                quote = c
            elif c in "([{":
                depth += 1
            elif c in ")]}":
                if depth == 0:
                    break
                depth -= 1
            j += 1
        out.append(JS[i:j])
        start = j


def _literals(src: str) -> list[str]:
    """The string literals in a piece of the script, in order, read left to right as a
    tokenizer would; an object literal's keys (`{ k: …`, `, k: …`) are left out, a
    ternary's branches (`? "a" : "b"`) are not."""
    found, i = [], 0
    while i < len(src):
        c = src[i]
        if src.startswith("//", i):
            i = src.find("\n", i)
            i = len(src) if i == -1 else i
            continue
        if src.startswith("/*", i):
            i = src.find("*/", i + 2)
            i = len(src) if i == -1 else i + 2
            continue
        if c in "\"'":
            j, chars = i + 1, []
            while j < len(src) and src[j] != c:
                if src[j] == "\\" and j + 1 < len(src):
                    chars.append(src[j + 1])
                    j += 2
                    continue
                chars.append(src[j])
                j += 1
            before = src[:i].rstrip()[-1:]
            after = src[j + 1:].lstrip()[:1]
            if not (after == ":" and before in ("{", ",")):
                found.append("".join(chars))
            i = j + 1
            continue
        i += 1
    return found


def _used_texts() -> set[str]:
    """Every English text the grid can show: what it passes to `tx` and `tn`, and the
    texts it passes through them by name — the coach's lines, the shapes, the two
    messages it posts in the chat, and the labels the server sends (budget, origin)."""
    used: set[str] = set()
    for args in _calls("tx"):
        used.update(text for text in _literals(args) if text)
    for args in _calls("tn"):
        lits = _literals(args)
        # (n, one, other[, vars]): the plural is the key.
        if len(lits) >= 2:
            used.add(lits[1])
    coach = JS[JS.index("const COACH_SAY = {"):JS.index("function coachHtml(src)")]
    used.update(t for t in _literals(coach) if " " in t)
    shapes = JS[JS.index("const SHAPE_LABEL = {"):]
    used.update(_literals(shapes[:shapes.index("};")]))
    for name in ("CONNECT_ASK", "TRY_AGAIN_ASK"):
        line = JS[JS.index(f"const {name} = "):]
        used.add(_literals(line[:line.index(";")])[0])
    used.update({budget.SIGN_IN_LABEL, budget.OPTIONS_LABEL, "Signed in", "Sign in to Pexafy",
                 "Open in Pexafy", "Image search at Pexafy"})
    return used


def test_the_server_labels_named_here_are_the_ones_it_sends():
    """The account labels and the link's labels come from the server, in English: the
    grid translates them by their wording, so they are named above as sent."""
    src = open(budget.__file__, encoding="utf-8").read()
    assert '"label": "Signed in"' in src and '"label": "Sign in to Pexafy"' in src
    src = open(origin.__file__, encoding="utf-8").read()
    assert '"Open in Pexafy"' in src and '"Image search at Pexafy"' in src


def test_every_text_the_grid_shows_has_a_key():
    used = _used_texts()
    keys = set(widget_i18n.KEYS)
    assert used - keys == set(), sorted(used - keys)
    # …and the table carries nothing the grid no longer says.
    assert keys - used == set(), sorted(keys - used)


def test_the_plural_keys_are_the_english_plurals_the_script_passes():
    pairs = []
    for args in _calls("tn"):
        lits = _literals(args)
        if len(lits) >= 2:
            pairs.append((lits[0], lits[1]))
    for one, other in pairs:
        assert widget_i18n.ENGLISH_ONE.get(other) == one, (one, other)


def test_every_language_of_the_site_has_every_text():
    assert set(widget_i18n.TRANSLATIONS) == set(widget_i18n.LANGUAGES) - {"en"}
    for lang, table in widget_i18n.TRANSLATIONS.items():
        assert list(table) == list(widget_i18n.KEYS), lang
        for key, value in table.items():
            assert value, (lang, key)


@pytest.mark.parametrize("lang", sorted(widget_i18n.TRANSLATIONS))
def test_a_translation_keeps_what_the_grid_fills_in(lang):
    table = widget_i18n.TRANSLATIONS[lang]
    for key, value in table.items():
        want = set(PLACEHOLDER.findall(key))
        plural = key in widget_i18n.ENGLISH_ONE
        forms = value.items() if isinstance(value, dict) else [("other", value)]
        if isinstance(value, dict):
            assert plural, (lang, key, "only a text with a count has plural forms")
            assert set(value) <= CATEGORIES and "other" in value, (lang, key, sorted(value))
        for category, text in forms:
            assert isinstance(text, str) and text.strip(), (lang, key, category)
            got = set(PLACEHOLDER.findall(text))
            # A singular may say "one photo" without the number.
            if plural and category in ("zero", "one", "two") and want == {"n"}:
                assert got <= want, (lang, key, category, text)
            else:
                assert got == want, (lang, key, category, text)


def test_the_bold_count_has_its_place_in_every_language():
    """The selection's count is drawn in bold where `{n}` stands: a language without it
    in that line would lose the number."""
    for lang, table in widget_i18n.TRANSLATIONS.items():
        value = table["{n} images selected"]
        forms = value.values() if isinstance(value, dict) else [value]
        for text in forms:
            assert text.count("{n}") == 1, (lang, text)


def test_the_table_is_served_once_and_compact():
    """English once, then each language in the same order: a key repeated 22 times was
    half the weight of the table."""
    data = json.loads(widget_i18n.as_json())
    assert data["k"] == list(widget_i18n.KEYS)
    assert set(data["t"]) == set(widget_i18n.TRANSLATIONS)
    assert all(len(row) == len(data["k"]) for row in data["t"].values())
    assert len(widget_i18n.as_json().encode()) < 140_000


# ── The lookup, run ──────────────────────────────────────────────────────────

_RUN = r"""
const window = { openai: plan.openai || undefined };
const navigator = { language: plan.navigator || "" };
const document = { documentElement: {} };
""" + I18N_JS + r"""
const out = {};
out.lang = readLang() ? LANG : LANG;
out.tags = plan.tags.map((t) => langOf(t));
out.texts = plan.texts.map(([src, vars]) => tx(src, vars));
out.counts = plan.counts.map(([n, one, other]) => tn(n, one, other));
out.list = listOr(plan.list);
process.stdout.write(JSON.stringify(out));
"""


def _run(**plan):
    plan.setdefault("tags", [])
    plan.setdefault("texts", [])
    plan.setdefault("counts", [])
    plan.setdefault("list", ["a", "b"])
    return _node("const plan = " + json.dumps(plan) + ";\n" + _RUN)


def _node(script: str):
    """Run a script in node from its standard input: the table alone is most of what a
    single command-line argument may hold."""
    out = subprocess.run([NODE], input=script, capture_output=True, text=True, check=True).stdout
    return json.loads(out)


@needs_node
def test_a_host_s_locale_names_the_site_s_language():
    out = _run(tags=["fr-FR", "fr", "pt-BR", "pt-PT", "zh-CN", "zh-Hans-CN", "zh-TW",
                     "en-US", "de-AT", "fil-PH", "sw-KE", "xx-YY", ""])
    assert out["tags"] == ["fr", "fr", "pt-br", "pt-br", "zh-hans", "zh-hans", "zh-hans",
                           "en", "de", "fil", "sw", "", ""]


@needs_node
def test_chatgpt_s_locale_wins_over_the_frame_s_own():
    assert _run(openai={"locale": "es-ES"}, navigator="de-DE")["lang"] == "es"
    assert _run(navigator="ja-JP")["lang"] == "ja"
    assert _run(navigator="xx")["lang"] == "en"


@needs_node
def test_texts_and_counts_in_four_ways_of_counting():
    texts = [["Photos you like are shared with {assistant}", {"assistant": "Cursor"}],
             ["Report a bug", None], ["A text nobody translated", None]]
    counts = [[1, "{n} image selected", "{n} images selected"],
              [3, "{n} image selected", "{n} images selected"],
              [7, "{n} image selected", "{n} images selected"]]
    fr = _run(navigator="fr-FR", texts=texts, counts=counts, list=["Paysage", "Carré"])
    assert fr["texts"] == ["Les photos que vous aimez sont transmises à Cursor",
                           "Signaler un bug", "A text nobody translated"]
    assert fr["counts"] == ["1 image sélectionnée", "3 images sélectionnées",
                            "7 images sélectionnées"]
    assert fr["list"] == "Paysage ou Carré"
    ru = _run(navigator="ru-RU", counts=counts)
    assert ru["counts"] == ["Выбрано 1 изображение", "Выбрано 3 изображения",
                            "Выбрано 7 изображений"]
    ja = _run(navigator="ja-JP", counts=counts)
    assert ja["counts"] == ["1 枚を選択中", "3 枚を選択中", "7 枚を選択中"]
    en = _run(navigator="en-GB", counts=counts)
    assert en["counts"] == ["1 image selected", "3 images selected", "7 images selected"]
    ar = _run(navigator="ar", counts=[[5, "It resets in about {n} hour.",
                                       "It resets in about {n} hours."]])
    assert "ساعات" in ar["counts"][0]


# ── The allowance in the reader's language ───────────────────────────────────

_BUDGET = r"""
const window = {};
const navigator = { language: plan.lang };
const document = { documentElement: {} };
""" + I18N_JS + r"""
readLang();
""" + JS[JS.index("function budgetWords(b) {"):JS.index("/* Who is asking, in the reader's language")] + r"""
process.stdout.write(JSON.stringify(plan.blocks.map((b) => budgetWords(b))));
"""


def _budget(lang, *blocks):
    return _node("const plan = " + json.dumps({"lang": lang, "blocks": blocks}) + ";\n" + _BUDGET)


@needs_node
def test_the_allowance_is_said_from_its_numbers_and_english_is_the_server_s():
    warning = {"scope": "day", "limit": 100, "remaining": 2, "used": 98, "state": "warning",
               "signed_in": False, "short": "2 searches left today", "title": "2 searches left today",
               "detail": "A free Pexafy account lifts this daily limit.", "cta_label": "Sign in",
               "reset_hours": None, "free_account": True}
    wall = dict(warning, remaining=0, used=100, state="exhausted", wall=True, reset_hours=5,
                title="Today's free searches are used up")
    member = dict(wall, signed_in=True, scope="month", cta_label="")
    fr = _budget("fr", warning, wall, member)
    assert fr[0]["short"] == "Encore 2 recherches aujourd'hui"
    assert fr[0]["detail"] == "Un compte Pexafy gratuit lève cette limite quotidienne."
    assert fr[0]["cta_label"] == "Se connecter"
    assert fr[1]["title"] == "Les recherches gratuites du jour sont épuisées"
    assert fr[1]["detail"] == ("Le compteur repart à zéro dans environ 5 heures. "
                               "Un compte Pexafy gratuit lève cette limite quotidienne.")
    assert fr[2]["title"] == "Les recherches de ce mois-ci sont épuisées"
    assert fr[2]["detail"] == "Le compteur repart à zéro au début du mois prochain."
    # English: exactly what the server wrote.
    assert _budget("en", warning) == [warning]


@needs_node
def test_french_elides_de_before_a_name_that_starts_with_a_vowel():
    texts = [["Photo by {author} on {source}", {"author": "Amanda Frank", "source": "Unsplash"}],
             ["Photo by {author} on {source}", {"author": "Cody Boileau", "source": "Unsplash"}],
             ["Back to the photos like {author}'s", {"author": "Émile"}]]
    fr = _run(navigator="fr", texts=texts)
    assert fr["texts"] == ["Photo d'Amanda Frank sur Unsplash", "Photo de Cody Boileau sur Unsplash",
                           "Retour aux photos semblables à celle d'Émile"]
    # Nowhere else: Italian keeps "di", and English is untouched.
    assert _run(navigator="it", texts=texts[:1])["texts"] == ["Foto di Amanda Frank su Unsplash"]


def test_the_french_table_is_written_elided_already():
    """The rule only ever meets a name: no French text of the table has "de" before a
    vowel of its own."""
    vowel = re.compile(r"\bde [aeiouyàâäéèêëîïôöûùüAEIOUYÀÂÄÉÈÊËÎÏÔÖÛÙÜ]")
    for key, value in widget_i18n.TRANSLATIONS["fr"].items():
        forms = value.values() if isinstance(value, dict) else [value]
        for text in forms:
            assert not vowel.search(text), (key, text)


def test_right_to_left_text_in_the_deck_sets_its_own_direction():
    css = widget._STYLE
    rule = css[css.index("#status, #toast"):]
    rule = rule[:rule.index("}")]
    assert ".vinfo .by > span" in rule and ".vinfo .cap" in rule and "unicode-bidi: plaintext" in rule
    assert "#viewer.guiding .vinfo .cap { visibility: hidden; }" in css
