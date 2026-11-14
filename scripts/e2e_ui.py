"""End-to-end UI check against a running stack (default http://127.0.0.1:8001, password from NA_E2E_PASSWORD).

Needs Playwright and a Chrome: python -m venv v && v/bin/pip install playwright && v/bin/python scripts/e2e_ui.py
The AI endpoints are answered with canned replies (free services are rate limited and slow), everything else is the real backend.
Screenshots go to /tmp/na-e2e. Exits non-zero when a check fails or the browser logs an error."""
import json
import os
import sys

from playwright.sync_api import sync_playwright

BASE = os.environ.get("NA_E2E_URL", "http://127.0.0.1:8001")
PW = os.environ.get("NA_E2E_PASSWORD") or sys.exit("Set NA_E2E_PASSWORD to the password of the running app")
CHROME = os.environ.get("NA_E2E_CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
OUT = "/tmp/na-e2e"
os.makedirs(OUT, exist_ok=True)
errors, failures, passed = [], [], []


def check(name, cond, detail=""):
    (passed if cond else failures).append(f"{name}{': ' + str(detail) if detail and not cond else ''}")
    print(("ok   " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))


STORY_AI = {"neutral_headline": "Neutral headline", "summary": [{"text": "Two outlets report the same event.", "cite": ["S1", "S2"]}],
            "established": [{"text": "Both give the same figure.", "cite": ["S1", "S2"]}], "disputed": [], "single_source": [{"text": "Only one says 9 people were there.", "cite": ["S1"], "unverified": True}],
            "official_statements": [], "social_signals": [], "framing": [], "context": [], "unknowns": ["Who is responsible"], "what_to_watch": [], "confidence": "medium", "best_reads": ["S1"],
            "sources": {"S1": {"id": "S1", "name": "Outlet One", "type": "news", "band": "reliable", "url": "https://example.org/1", "title": "t1", "country": "IN"},
                        "S2": {"id": "S2", "name": "Outlet Two", "type": "official", "band": "unrated", "url": "https://example.org/2", "title": "t2", "country": "GB"}},
            "quality": {"dropped_uncited": 1, "invalid_ids_removed": 0, "numbers_not_found": 1}, "meta": {"cached": False, "provider": "groq", "model": "llama", "created_at": "2026-11-01T00:00:00Z", "basis": "headlines and publisher summaries"}}
BRIEF_AI = {"title": "Worldwide, last 24 hours", "big_picture": [{"text": "A busy day.", "cite": ["S1"]}],
            "stories": [{"story_id": "D1", "headline": "Story one", "what_happened": [{"text": "Something happened.", "cite": ["S1"]}], "why_it_matters": [], "where_sources_differ": []}],
            "threads": [], "watch_next": [{"text": "A vote on Friday.", "cite": ["S1"]}], "not_known_yet": ["The cause"],
            "sources": STORY_AI["sources"], "scope": {"place": "world", "topic": "", "name": "Worldwide", "stories": {"D1": {"id": 1, "title": "Story one", "breaking": True, "verification": {"level": "confirmed", "label": "Confirmed by 2 reliable outlets", "outlets": 2, "reliable": 2}}}},
            "quality": {"dropped_uncited": 0, "invalid_ids_removed": 0, "numbers_not_found": 0}, "meta": {"cached": True, "provider": "groq", "model": "llama", "created_at": "2026-11-01T00:00:00Z", "basis": "headlines", "length": "standard"}}


def wait_until(pg, fn, timeout=15000):
    waited = 0
    while waited < timeout:
        if fn():
            return True
        pg.wait_for_timeout(250)
        waited += 250
    return False


def main():
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, headless=True)
        ctx = b.new_context(viewport={"width": 1440, "height": 900})
        pg = ctx.new_page()
        pg.on("console", lambda m: errors.append((m.type, m.text)) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errors.append(("pageerror", str(e))))
        pg.on("response", lambda r: print("   HTTP", r.status, r.url) if r.status >= 400 else None)
        pg.route("**/api/ai/story", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(STORY_AI)))
        pg.route("**/api/ai/briefing", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(BRIEF_AI)))

        pg.goto(BASE)
        pg.wait_for_selector("input[type=password]")
        pg.fill("#pw", PW)
        pg.click("button.btn")
        pg.wait_for_selector(".story", timeout=30000)

        # ---- tabs, verification, load more -------------------------------------------------
        tabs = [t.strip() for t in pg.locator("[role=tab]").all_inner_texts()]
        check("tabs are Top, Trending, Storylines, Saved (no Following, no Latest tab)", tabs == ["Top", "Trending", "Storylines", "Saved"], tabs)
        check("Top has the views Aggregated, Fresh, Ongoing", pg.locator(".views button").all_inner_texts() == ["Aggregated", "Fresh", "Ongoing"])
        check("every row says when its event is", pg.locator(".story p.when").count() == pg.locator(".story").count() and
              all(t.startswith(("First reported", "Scheduled")) for t in pg.locator(".story p.when").all_inner_texts()))
        pg.locator(".views button", has_text="Ongoing").click()
        check("choosing a view shows it in the address", wait_until(pg, lambda: "view=ongoing" in pg.url), pg.url)
        pg.wait_for_selector(".story", timeout=30000)
        check("the Ongoing list has stories", pg.locator(".story").count() > 0)
        pg.locator(".views button", has_text="Fresh").click()
        pg.wait_for_selector(".story", timeout=30000)
        check("Fresh lists stories", pg.locator(".story").count() > 0)
        pg.locator(".views button", has_text="Aggregated").click()
        pg.wait_for_timeout(1000)
        check("Trending has the same three views", (pg.get_by_role("tab", name="Trending").click() or True) and pg.locator(".views button").all_inner_texts() == ["Aggregated", "Fresh", "Ongoing"])
        pg.get_by_role("tab", name="Top").click()
        pg.wait_for_timeout(800)
        # several places at once: tick two continents in the Where menu
        pg.locator(".crumbs[aria-label=Where] button.narrow").click()
        pg.wait_for_selector(".menu .mrow")
        pg.locator(".menu .mrow", has_text="Asia").locator("button.box").first.click()
        pg.wait_for_timeout(800)
        pg.locator(".menu .mrow", has_text="Europe").locator("button.box").first.click()
        pg.wait_for_timeout(1500)
        check("ticking two places shows both as chips", pg.locator(".crumbs[aria-label=Where] .chip").count() == 2 and "place=asia%2Ceurope" in pg.url.replace(",", "%2C"), pg.url)
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(1200)
        check("a combined scope lists stories", pg.locator(".story").count() > 0)
        pg.locator(".crumbs[aria-label=Where] button.link", has_text="Clear").click()
        pg.wait_for_timeout(1000)
        check("every row carries a verification mark", pg.locator(".story .vtag").count() == pg.locator(".story").count())
        n0 = pg.locator(".story").count()
        more = pg.locator("button.more")
        check("Top lists stories", n0 > 0, n0)
        if more.count():
            more.click()
            check("Show more loads another page", wait_until(pg, lambda: pg.locator(".story").count() > n0))
        pg.screenshot(path=f"{OUT}/news-latest.png")

        # Storylines tab: ranked storylines for a chosen period, with activity bars and their developments
        pg.goto(BASE + "/?tab=storylines")
        pg.wait_for_selector(".story.line", timeout=20000)
        n_lines = pg.locator(".story.line").count()
        check("storylines tab lists storylines", n_lines >= 3, f"{n_lines} storylines")
        check("storyline cards show activity bars", pg.locator(".story.line .spark i").count() >= 24)
        check("period buttons present", pg.locator(".views.ranges button").count() == 8)
        pg.locator(".views.ranges button", has_text="7 days").click()
        pg.wait_for_url("**range=168**", timeout=10000)
        pg.wait_for_selector(".story.line", timeout=20000)
        check("7-day period lists storylines", pg.locator(".story.line").count() >= 3)
        check("multi-development storylines show their timeline", pg.locator(".story.line .timeline.mini li").count() >= 2)
        pg.screenshot(path=f"{OUT}/storylines.png")
        pg.locator(".story.line").first.click()
        pg.wait_for_selector(".reading h2", timeout=15000)
        check("opening a storyline opens its story", pg.locator(".reading h2").count() == 1)
        pg.screenshot(path=f"{OUT}/storyline-open.png")
        pg.get_by_role("tab", name="Top").click()
        pg.wait_for_selector(".story")

        # ---- a story: AI digest, reading the text --------------------------------------------
        pg.locator(".story").first.click()
        pg.wait_for_selector(".coverage li")
        check("pane shows the verification explanation", pg.locator(".vtag.big").count() == 1)
        check("story view has a labelled reliability breakdown", pg.locator("figure.bands .bandkey li").count() >= 1 and "Reliability of the" in pg.inner_text("figure.bands"))
        check("rows have no unlabelled source strip", pg.locator(".story .strip").count() == 0)
        check("the Narrow control uses an icon, not a text arrow", pg.locator("button.narrow svg").count() >= 1 and "▾" not in pg.inner_text("header.top"))
        pg.get_by_role("button", name="Brief me on this story").click()
        pg.wait_for_selector(".aiout h5")
        check("AI digest renders with citation markers", pg.locator(".aiout .cite").count() >= 3)
        check("unverified number is flagged", pg.locator(".unver").count() == 1)
        check("sourcing line reports the checks", "uncited statements removed" in pg.locator(".aimeta").last.inner_text())
        pg.screenshot(path=f"{OUT}/story-ai.png")
        pg.get_by_role("button", name="Read here").first.click()
        pg.get_by_role("button", name="Read the text here").click()
        try:
            pg.wait_for_selector(".atext p", timeout=45000)
            check("article text is fetched and shown", pg.locator(".atext p").count() >= 1)
        except Exception:
            check("article text is fetched and shown (or a clear note)", pg.locator(".reader .aimeta, .reader .note").count() > 0)
        pg.screenshot(path=f"{OUT}/story-read.png")

        # ---- floating briefing --------------------------------------------------------------
        pg.get_by_role("button", name="Briefing", exact=True).click()
        pg.wait_for_selector(".briefing")
        pg.get_by_role("button", name="Write a briefing for this list").click()
        pg.wait_for_selector(".briefing .aiout h5")
        check("briefing shows stories with verification and sources", pg.locator(".briefing .vtag.confirmed").count() == 1 and pg.locator(".briefing details.srclist").count() == 1)
        box0 = pg.locator(".briefing").bounding_box()
        head = pg.locator(".briefing .bhead").bounding_box()
        pg.mouse.move(head["x"] + 60, head["y"] + 12)
        pg.mouse.down()
        pg.mouse.move(head["x"] - 240, head["y"] + 120, steps=8)
        pg.mouse.up()
        box1 = pg.locator(".briefing").bounding_box()
        check("briefing can be dragged", abs(box1["x"] - box0["x"]) > 150 and abs(box1["y"] - box0["y"]) > 60, (box0, box1))
        pg.screenshot(path=f"{OUT}/briefing.png")
        pg.get_by_label("Minimise briefing").click()
        check("briefing minimises to a bar", pg.locator(".briefing .bbody").count() == 0 and pg.locator(".briefing").bounding_box()["height"] < 80)
        pg.get_by_label("Expand briefing").click()
        pg.get_by_label("Close briefing").click()
        check("briefing closes", pg.locator(".briefing").count() == 0)

        # ---- explore -------------------------------------------------------------------------
        pg.goto(BASE + "/explore")
        pg.fill("input.q", "cricket")
        pg.get_by_role("button", name="Explore", exact=True).click()
        pg.wait_for_selector(".tiles b", timeout=20000)
        pg.wait_for_selector(".chart svg")
        check("explore shows totals, a timeline and facet bars", pg.locator(".tiles b").count() == 5 and pg.locator(".bars li").count() > 3)
        pg.screenshot(path=f"{OUT}/explore.png", full_page=True)
        first_source = pg.locator(".card2:has(h3:has-text('Who published')) .bars button").first
        name = first_source.locator(".lbl").inner_text().split("\n")[0]
        first_source.click()
        pg.wait_for_selector(".chip.on")
        check("clicking a bar narrows the question", pg.locator(".chip.on").count() == 1, name)
        pg.locator(".chip.on").click()
        pg.get_by_label("Compare queries").check()
        pg.get_by_placeholder("Query 1").fill("cricket")
        pg.get_by_placeholder("Query 2").fill("football")
        pg.get_by_role("button", name="Compare").click()
        pg.wait_for_selector("polyline", timeout=20000)
        check("compare draws one line per query", pg.locator("figure polyline").count() == 2)
        pg.screenshot(path=f"{OUT}/explore-compare.png")

        # ---- sources: discovery -----------------------------------------------------------------
        pg.goto(BASE + "/sources")
        pg.wait_for_selector(".tiles b")
        check("sources page shows discovery status tiles", pg.locator(".tiles b").count() == 5)
        pg.get_by_label("Stage").select_option("candidate")
        pg.wait_for_timeout(1200)
        rows = pg.locator("tbody tr.click")
        check("candidates can be listed", rows.count() > 0, rows.count())
        if rows.count():
            rows.first.click()
            pg.wait_for_selector(".subtle")
            check("a candidate explains its stage", "Stage: Candidate" in pg.locator(".subtle").first.inner_text())
        pg.screenshot(path=f"{OUT}/sources-candidates.png", full_page=True)
        pg.get_by_label("Stage").select_option("")
        check("paging controls exist", pg.get_by_role("button", name="Next").count() == 1)

        # ---- status ------------------------------------------------------------------------------------
        pg.goto(BASE + "/status")
        pg.wait_for_selector(".tiles b", timeout=20000)
        txt = pg.locator(".explore").inner_text()
        check("status page shows totals, last-run times and coverage", all(w in txt for w in ("sources known", "When things last happened", "Coverage of the world", "Newest article collected", "Web search for sources")))
        check("status page lists categories and discovery per day", pg.locator(".bars li").count() > 20 and "discovered per day" in txt)
        pg.screenshot(path=f"{OUT}/status.png", full_page=True)
        pg.goto(BASE + "/sources?band=unrated")
        pg.wait_for_selector("tbody tr.click", timeout=20000)
        check("status links open a filtered source list", pg.get_by_label("Reliability band").input_value() == "unrated")

        # ---- settings: AI, tokens, prompts --------------------------------------------------------
        pg.goto(BASE + "/settings")
        pg.wait_for_selector("#ai")
        pg.evaluate("document.getElementById('ai').scrollIntoView()")
        pg.wait_for_timeout(600)
        check("AI providers are listed", pg.locator(".prov").count() >= 10, pg.locator(".prov").count())
        check("prompts are editable text areas", pg.locator("#ai textarea.area").count() == 4, pg.locator("#ai textarea.area").count())
        pg.evaluate("document.getElementById('social').scrollIntoView()")
        pg.wait_for_timeout(600)
        check("social section lists every platform and an X login", pg.locator("#social .socialpanel dt").count() >= 9 and pg.locator("#social a:has-text('Log in with X')").count() == 1)
        check("social logins are password inputs", pg.locator("#social input[type=password]").count() >= 5, pg.locator("#social input[type=password]").count())
        pg.screenshot(path=f"{OUT}/settings-ai.png")
        pg.evaluate("document.getElementById('sources').scrollIntoView()")
        pg.wait_for_timeout(500)
        check("Media Cloud token field is a password input", pg.locator("input[type=password][aria-label='Media Cloud API token']").count() == 1)
        check("a password change form exists", pg.locator("input[autocomplete=new-password]").count() == 2)
        check("no token value is present in the page", "7c3bcd" not in pg.content())
        pg.screenshot(path=f"{OUT}/settings-sources.png")

        # ---- phone -----------------------------------------------------------------------------------
        ph_ctx = b.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
        ph = ph_ctx.new_page()
        ph.on("console", lambda m: errors.append((m.type, m.text)) if m.type == "error" else None)
        ph.route("**/api/ai/briefing", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(BRIEF_AI)))
        ph.goto(BASE)
        ph.fill("#pw", PW)
        ph.click("button.btn")
        ph.wait_for_selector(".story", timeout=30000)
        ph.get_by_role("button", name="Briefing", exact=True).click()
        ph.wait_for_selector(".briefing.phone")
        b_ = ph.locator(".briefing").bounding_box()
        check("briefing docks to the bottom on a phone", b_["y"] + b_["height"] >= 840 and b_["width"] >= 385, b_)
        ph.screenshot(path=f"{OUT}/phone-briefing.png")
        ph.goto(BASE + "/explore")
        ph.wait_for_selector("input.q")
        sw = ph.evaluate("document.documentElement.scrollWidth")
        check("explore does not scroll sideways on a phone", sw <= 392, sw)
        b.close()

    bad = [e for e in errors if not (e[0] == "error" and "favicon" in e[1])]
    check("no console errors", not bad, bad[:3])
    print(f"\n{len(passed)} passed, {len(failures)} failed")
    sys.exit(1 if failures else 0)


main()
