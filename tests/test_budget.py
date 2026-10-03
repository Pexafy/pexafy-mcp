"""What the caller is told about today's allowance, and what must never be said.

Two halves. The arithmetic — when a notice appears at all, and what it counts. And the
wording, which is where OpenAI's app guidelines bite: selling digital products or
services is forbidden "including subscriptions, digital content, tokens, or credits …
whether offered directly or indirectly (for example, through freemium upsells)", as is
displaying plans, initiating subscriptions, promoting upgrades, or linking to a
checkout. Explaining why something is unavailable is permitted, and so is linking to an
informational page about plans.

The tests below hold the line by naming what may not appear. A future edit that adds a
price, a plan name or the word "upgrade" fails here rather than at review.

    pytest tests/test_budget.py -v
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from pexafy_mcp import budget


def _headers(limit=None, remaining=None, plan="anonymous"):
    h = {}
    if limit is not None:
        h[budget.LIMIT_HEADER] = str(limit)
    if remaining is not None:
        h[budget.REMAINING_HEADER] = str(remaining)
    if plan:
        h[budget.PLAN_HEADER] = plan
    return h


class SilenceTests(unittest.TestCase):
    """Saying nothing is the default; a counter on every answer is noise."""

    def test_an_account_plan_says_nothing(self):
        """No daily allowance, no daily notice — which is every account plan."""
        self.assertIsNone(budget.read(_headers(plan="pro")))

    def test_a_zero_limit_says_nothing(self):
        self.assertIsNone(budget.read(_headers(limit=0, remaining=0)))

    def test_early_in_the_day_says_nothing(self):
        self.assertIsNone(budget.read(_headers(limit=100, remaining=80)))

    def test_one_short_of_the_threshold_still_says_nothing(self):
        """79 used of 100 is below 80% — the boundary belongs to the quiet side."""
        self.assertIsNone(budget.read(_headers(limit=100, remaining=21)))

    def test_the_feature_can_be_turned_off_entirely(self):
        with patch.object(budget, "ENABLED", False):
            self.assertIsNone(budget.read(_headers(limit=100, remaining=1)))

    def test_garbage_headers_say_nothing_rather_than_raise(self):
        for bad in ({"X-Daily-Quota-Limit": "lots"}, {"X-Daily-Quota-Remaining": ""},
                    {"X-Daily-Quota-Limit": "100"}):
            with self.subTest(headers=bad):
                self.assertIsNone(budget.read(bad))


class ThresholdTests(unittest.TestCase):
    def test_it_fires_at_exactly_the_threshold(self):
        got = budget.read(_headers(limit=100, remaining=20))
        self.assertIsNotNone(got)
        self.assertEqual(got["used"], 80)
        self.assertEqual(got["remaining"], 20)
        self.assertEqual(got["state"], "warning")

    def test_the_threshold_is_on_what_is_spent_so_it_scales(self):
        """80 of 100 and 8 of 10 are the same moment; a threshold on what is LEFT
        would fire at the start of a small allowance and never on a large one."""
        self.assertIsNotNone(budget.read(_headers(limit=10, remaining=2)))
        self.assertIsNone(budget.read(_headers(limit=10, remaining=3)))

    def test_the_threshold_is_configurable(self):
        with patch.object(budget, "WARN_AT", 0.5):
            self.assertIsNotNone(budget.read(_headers(limit=100, remaining=50)))
            self.assertIsNone(budget.read(_headers(limit=100, remaining=51)))

    def test_the_last_search_is_marked_exhausted(self):
        got = budget.read(_headers(limit=100, remaining=0))
        self.assertEqual(got["state"], "exhausted")
        self.assertEqual(got["remaining"], 0)

    def test_a_negative_remaining_never_reaches_the_screen(self):
        """The edge counts and the origin counts; a stale header must not render
        "-2 searches left"."""
        got = budget.read(_headers(limit=100, remaining=-2))
        self.assertEqual(got["remaining"], 0)
        self.assertNotIn("-", got["message"])


class WordingTests(unittest.TestCase):
    """What the sentence says — and the words that must never be in it."""

    FORBIDDEN = ("upgrade", "subscribe", "subscription", "€", "$", "/month", "per month",
                 "pricing", "plan starts", "starter", "pro plan", "buy", "checkout")

    def _messages(self):
        return [
            budget.message(100, 20),
            budget.message(100, 1),
            budget.message(100, 0),
            budget.message(10, 2),
        ]

    def test_no_message_sells_anything(self):
        for msg in self._messages():
            for word in self.FORBIDDEN:
                with self.subTest(msg=msg[:40], word=word):
                    self.assertNotIn(word, msg.lower())

    def test_it_counts_searches_not_percentages(self):
        """"3 searches left" is something an assistant can act on; "80% used" is a
        statistic about us."""
        self.assertIn("20 searches left", budget.message(100, 20))
        self.assertNotIn("%", budget.message(100, 20))

    def test_one_search_left_is_singular(self):
        self.assertIn("1 search left", budget.message(100, 1))
        self.assertNotIn("1 searches", budget.message(100, 1))

    def test_the_last_one_says_it_was_the_last(self):
        self.assertIn("last of today's 100", budget.message(100, 0))

    def test_the_account_line_states_a_fact_and_stops(self):
        msg = budget.message(100, 5)
        self.assertIn("A free Pexafy account lifts this daily limit.", msg)
        # One mention, not a pitch spread across the sentence.
        self.assertEqual(msg.count("account"), 2, "once for the situation, once for the way out")

    def test_the_account_line_can_be_switched_off(self):
        """Left as the bare counter, which is fact and defensible anywhere."""
        with patch.object(budget, "ACCOUNT_CTA", False):
            msg = budget.message(100, 5)
        self.assertIn("5 searches left today", msg)
        self.assertNotIn("free Pexafy account", msg)


class ShortFormTests(unittest.TestCase):
    """The corner of a card holds one line, not a sentence.

    Rendered as the full message it took three lines in a real browser and pushed the
    header out of shape — which is why there are two forms and why this one is capped.
    """

    def test_it_stays_short_enough_for_a_header(self):
        for remaining in (0, 1, 5, 19, 99):
            with self.subTest(remaining=remaining):
                self.assertLessEqual(len(budget.short(remaining)), 24)

    def test_it_counts_searches(self):
        self.assertEqual(budget.short(19), "19 searches left today")

    def test_one_is_singular(self):
        self.assertEqual(budget.short(1), "1 search left today")

    def test_empty_says_the_limit_is_reached(self):
        self.assertEqual(budget.short(0), "Daily limit reached")

    def test_the_block_carries_both_forms(self):
        got = budget.read(_headers(limit=100, remaining=19))
        self.assertEqual(got["short"], "19 searches left today")
        self.assertIn("A free Pexafy account", got["message"])

    def test_the_link_label_is_a_label_not_a_pitch(self):
        """"Sign in" is the door. The guidelines permit signing in to an account by
        name; the argument for having one is not made on a button."""
        got = budget.read(_headers(limit=100, remaining=19))
        self.assertEqual(got["cta_label"], "Sign in")

    def test_no_cta_means_no_label(self):
        with patch.object(budget, "ACCOUNT_CTA", False):
            self.assertEqual(budget.read(_headers(limit=100, remaining=19))["cta_label"], "")


class LinkTests(unittest.TestCase):
    """The link, when there is one, points at information — never at a checkout."""

    def test_no_url_configured_means_no_link(self):
        with patch.object(budget, "SIGN_IN_URL", ""):
            self.assertNotIn("account_url", budget.read(_headers(limit=100, remaining=5)))

    def test_a_configured_url_is_carried(self):
        with patch.object(budget, "SIGN_IN_URL", "https://pexafy.com/auth/login/"), \
             patch.object(budget, "ACCOUNT_CTA", True):
            got = budget.read(_headers(limit=100, remaining=5))
        self.assertEqual(got["account_url"], "https://pexafy.com/auth/login/")

    def test_the_cta_switch_also_removes_the_link(self):
        """Off must mean off on both surfaces, or the widget would still show one."""
        with patch.object(budget, "SIGN_IN_URL", "https://pexafy.com/auth/login/"), \
             patch.object(budget, "ACCOUNT_CTA", False):
            self.assertNotIn("account_url", budget.read(_headers(limit=100, remaining=5)))


class AccountTests(unittest.TestCase):
    """Who is asking — the block that rides on EVERY answer.

    It exists because the counter did not: a notice that only appears in the last
    fifth of an allowance leaves somebody who simply wants to sign in with nowhere to
    go until they have spent eighty searches.
    """

    def test_a_visitor_is_offered_the_way_in(self):
        with patch.object(budget, "SIGN_IN_URL", "https://pexafy.com/auth/login/"):
            got = budget.account({"X-Plan": "anonymous"})
        self.assertEqual(got["state"], "anonymous")
        self.assertEqual(got["sign_in_url"], "https://pexafy.com/auth/login/")
        self.assertEqual(got["sign_in_label"], "Sign in")

    def test_a_signed_in_caller_is_sent_home_not_to_a_sign_in_form(self):
        """A button with their name on it can honestly offer one thing: the site."""
        with patch.object(budget, "SIGN_IN_URL", "https://pexafy.com/auth/login/"), \
             patch.object(budget, "HOME_URL", "https://pexafy.com/"):
            got = budget.account({"X-Plan": "free"})
        self.assertEqual(got["state"], "signed_in")
        self.assertEqual(got["home_url"], "https://pexafy.com/")
        self.assertNotIn("sign_in_url", got)

    def test_the_way_in_is_the_form_not_a_page_about_the_product(self):
        """A button labelled "Sign in" that opens a landing page is a door onto a
        corridor. The default is the site's own sign-in path."""
        with patch.object(budget, "SIGN_IN_URL", budget.WEB_URL + "/auth/login/"):
            self.assertTrue(budget.account({"X-Plan": "anonymous"})["sign_in_url"]
                            .endswith("/auth/login/"))

    def test_no_plan_header_means_no_block(self):
        self.assertIsNone(budget.account({}))

    def test_the_cta_switch_removes_the_link_here_too(self):
        with patch.object(budget, "SIGN_IN_URL", "https://pexafy.com/auth/login/"), \
             patch.object(budget, "ACCOUNT_CTA", False):
            self.assertNotIn("sign_in_url", budget.account({"X-Plan": "anonymous"}))


class MonthlyTests(unittest.TestCase):
    """An account runs out by the month, and is told so in the month's words."""

    def _month(self, limit, remaining, plan="free"):
        return {"X-Quota-Limit": str(limit), "X-Quota-Remaining": str(remaining),
                "X-Plan": plan}

    def test_it_counts_the_month_for_an_account(self):
        got = budget.read(self._month(5000, 800))
        self.assertEqual(got["scope"], "month")
        self.assertTrue(got["signed_in"])
        self.assertIn("800 searches left this month", got["short"])

    def test_an_unmetered_plan_says_nothing(self):
        """-1 is the API's sentinel for "no limit", not a caller at zero."""
        self.assertIsNone(budget.read(self._month(5000, -1)))

    def test_the_daily_allowance_wins_when_both_are_present(self):
        h = self._month(5000, 4000, plan="anonymous")
        h.update({"X-Daily-Quota-Limit": "100", "X-Daily-Quota-Remaining": "9"})
        self.assertEqual(budget.read(h)["scope"], "day")

    def test_an_account_at_its_limit_is_never_sent_to_the_sign_in_form(self):
        with patch.object(budget, "OPTIONS_URL", ""):
            got = budget.read(self._month(5000, 0))
        self.assertEqual(got["state"], "exhausted")
        self.assertEqual(got["cta_label"], "")
        self.assertNotIn("account_url", got)

    def test_it_offers_the_reset_instead(self):
        self.assertIn("next month", budget.read(self._month(5000, 0))["detail"])


class WallTests(unittest.TestCase):
    """The screen a refusal becomes.

    A tool that raises renders no widget, so a spent allowance comes back as an empty
    answer carrying this block — see server._wall_as_grid.
    """

    def test_it_says_which_allowance_and_when_it_returns(self):
        got = budget.wall({"X-Daily-Quota-Limit": "100", "X-Daily-Quota-Remaining": "0",
                           "X-Plan": "anonymous", "Retry-After": "25200"})
        self.assertEqual(got["state"], "exhausted")
        self.assertIn("used up", got["title"])
        self.assertIn("about 7 hours", got["detail"])

    def test_without_a_retry_after_it_names_the_rule(self):
        got = budget.wall({"X-Daily-Quota-Limit": "100", "X-Daily-Quota-Remaining": "0",
                           "X-Plan": "anonymous"})
        self.assertIn("midnight UTC", got["detail"])

    def test_a_visitor_is_offered_the_way_in(self):
        with patch.object(budget, "SIGN_IN_URL", "https://pexafy.com/auth/login/"):
            got = budget.wall({"X-Daily-Quota-Limit": "100",
                               "X-Daily-Quota-Remaining": "0", "X-Plan": "anonymous"})
        self.assertEqual(got["account_url"], "https://pexafy.com/auth/login/")
        self.assertEqual(got["cta_label"], "Sign in")

    def test_an_account_at_its_limit_is_never_sent_to_the_sign_in_form(self):
        """They are already signed in; the form would be a dead end."""
        with patch.object(budget, "SIGN_IN_URL", "https://pexafy.com/auth/login/"), \
             patch.object(budget, "OPTIONS_URL", ""):
            got = budget.wall({"X-Quota-Limit": "5000", "X-Quota-Remaining": "0",
                               "X-Plan": "free"})
        self.assertNotIn("account_url", got)
        self.assertEqual(got["cta_label"], "")
        self.assertIn("next month", got["detail"])

    def test_an_account_at_its_limit_is_offered_the_page_when_there_is_one(self):
        """"Link to an informational page describing available plans or entitlement
        options" is permitted; linking to a checkout is not. The label names the page
        and makes no argument."""
        with patch.object(budget, "OPTIONS_URL", "https://pexafy.com/connectors/limits/"):
            got = budget.wall({"X-Quota-Limit": "5000", "X-Quota-Remaining": "0",
                               "X-Plan": "free"})
        self.assertEqual(got["account_url"], "https://pexafy.com/connectors/limits/")
        self.assertEqual(got["cta_label"], "Account options")
        for word in ("upgrade", "plan", "buy", "subscribe"):
            self.assertNotIn(word, got["cta_label"].lower())

    def test_no_page_configured_means_no_link(self):
        """Until such a page exists there is no honest link, so none is drawn."""
        with patch.object(budget, "OPTIONS_URL", ""):
            self.assertNotIn("account_url",
                             budget.wall({"X-Quota-Limit": "5000",
                                          "X-Quota-Remaining": "0", "X-Plan": "free"}))

    def test_it_always_returns_something(self):
        """By the time this is called the request is already refused, and a wall with
        no words is worse than a plain sentence."""
        got = budget.wall({})
        self.assertTrue(got["title"])
        self.assertTrue(got["message"])

    def test_no_wall_sells_anything(self):
        for headers in ({"X-Daily-Quota-Limit": "100", "X-Daily-Quota-Remaining": "0",
                         "X-Plan": "anonymous"},
                        {"X-Quota-Limit": "5000", "X-Quota-Remaining": "0",
                         "X-Plan": "free"}):
            msg = budget.wall(headers)["message"].lower()
            for word in WordingTests.FORBIDDEN:
                with self.subTest(word=word):
                    self.assertNotIn(word, msg)


class WidgetTests(unittest.TestCase):
    """The grid's head carries a BUTTON now, not a line of text.

    The line was only ever there past 80% of the allowance, which is the wrong moment
    to be shown where the door is; and a counter reading itself out on the card is the
    wrong altitude for something that matters once.
    """

    def test_the_button_exists_in_the_markup(self):
        from pexafy_mcp import widget

        self.assertIn('id="acct"', widget.GRID_HTML)
        self.assertIn('id="acctdot"', widget.GRID_HTML)
        self.assertIn('id="acctpop"', widget.GRID_HTML)

    def test_the_mark_and_the_panel_are_hidden_until_there_is_something_to_say(self):
        from pexafy_mcp import widget

        self.assertIn('<span class="dot" id="acctdot" hidden>', widget.GRID_HTML)
        self.assertIn('<div class="acctpop" id="acctpop" role="dialog" hidden>',
                      widget.GRID_HTML)

    def test_the_wall_has_its_own_element(self):
        from pexafy_mcp import widget

        self.assertIn('<div class="wall" id="wall" hidden>', widget.GRID_HTML)

    def test_the_grid_paints_both_from_the_tool_result(self):
        from pexafy_mcp import widget

        self.assertIn("function paintAccount", widget.GRID_HTML)
        self.assertIn("function paintWall", widget.GRID_HTML)
        self.assertIn("sc.budget", widget.GRID_HTML)
        self.assertIn("sc.account", widget.GRID_HTML)

    def test_the_mark_stops_beating(self):
        """A dot that pulses forever is an alarm; this is a notice."""
        from pexafy_mcp import widget

        start = widget.GRID_HTML.index("@keyframes dotbeat")
        self.assertIn("dotbeat 1.1s ease-in-out 3",
                      widget.GRID_HTML[start:start + 700])

    def test_the_wall_blurs_shapes_and_never_photographs(self):
        """A blurred real result would say "we found these and will not show you",
        which is untrue — nothing was searched."""
        from pexafy_mcp import widget

        start = widget.GRID_HTML.index("function paintWall")
        block = widget.GRID_HTML[start:start + 2600]
        self.assertIn('createElement("i")', block)
        self.assertNotIn("preview_url", block)

    def test_the_wall_keeps_the_head(self):
        """Same wordmark, same button, same corner — that continuity is the reason
        the refusal is drawn here instead of printed in the chat."""
        from pexafy_mcp import widget

        start = widget.GRID_HTML.index("function paintWall")
        block = widget.GRID_HTML[start:start + 3400]
        self.assertIn("headEl.hidden = false", block)

    def test_one_press_goes_straight_through_when_there_is_nothing_to_say(self):
        """A panel holding a single button is a door in front of a door. With no
        notice, the press acts: the site for somebody already signed in, and for a
        visitor the host's own connect-account flow — which is the whole point of the
        button being there on every answer rather than only near the wall."""
        from pexafy_mcp import widget

        start = widget.GRID_HTML.index('acctEl.addEventListener("click"')
        block = widget.GRID_HTML[start:start + 1100]
        # `wallEl.hidden` in the condition: the wall already says what the panel says,
        # a few centimetres below, so there it goes straight to the flow.
        self.assertIn("if (acctBudget && wallEl.hidden) { fillPop(); openPop(); return; }",
                      block)
        self.assertIn('openLink((acctWho && acctWho.home_url)', block)
        self.assertIn("connectAccount((acctWho && acctWho.sign_in_url)", block)

    def test_connecting_asks_the_host_before_it_opens_a_page(self):
        """The sign-in page creates an account; it does not ATTACH one. Only a tool
        call refused with the host's challenge does that, so it is tried first and the
        page is the last resort."""
        from pexafy_mcp import widget

        start = widget.GRID_HTML.index("async function connectAccount")
        block = widget.GRID_HTML[start:start + 1200]
        order = [block.index('callServerTool("connect_account"'),
                 block.index("speak(tx(CONNECT_ASK))"),
                 block.index('openLink(url, "signin")')]
        self.assertEqual(order, sorted(order), "the fallbacks are out of order")

    def test_the_wall_is_the_same_block_as_a_page_of_results(self):
        """The host has already sized its surface for the grid; a card that shrinks to
        a third of its height mid-conversation reads as something breaking."""
        from pexafy_mcp import widget

        start = widget.GRID_HTML.index("  .wallblur {")
        block = widget.GRID_HTML[start:start + 500]
        # The grid's own geometry, to the pixel.
        self.assertIn("gap: 10px; padding: 12px", block)
        self.assertIn("minmax(max(140px, (100% - 20px) / 3), 1fr)", block)
        self.assertIn("aspect-ratio: 1 / 1", block)
        # And its own first-page count, so the two stay in step.
        start = widget.GRID_HTML.index("function paintWall")
        self.assertIn("i < gridFirst()", widget.GRID_HTML[start:start + 1400])

    def test_the_walls_guard_expires(self):
        """An allowance comes back — at midnight, or the moment somebody attaches an
        account or moves up a plan — and this frame is the last to hear about it: it
        only learns from a tool result, and a grid that refuses to call can never
        receive one. Left permanent, the guard turned "you are out of searches" into
        "this conversation is dead"; the way out was to open a new one."""
        from pexafy_mcp import widget

        start = widget.GRID_HTML.index("async function refineFrom")
        block = widget.GRID_HTML[start:start + 2200]
        self.assertIn("Date.now() - wallSeenAt < WALL_GUARD_MS", block)
        self.assertIn("const WALL_GUARD_MS = 15000", widget.GRID_HTML)

    def test_every_link_goes_through_the_host_not_an_anchor_href(self):
        """A plain <a href> inside a sandboxed iframe navigates nowhere."""
        from pexafy_mcp import widget

        start = widget.GRID_HTML.index("async function connectAccount")
        block = widget.GRID_HTML[start:widget.GRID_HTML.index("function render(sc)")]
        self.assertIn('openLink(url, "account")', block)
        self.assertIn('openLink(url, "wall")', block)
        self.assertIn('openLink(url, "signin")', block)
        self.assertNotIn(".href =", block)


if __name__ == "__main__":
    unittest.main()


class TrailingSlashTests(unittest.TestCase):
    """A page URL keeps its slash.

    Django answers a missing trailing slash with a 301, so stripping one costs a
    redirect on every press of the button — through the host's own link opener, for a
    slash we removed ourselves. Only the site ROOT is trimmed, because paths are built
    on top of it.
    """

    def test_the_options_page_keeps_its_slash(self):
        with patch.dict("os.environ",
                        {"PEXAFY_ACCOUNT_OPTIONS_URL": "https://p.example/mcp/limits/"}):
            import importlib

            reloaded = importlib.reload(budget)
            self.assertEqual(reloaded.OPTIONS_URL, "https://p.example/mcp/limits/")
        importlib.reload(budget)

    def test_the_default_sign_in_path_ends_in_a_slash(self):
        self.assertTrue(budget.SIGN_IN_URL.endswith("/"))
        self.assertTrue(budget.HOME_URL.endswith("/"))


class WallLiftsTests(unittest.TestCase):
    """The wall is a photograph of a moment, and the moment passes.

    An allowance comes back — midnight, an account attached, a plan changed — and
    nothing tells the frame: it only ever hears from a tool result, and there will be
    no more of those on that screen. Measured with a real upgrade: it went on saying
    "This month's searches are used up" to somebody who had just bought more.
    """

    def test_the_wall_starts_watching(self):
        from pexafy_mcp import widget

        start = widget.GRID_HTML.index("function paintWall")
        block = widget.GRID_HTML[start:start + 3600]
        self.assertIn("watchForAllowance()", block)

    def test_the_watch_costs_no_search_and_stops(self):
        from pexafy_mcp import widget

        start = widget.GRID_HTML.index("function watchForAllowance")
        block = widget.GRID_HTML[start:start + 1300]
        # The probe, not a search: it reads the usage endpoint, exempt from the quota.
        self.assertIn('callServerTool("connect_account", { check_only: true })', block)
        self.assertIn("Date.now() < until", block)

    def test_it_says_the_wall_lifted_rather_than_pretending_to_search(self):
        """The grid never held the words of the search, so it cannot re-run it. What it
        can do is stop lying and hand the next move back."""
        from pexafy_mcp import widget

        start = widget.GRID_HTML.index("function markAllowanceBack")
        block = widget.GRID_HTML[start:start + 1200]
        self.assertIn("Your searches are back", block)
        self.assertIn("acctBudget = null", block)
        self.assertIn("wallSeenAt = 0", block)


class WayOutTests(unittest.TestCase):
    """A wall needs a way out that depends on nothing the host has to grant.

    The watch needs `serverTools`; asking the model to search again needs only a
    message, which is the last channel to go. Measured 2026-09-15: the frame is served
    from the host's cache, so a widget change is not even live until the connector is
    reinstalled — all the more reason for the screen to carry both doors.
    """

    def test_the_wall_offers_to_ask_again(self):
        from pexafy_mcp import widget

        start = widget.GRID_HTML.index("function paintWall")
        block = widget.GRID_HTML[start:start + 4200]
        self.assertIn("Try again", block)
        self.assertIn("searchAgain();", block)
        # …which asks the model first, and runs the search itself only where the host
        # carries no message (test_widget_ide_hosts).
        again = widget.GRID_HTML[widget.GRID_HTML.index("async function searchAgain() {"):]
        self.assertIn("if (speak(tx(TRY_AGAIN_ASK)))", again[:400])

    def test_asking_again_is_an_instruction_to_the_model(self):
        """The grid never held the words of the search; the conversation did."""
        from pexafy_mcp import widget

        # "my last" and not "that": the model has to find the search in the
        # conversation, and "that" points at nothing it can resolve.
        self.assertIn('const TRY_AGAIN_ASK = "Run my last Pexafy search again."',
                      widget.GRID_HTML)
