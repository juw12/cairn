"""The voice daily log: centre button, spoken commands, typing fallback, and the Journal & memories view.

A fake SpeechRecognition stands in for the microphone: window.__say(text) delivers a final transcript to the
recognizer the app started, exactly like the browser engine would.
"""

import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from webserver import install_github_mock, launch_browser, start_static_server  # noqa: E402

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    if os.environ.get("CAIRN_REQUIRE_BROWSER"):
        raise
    sync_playwright = None

FAKE_SPEECH = """
(() => {
  class FakeRecognition {
    constructor() { this.continuous = false; this.interimResults = false; this.lang = 'en-US'; window.__recognizer = this; }
    start() { this.running = true; setTimeout(() => { this.onstart && this.onstart(); }, 5); }
    stop() { this.running = false; setTimeout(() => this.onend && this.onend(), 5); }
    abort() { this.stop(); }
  }
  window.SpeechRecognition = FakeRecognition;
  window.webkitSpeechRecognition = FakeRecognition;
  window.__say = (text, final = true) => {
    const r = window.__recognizer;
    if (!r || !r.onresult) return false;
    const result = [{ transcript: text, confidence: 0.9 }];
    result.isFinal = final;
    r.onresult({ resultIndex: 0, results: [result] });
    return true;
  };
})();
"""

NO_SPEECH = "delete window.SpeechRecognition; delete window.webkitSpeechRecognition;"

TODAY_JS = "() => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; }"


@unittest.skipIf(sync_playwright is None, "playwright not installed")
class VoiceLogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server, cls.url = start_static_server()
        cls.pw = sync_playwright().start()
        cls.browser = launch_browser(cls.pw)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.server.shutdown()

    def open(self, query="?demo=1", viewport=(1280, 860), speech=True, wait="#nav button"):
        context = self.browser.new_context(viewport={"width": viewport[0], "height": viewport[1]})
        install_github_mock(context)
        page = context.new_page()
        self.errors = []
        page.on("console", lambda m: self.errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: self.errors.append(str(e)))
        page.add_init_script("localStorage.setItem('cairn.theme', 'light')")
        page.add_init_script(FAKE_SPEECH if speech else NO_SPEECH)
        page.goto(self.url + query)
        page.wait_for_selector(wait)
        self.addCleanup(context.close)
        self.today = page.evaluate(TODAY_JS)
        return page

    def journal_text(self, page, day=None):
        path = f"journal/{day or self.today}.md"
        return page.evaluate("(p) => window.cairn.state.files.find((f) => f.path === p)?.text ?? ''", path)

    def open_sheet_listening(self, page):
        page.click("#log-fab")
        page.wait_for_selector(".log-sheet")
        page.wait_for_function("() => window.__recognizer && window.__recognizer.onresult && window.__recognizer.running")

    def say(self, page, text):
        self.assertTrue(page.evaluate("(t) => window.__say(t)", text), "the recognizer was listening")

    # ------------------------------------------------------------------ pure functions

    def test_daylog_parsing_routing_and_memory(self):
        page = self.open()
        result = page.evaluate(
            """async () => {
              const dl = await import('./daylog.js');
              const body = dl.appendEntry(dl.appendEntry('', '08:10', 'Walked to work #health'), '09:00', 'Task: [[ship-beta|Ship beta]]');
              const days = new Map([
                ['2026-03-01', { entries: [{ text: 'a' }] }],
                ['2026-03-02', { entries: [{ text: 'b' }] }],
                ['2026-03-04', { entries: [{ text: 'c' }] }],
              ]);
              return {
                body,
                entries: dl.parseEntries(body).map((e) => [e.time, e.kind, e.tags, e.links]),
                todo: dl.voiceIntent('Hey Cairn, todo call the dentist #health'),
                remind: dl.voiceIntent('remind me to water the plants'),
                idea: dl.voiceIntent('idea: a map of every walk'),
                decision: dl.voiceIntent('decision use SQLite'),
                mood: dl.voiceIntent('I feel great today'),
                undo: dl.voiceIntent('scratch that').kind,
                stop: dl.voiceIntent('stop listening').kind,
                read: dl.voiceIntent('what did I do yesterday'),
                plain: dl.voiceIntent('finished the GPX export.'),
                moodLine: dl.moodLine(dl.MOODS[3]),
                streak: dl.streaks(days, '2026-03-04'),
                streakYesterday: dl.streaks(days, '2026-03-03'),
                streakBroken: dl.streaks(days, '2026-03-06'),
                review: dl.weeklyReview(new Map(), [], '2026-03-04').title,
              };
            }"""
        )
        self.assertIn("## Log", result["body"])
        self.assertIn("- 08:10 Walked to work #health", result["body"])
        self.assertEqual(result["entries"][0], ["08:10", "log", ["health"], []])
        self.assertEqual(result["entries"][1][1], "task")
        self.assertEqual((result["todo"]["kind"], result["todo"]["text"]), ("task", "Call the dentist #health"))
        self.assertEqual((result["remind"]["kind"], result["remind"]["text"]), ("task", "Water the plants"))
        self.assertEqual(result["idea"]["kind"], "idea")
        self.assertEqual(result["decision"]["kind"], "decision")
        self.assertEqual(result["mood"]["kind"], "mood")
        self.assertEqual(result["mood"]["mood"]["score"], 5)
        self.assertEqual(result["undo"], "undo")
        self.assertEqual(result["stop"], "stop")
        self.assertEqual((result["read"]["kind"], result["read"]["day"]), ("read", -1))
        self.assertEqual(result["plain"]["kind"], "log")
        self.assertEqual(result["moodLine"], "Mood: good (4/5)")
        self.assertEqual((result["streak"]["current"], result["streak"]["longest"], result["streak"]["loggedToday"]), (1, 2, True))
        # Not logged yet today: yesterday's run still counts until a whole day is missed.
        self.assertEqual((result["streakYesterday"]["current"], result["streakYesterday"]["loggedToday"]), (2, False))
        self.assertEqual(result["streakBroken"]["current"], 0)
        self.assertIn("2026", result["review"])

    # ------------------------------------------------------------------ hands-free capture

    def test_centre_button_logs_spoken_line_to_todays_markdown(self):
        page = self.open()
        fab = page.locator("#log-fab")
        self.assertTrue(fab.is_visible())
        box = fab.bounding_box()
        self.assertAlmostEqual(box["x"] + box["width"] / 2, 640, delta=4, msg="the log button sits in the middle")
        self.open_sheet_listening(page)
        self.assertEqual(page.get_attribute(".log-mic", "aria-pressed"), "true")
        self.say(page, "finished the GPX export and it feels great")
        page.wait_for_function(
            "(p) => (window.cairn.state.files.find((f) => f.path === p)?.text || '').includes('GPX export')",
            arg=f"journal/{self.today}.md",
        )
        text = self.journal_text(page)
        self.assertRegex(text, r"(?m)^- \d\d:\d\d Finished the GPX export and it feels great$")
        self.assertIn("## Log", text)
        page.wait_for_selector(".log-entry .entry-text:has-text('GPX export')")
        # Delete it again from the timeline.
        page.click(".log-entry:has-text('GPX export') [data-log='remove']")
        page.wait_for_function(
            "(p) => !(window.cairn.state.files.find((f) => f.path === p)?.text || '').includes('GPX export')",
            arg=f"journal/{self.today}.md",
        )
        page.keyboard.press("Escape")
        page.wait_for_selector(".log-sheet", state="detached")
        self.assertEqual(self.errors, [])

    def test_spoken_todo_creates_linked_task_and_undo_removes_both(self):
        page = self.open()
        before = page.evaluate("window.cairn.state.notes.filter((n) => n.type === 'task').length")
        self.open_sheet_listening(page)
        self.say(page, "todo book the train to Lyon")
        page.wait_for_function(
            "(n) => window.cairn.state.notes.filter((t) => t.type === 'task').length === n + 1", arg=before
        )
        task = page.evaluate("window.cairn.state.notes.find((n) => n.type === 'task' && n.title === 'Book the train to Lyon')")
        self.assertIsNotNone(task)
        page.wait_for_function(
            "(p) => (window.cairn.state.files.find((f) => f.path === p)?.text || '').includes('Task: [[')",
            arg=f"journal/{self.today}.md",
        )
        self.assertIn(f"Task: [[{task['slug']}|Book the train to Lyon]]", self.journal_text(page))
        self.say(page, "scratch that")
        page.wait_for_function(
            "(n) => window.cairn.state.notes.filter((t) => t.type === 'task').length === n", arg=before
        )
        self.assertNotIn("ook the train", self.journal_text(page))
        self.assertEqual(self.errors, [])

    def test_mood_by_voice_and_by_tap(self):
        page = self.open()
        self.open_sheet_listening(page)
        self.say(page, "mood good")
        page.wait_for_function(
            "(p) => (window.cairn.state.files.find((f) => f.path === p)?.text || '').includes('(4/5)')",
            arg=f"journal/{self.today}.md",
        )
        page.click(".log-sheet [data-mood='5']")
        page.wait_for_function(
            "(p) => (window.cairn.state.files.find((f) => f.path === p)?.text || '').includes('(5/5)')",
            arg=f"journal/{self.today}.md",
        )
        text = self.journal_text(page)
        self.assertIn("Mood: good (4/5)", text)
        self.assertIn("Mood: great (5/5)", text)
        self.assertEqual(self.errors, [])

    def test_stop_command_ends_listening(self):
        page = self.open()
        self.open_sheet_listening(page)
        self.say(page, "stop")
        page.wait_for_function("() => document.querySelector('.log-mic')?.getAttribute('aria-pressed') === 'false'")
        self.assertFalse(page.evaluate("window.__recognizer.running"))

    def test_typing_fallback_without_speech_and_l_shortcut(self):
        page = self.open(speech=False)
        page.locator("body").click(position={"x": 700, "y": 300})
        page.keyboard.press("l")
        page.wait_for_selector(".log-sheet")
        self.assertTrue(page.locator(".log-mic").is_disabled())
        self.assertIn("type", page.inner_text("#log-status").lower())
        page.fill("#log-text", "walked the dog in the rain")
        page.keyboard.press("Enter")
        page.wait_for_function(
            "(p) => (window.cairn.state.files.find((f) => f.path === p)?.text || '').includes('Walked the dog')",
            arg=f"journal/{self.today}.md",
        )
        self.assertEqual(page.input_value("#log-text"), "")
        # Typed prefixes route like spoken ones.
        page.fill("#log-text", "idea: a pocket map of every walk")
        page.keyboard.press("Enter")
        page.wait_for_function("() => window.cairn.state.notes.some((n) => n.type === 'idea' && n.title === 'A pocket map of every walk')")
        self.assertEqual(self.errors, [])

    def test_read_only_brain_has_no_log_button(self):
        page = self.open("?brain=juw12/cairn", wait=".ro-banner")
        self.assertEqual(page.locator("#log-fab:visible").count(), 0)
        page.locator("body").click(position={"x": 700, "y": 300})
        page.keyboard.press("l")
        page.wait_for_timeout(200)
        self.assertEqual(page.locator(".log-sheet").count(), 0)
        page.click('#nav [data-go="journal"]')
        page.wait_for_selector(".memory-stats")
        self.assertEqual(page.locator("[data-jl='log']").count(), 0)
        self.assertEqual(self.errors, [])

    # ------------------------------------------------------------------ daily memory

    def test_journal_memories_heatmap_search_and_weekly_review(self):
        page = self.open()
        page.click('#nav [data-go="journal"]')
        page.wait_for_selector(".memory-stats")
        self.assertEqual(page.locator(".heat-grid .heat").count(), 18 * 7)
        self.assertGreater(page.locator(".heat-grid .heat.l1, .heat-grid .heat.l2, .heat-grid .heat.l3").count(), 4)
        self.assertGreaterEqual(page.locator(".lookback").count(), 3)
        self.assertEqual(page.locator(".mood-chart").count(), 1)
        page.fill("#journal-search", "#beta")
        page.wait_for_selector("#journal-results .search-hits li")
        self.assertGreaterEqual(page.locator("#journal-results .search-hits li").count(), 2)
        page.click("[data-jl='review']")
        page.wait_for_function("() => window.cairn.state.files.some((f) => /^journal\\/\\d{4}-W\\d\\d\\.md$/.test(f.path))")
        review = page.evaluate("window.cairn.state.files.find((f) => /^journal\\/\\d{4}-W\\d\\d\\.md$/.test(f.path)).text")
        self.assertIn("review", review)
        self.assertTrue(re.search(r"(?m)^#|^## ", review))
        self.assertEqual(self.errors, [])

    def test_home_strip_opens_the_log(self):
        page = self.open()
        page.wait_for_selector(".home-log")
        self.assertIn("Today's log", page.inner_text(".home-log"))
        page.click(".home-log [data-log-open]")
        page.wait_for_selector(".log-sheet")
        self.assertEqual(self.errors, [])

    def test_phone_layout_has_no_overflow(self):
        page = self.open(viewport=(390, 844))
        self.assertTrue(page.locator("#log-fab").is_visible())
        self.open_sheet_listening(page)
        self.assertFalse(page.evaluate("() => document.documentElement.scrollWidth > innerWidth + 1"))
        page.keyboard.press("Escape")
        page.wait_for_selector(".log-sheet", state="detached")
        page.evaluate("async () => (await import('./app.js')).setView('journal')")
        page.wait_for_selector(".memory-stats")
        self.assertFalse(page.evaluate("() => document.documentElement.scrollWidth > innerWidth + 1"))
        self.assertEqual(self.errors, [])


    # ------------------------------------------------------------------ round 2: wake word, language, offline, people

    def test_hey_cairn_wake_word_opens_the_sheet_and_logs_the_rest(self):
        page = self.open()
        page.evaluate("localStorage.setItem('cairn.voicelog', JSON.stringify({ wake: true }))")
        page.reload()
        page.wait_for_selector("#log-fab.wake-on")
        page.wait_for_function("() => window.__recognizer && window.__recognizer.running && window.__recognizer.continuous")
        self.assertEqual(page.locator(".log-sheet").count(), 0)
        # Speech that does not start with the wake word is ignored.
        self.say(page, "just chatting with someone nearby")
        page.wait_for_timeout(150)
        self.assertEqual(page.locator(".log-sheet").count(), 0)
        self.say(page, "hey cairn finished the export")
        page.wait_for_selector(".log-sheet")
        page.wait_for_function(
            "(p) => (window.cairn.state.files.find((f) => f.path === p)?.text || '').includes('Finished the export')",
            arg=f"journal/{self.today}.md",
        )
        self.assertNotIn("just chatting", self.journal_text(page))
        self.assertNotIn("hey cairn", self.journal_text(page).lower())
        # Closing the sheet goes back to waiting for the wake word.
        page.keyboard.press("Escape")
        page.wait_for_selector(".log-sheet", state="detached")
        page.wait_for_function("() => window.__recognizer && window.__recognizer.running && window.__recognizer.continuous")
        self.assertEqual(self.errors, [])

    def test_wake_word_is_off_by_default_and_toggles_from_the_sheet(self):
        page = self.open()
        self.assertEqual(page.locator("#log-fab.wake-on").count(), 0)
        page.click("#log-fab")
        page.wait_for_selector(".log-sheet")
        page.check(".log-sheet [data-pref='wake']")
        self.assertTrue(page.evaluate("JSON.parse(localStorage.getItem('cairn.voicelog')).wake"))
        page.keyboard.press("Escape")
        page.wait_for_selector("#log-fab.wake-on")
        self.assertEqual(self.errors, [])

    def test_language_picker_saves_and_is_used_by_the_recognizer(self):
        page = self.open()
        self.open_sheet_listening(page)
        page.select_option(".log-sheet [data-pref-select='lang']", "fr-FR")
        self.assertEqual(page.evaluate("JSON.parse(localStorage.getItem('cairn.voicelog')).lang"), "fr-FR")
        page.wait_for_function("() => window.__recognizer && window.__recognizer.running && window.__recognizer.lang === 'fr-FR'")
        self.assertEqual(self.errors, [])

    def test_offline_status_says_typing_still_works(self):
        page = self.open()
        page.context.set_offline(True)
        page.click("#log-fab")
        page.wait_for_selector(".log-sheet")
        page.wait_for_function("() => /offline/i.test(document.querySelector('#log-status')?.textContent || '')")
        self.assertEqual(page.locator(".log-voice.is-offline").count(), 1)
        page.fill("#log-text", "wrote notes on the train")
        page.keyboard.press("Enter")
        page.wait_for_function(
            "(p) => (window.cairn.state.files.find((f) => f.path === p)?.text || '').includes('Wrote notes on the train')",
            arg=f"journal/{self.today}.md",
        )
        page.context.set_offline(False)
        page.wait_for_function("() => !/offline/i.test(document.querySelector('#log-status')?.textContent || '')")

    def test_first_run_hint_shows_once(self):
        page = self.open()
        self.assertEqual(page.locator("#log-fab .log-fab-hint").count(), 1)
        page.click("#log-fab")
        page.wait_for_selector(".log-sheet")
        self.assertEqual(page.locator(".log-fab-hint").count(), 0)
        page.keyboard.press("Escape")
        page.reload()
        page.wait_for_selector("#log-fab")
        page.wait_for_timeout(100)
        self.assertEqual(page.locator(".log-fab-hint").count(), 0)

    def test_people_are_found_chipped_and_linked_from_speech(self):
        page = self.open()
        found = page.evaluate(
            """async () => {
              const dl = await import('./daylog.js');
              return {
                people: dl.peopleIn('Coffee with @Sam and @ana-lu, mail me at a@b.com'),
                entry: dl.parseEntries(dl.appendEntry('', '09:00', 'Call @sam about #beta')).map((e) => e.people)[0],
              };
            }"""
        )
        self.assertEqual(found["people"], ["sam", "ana-lu"])
        self.assertEqual(found["entry"], ["sam"])
        page.click('#nav [data-go="journal"]')
        page.wait_for_selector(".memory-stats")
        chip = page.locator(".tag.person[data-jl-search='@sam']")
        self.assertEqual(chip.count(), 1)
        chip.click()
        page.wait_for_selector("#journal-results .search-hits li")
        self.assertEqual(page.input_value("#journal-search"), "@sam")
        # A spoken name of someone already @mentioned becomes a mention.
        self.open_sheet_listening(page)
        self.say(page, "lunch with Sam by the river")
        page.wait_for_function(
            "(p) => (window.cairn.state.files.find((f) => f.path === p)?.text || '').includes('Lunch with @sam by the river')",
            arg=f"journal/{self.today}.md",
        )
        self.assertEqual(self.errors, [])

    def test_mood_range_and_look_back_details(self):
        page = self.open()
        page.click('#nav [data-go="journal"]')
        page.wait_for_selector(".memory-stats")
        self.assertEqual(page.get_attribute("[data-jl-range='30']", "aria-pressed"), "true")
        page.click("[data-jl-range='90']")
        page.wait_for_selector("[data-jl-range='90'][aria-pressed='true']")
        self.assertIn("90", page.get_attribute(".mood-chart", "aria-label") or "")
        self.assertRegex(page.inner_text(".lookbacks"), r"\d\d:\d\d")
        if page.locator(".lookback-more").count():
            page.click(".lookback-more summary")
            self.assertTrue(page.evaluate("document.querySelector('.lookback-more').open"))
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()
