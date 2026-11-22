"""Notion-style blocks (databases, the / menu) and the Projects ranking, in the real web app."""

import json
import os
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

DAY = "2026-11-10T00:00:00Z"


def repo(name, **extra):
    base = {
        "name": name,
        "full_name": f"juw12/{name}",
        "html_url": f"https://github.com/juw12/{name}",
        "owner": {"login": "juw12"},
        "description": "",
        "homepage": "",
        "has_pages": False,
        "license": None,
        "topics": [],
        "size": 0,
        "stargazers_count": 0,
        "forks_count": 0,
        "fork": False,
        "archived": False,
        "created_at": "2026-01-01T00:00:00Z",
        "pushed_at": "2026-01-01T00:00:00Z",
        "language": "JavaScript",
    }
    base.update(extra)
    return base


REPOS = [
    # Polished, used and still moving: should lead.
    repo(
        "cairn",
        description="A second brain that lives inside your repo.",
        homepage="https://juw12.github.io/cairn/",
        license={"key": "mit"},
        topics=["obsidian", "notes", "ai"],
        size=4000,
        stargazers_count=40,
        forks_count=3,
        created_at="2026-06-01T00:00:00Z",
        pushed_at="2026-11-09T00:00:00Z",
    ),
    # Brand new and hot, little polish yet: high potential.
    repo("rocket", description="Fast new idea", size=300, stargazers_count=12, created_at="2026-10-20T00:00:00Z", pushed_at="2026-11-09T00:00:00Z"),
    # Old and untouched.
    repo("old-thing", description="Something from last year", size=900, created_at="2024-01-01T00:00:00Z", pushed_at="2025-01-01T00:00:00Z"),
    # Never ranked: a fork and an archived repo.
    repo("someone-elses", fork=True, size=999, stargazers_count=500),
    repo("retired", archived=True, size=999, stargazers_count=500),
    # Settings, not projects: the profile README repo and .github.
    repo("juw12", description="Profile README", size=50, pushed_at="2026-11-09T00:00:00Z"),
    repo(".github", description="Default community files", size=10, pushed_at="2026-11-09T00:00:00Z"),
]

ROOTS = {
    "cairn": ["README.md", "tests", ".github", "pyproject.toml", "LICENSE"],
    "rocket": ["README.md", "index.html"],
    "old-thing": ["README.md"],
}


@unittest.skipIf(sync_playwright is None, "playwright not installed")
class NotionAndProjectsTests(unittest.TestCase):
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

    def open(self, query="?demo=1", wait="#nav button", repos=REPOS):
        context = self.browser.new_context(viewport={"width": 1440, "height": 900})
        install_github_mock(context)
        calls = []

        def github(route):
            url = route.request.url
            calls.append(url)
            path = url.split("api.github.com", 1)[1].split("?")[0]
            headers = {"Access-Control-Allow-Origin": "*", "Content-Type": "application/json"}
            if path == "/users/juw12/repos":
                return route.fulfill(status=200, headers=headers, body=json.dumps(repos))
            if path.startswith("/repos/juw12/") and path.endswith("/contents/"):
                name = path.split("/")[3]
                return route.fulfill(status=200, headers=headers, body=json.dumps([{"name": n} for n in ROOTS.get(name, [])]))
            return route.fallback()

        context.route("https://api.github.com/**", github)
        context.set_default_timeout(90_000)
        page = context.new_page()
        self.errors = []
        page.on("console", lambda m: self.errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: self.errors.append(str(e)))
        page.add_init_script("localStorage.setItem('cairn.theme', 'light'); localStorage.setItem('cairn.projects.owner', '\"juw12\"')")
        # The app is ready once the nav renders; don't wait for every font and image (slow CI machines).
        page.goto(self.url + query, wait_until="domcontentloaded", timeout=120_000)
        page.wait_for_selector(wait)
        self.addCleanup(context.close)
        self.calls = calls
        return page

    # ------------------------------------------------------------------ pure logic

    def test_scores_rank_solid_and_promising_repos_and_keep_cairn(self):
        page = self.open()
        result = page.evaluate(
            """async ([repos, roots, now]) => {
                const P = await import('./projects.js');
                const facts = Object.fromEntries(Object.entries(roots).map(([n, names]) => [`juw12/${n}`, P.rootFacts(names)]));
                const at = Date.parse(now);
                const order = (sort) => P.rankProjects(repos, { sort, now: at, facts }).map((p) => p.repo.name);
                const ranked = P.rankProjects(repos, { now: at, facts });
                const byName = Object.fromEntries(ranked.map((p) => [p.repo.name, p.score]));
                return {
                    overall: order('overall'),
                    potential: order('potential'),
                    tiers: Object.fromEntries(ranked.map((p) => [p.repo.name, p.score.tier])),
                    cairn: byName.cairn,
                    oldNext: byName['old-thing'].nextSteps,
                    mentions: P.brainMentions([
                        { title: 'Trail mix plan', slug: 'x', tags: [], body: '' },
                        { title: 'Other', slug: 'y', tags: [], body: 'see trail-mix and more' },
                        { title: 'Trailmixer', slug: 'z', tags: [], body: '' },
                    ], 'trail-mix'),
                    moves: P.rankMoves({ 'juw12/cairn': 2, 'juw12/rocket': 1 }, ranked),
                    md: P.rankingMarkdown('juw12', ranked, '2026-11-10'),
                    // Five near-identical repos that differ only in size still get distinct, ordered scores.
                    spread: P.rankProjects([1, 2, 3, 4, 5].map((i) => ({
                        ...repos[1], name: `twin${i}`, full_name: `juw12/twin${i}`, size: 200 * i,
                    })), { now: at }).map((p) => [p.repo.name, p.score.overall]),
                };
            }""",
            [REPOS, ROOTS, DAY],
        )
        # Forks and archived repos are left out; Cairn leads overall; the hot new repo leads on potential.
        self.assertEqual(result["overall"], ["cairn", "rocket", "old-thing"])
        self.assertEqual(result["potential"][0], "cairn")
        self.assertEqual(result["tiers"]["old-thing"], "Dormant")
        self.assertEqual(result["tiers"]["cairn"], "Flagship")
        self.assertGreater(result["cairn"]["solid"], 80)
        # "Why" leads with what sets Cairn apart from the other repos (only it has tests; it has by far the
        # most stars), ahead of what most repos share (a recent push).
        self.assertEqual(result["cairn"]["strengths"], ["has tests", "40 stars", "pushed 1d ago"])
        # Similar repos spread out by how they compare with each other: bigger codebase, higher score.
        names = [name for name, _ in result["spread"]]
        scores = [score for _, score in result["spread"]]
        self.assertEqual(names, ["twin5", "twin4", "twin3", "twin2", "twin1"])
        self.assertEqual(len(set(scores)), 5, result["spread"])
        # Next steps are things you can do, biggest gain first: a dormant repo needs a push, then tests.
        self.assertTrue(result["oldNext"][0].startswith("Push an update"), result["oldNext"])
        self.assertEqual(result["oldNext"][1], "Add tests")
        self.assertEqual(result["mentions"], 2)
        self.assertEqual(result["moves"]["juw12/cairn"], 1)
        self.assertEqual(result["moves"]["juw12/rocket"], -1)
        self.assertIn("| 1 | [cairn](https://github.com/juw12/cairn) (Cairn) | Flagship |", result["md"])

    def test_database_query_language(self):
        page = self.open()
        result = page.evaluate(
            """async () => {
                const D = await import('./database.js');
                const core = window.cairn.core;
                const notes = [
                    core.noteFromFile('tasks/a.md', '---\\ntitle: Alpha\\ntype: task\\nstatus: doing\\ntags: [release]\\ndue: 2026-11-12\\npoints: 3\\n---\\nbody', 3),
                    core.noteFromFile('tasks/b.md', '---\\ntitle: Beta\\ntype: task\\nstatus: todo\\ntags: [release, web]\\ndue: 2026-11-02\\npoints: 8\\n---\\n', 2),
                    core.noteFromFile('tasks/c.md', '---\\ntitle: Gamma\\ntype: task\\nstatus: done\\ntags: []\\n---\\n', 1),
                    core.noteFromFile('notes/d.md', '---\\ntitle: Delta\\n---\\nnot a task', 4),
                ];
                const spec = D.parseDatabase('title: Release\\nfrom: tasks\\ntag: release\\nwhere: status != done\\nwhere: points >= 3\\nsort: due asc\\nview: board\\ncolumns: status, due');
                const rows = D.runDatabase(notes, spec).rows.map((n) => n.title);
                const groups = D.groupRows(D.runDatabase(notes, D.parseDatabase('type: task')).rows, 'status', { type: ['task'] })
                    .map((g) => [g.value, g.rows.length]);
                const body = 'Intro\\n\\n```database\\ntype: task\\nview: table\\n```\\n\\nOutro';
                const weeks = D.calendarWeeks(notes, 'due', 2026, 10);
                return {
                    spec: { title: spec.title, view: spec.view, from: spec.from, where: spec.where, columns: spec.columns },
                    rows,
                    numeric: D.runDatabase(notes, D.parseDatabase('where: points > 5')).rows.map((n) => n.title),
                    has: D.runDatabase(notes, D.parseDatabase('where: has due')).rows.length,
                    groups,
                    defaults: D.defaultsFor(D.parseDatabase('type: task\\ntag: release\\nwhere: status = doing\\nwhere: owner = sam')),
                    setView: D.setBlockOption(body, 'type: task\\nview: table', 'view', 'board'),
                    addOption: D.setBlockOption(body, 'type: task\\nview: table', 'group', 'tags'),
                    missing: D.setBlockOption(body, 'type: idea', 'view', 'board'),
                    calendarFirst: weeks[0][0].date,
                    // Relative dates resolve against "now"; the first operator in the line wins.
                    overdue: D.runDatabase(notes, D.parseDatabase('where: due < today')).rows.map((n) => n.title),
                    soon: D.resolveDate('today+7', new Date(2026, 10, 10)),
                    yesterday: D.resolveDate('yesterday', new Date(2026, 10, 1)),
                    plain: D.resolveDate('2026-01-01'),
                    firstOp: D.parseCondition('title contains a=b'),
                    onTwelfth: weeks.flat().find((d) => d.date === '2026-11-12').rows.map((n) => n.title),
                };
            }"""
        )
        self.assertEqual(result["soon"], "2026-11-17")
        self.assertEqual(result["yesterday"], "2026-10-31")
        self.assertEqual(result["plain"], "2026-01-01")
        self.assertEqual(result["firstOp"], {"key": "title", "op": "contains", "value": "a=b"})
        # Both dated tasks are due before the real today (2026-11-12 and 2026-11-02 are in the past by CI time
        # only if today is later), so compare with a date computed the same way in Python.
        import datetime
        today = datetime.date.today().isoformat()
        expected_overdue = sorted(t for t, due in (("Alpha", "2026-11-12"), ("Beta", "2026-11-02")) if due < today)
        self.assertEqual(sorted(result["overdue"]), expected_overdue)
        self.assertEqual(result["spec"]["view"], "board")
        self.assertEqual(result["spec"]["from"], "tasks")
        self.assertEqual(result["spec"]["columns"], ["status", "due"])
        self.assertEqual(result["spec"]["where"][0], {"key": "status", "op": "!=", "value": "done"})
        self.assertEqual(result["rows"], ["Beta", "Alpha"])  # sorted by due, done and low points filtered out
        self.assertEqual(result["numeric"], ["Beta"])
        self.assertEqual(result["has"], 2)
        self.assertEqual(result["groups"], [["todo", 1], ["doing", 1], ["done", 1]])
        self.assertEqual(result["defaults"], {"type": "task", "tags": ["release"], "status": "doing", "owner": "sam"})
        self.assertEqual(result["setView"], "Intro\n\n```database\ntype: task\nview: board\n```\n\nOutro")
        self.assertEqual(result["addOption"], "Intro\n\n```database\ntype: task\nview: table\ngroup: tags\n```\n\nOutro")
        self.assertIsNone(result["missing"])
        self.assertEqual(result["calendarFirst"], "2026-10-26")  # November 2026 starts on a Sunday; weeks start Monday
        self.assertEqual(result["onTwelfth"], ["Alpha"])

    def test_slash_menu_logic(self):
        page = self.open()
        result = page.evaluate(
            """async () => {
                const S = await import('./slash.js');
                const pick = (id) => S.SLASH_COMMANDS.find((c) => c.id === id);
                return {
                    start: S.slashQuery('/hea', 4),
                    afterSpace: S.slashQuery('hello /to', 9),
                    url: S.slashQuery('see a/b', 7),
                    inCode: S.slashQuery('```\\n/x', 6),
                    heading: S.slashMatches('head').map((c) => c.id).slice(0, 3),
                    board: S.slashMatches('kanban').map((c) => c.id),
                    insertTodo: S.slashInsert(pick('todo'), ''),
                    insertBlockMidLine: S.slashInsert(pick('h2'), 'some text '),
                    insertInline: S.slashInsert(pick('highlight'), 'some text '),
                };
            }"""
        )
        self.assertEqual(result["start"], {"start": 0, "end": 4, "query": "hea"})
        self.assertEqual(result["afterSpace"]["query"], "to")
        self.assertIsNone(result["url"])
        self.assertIsNone(result["inCode"])
        self.assertEqual(result["heading"], ["h1", "h2", "h3"])
        self.assertEqual(result["board"], ["board"])
        self.assertEqual(result["insertTodo"], {"text": "- [ ] ", "caret": 6})
        self.assertEqual(result["insertBlockMidLine"], {"text": "\n## ", "caret": 4})
        self.assertEqual(result["insertInline"], {"text": "====", "caret": 2})

    # ------------------------------------------------------------------ in the app

    def test_projects_view_ranks_repos_and_saves_a_note(self):
        page = self.open()
        # Projects is a tab of the Build hub in the sidebar.
        page.click('#nav [data-hub="build"]')
        page.click('.hub-tabs [data-go="projects"]')
        page.wait_for_selector(".proj-card")
        names = page.eval_on_selector_all(".proj-card .proj-title a", "els => els.map((e) => e.textContent)")
        self.assertEqual(names[0], "cairn")
        self.assertEqual(set(names), {"cairn", "rocket", "old-thing"})
        self.assertEqual(page.locator(".proj-card.self .proj-self").count(), 1)
        # First ranking ever: nothing to compare with, so no "new"/up/down badges.
        self.assertEqual(page.locator(".proj-move").count(), 0)
        self.assertIn("Focus pick", page.inner_text(".proj-lead"))
        # Re-sorting by potential is instant (no new GitHub calls).
        before = len(self.calls)
        page.click('[data-action="projects-sort"][data-sort="potential"]')
        self.assertEqual(page.get_attribute('[data-sort="potential"]', "aria-pressed"), "true")
        self.assertEqual(len(self.calls), before)
        # Save the ranking as a plain Markdown note in the brain.
        page.click('[data-action="projects-note"]')
        page.wait_for_function("() => window.cairn.state.notes.some((n) => n.meta?.cairn === 'project-ranking')")
        body = page.evaluate("() => window.cairn.state.notes.find((n) => n.meta?.cairn === 'project-ranking').body")
        self.assertIn("| # | Project | Tier | Overall | Solid | Potential | Next step |", body)
        self.assertIn("[cairn](https://github.com/juw12/cairn)", body)
        self.assertEqual(self.errors, [])

    def test_projects_page_is_blocks_with_a_dashboard_and_a_recap(self):
        page = self.open()
        page.click('#nav [data-hub="build"]')
        page.click('.hub-tabs [data-go="projects"]')
        page.wait_for_selector(".proj-card")
        board = '.vibe-board[data-page="projects"]'
        order = lambda: page.eval_on_selector_all(f"{board} .vibe-panel", "els => els.map((e) => e.dataset.panel)")
        folded = lambda: page.eval_on_selector_all(f"{board} .vibe-panel.folded", "els => els.map((e) => e.dataset.panel)")
        self.assertEqual(order(), ["dashboard", "recap", "ranking", "scoring"])
        self.assertEqual(folded(), ["scoring"], "the explanation of the score starts folded")
        # Dashboard: the numbers of the ranking. Recap: the focus pick first.
        tiles = page.eval_on_selector_all(f'{board} [data-panel="dashboard"] .figure', "els => els.map((e) => e.innerText.replace(/\\s+/g, ' ').trim())")
        self.assertTrue(tiles[0].startswith("3 "), tiles)
        self.assertIn("Focus pick", page.inner_text(f'{board} [data-panel="recap"]'))

        # Fold the ranking to one line, then open it again.
        page.click(f'{board} .vp-toggle[data-panel="ranking"]')
        page.wait_for_selector(f'{board} .vibe-panel.folded[data-panel="ranking"]')
        self.assertEqual(page.locator(".proj-card").count(), 0)
        self.assertIn("3", page.inner_text(f'{board} [data-panel="ranking"] .vp-sum'))
        page.click(f'{board} .vp-toggle[data-panel="ranking"]')
        page.wait_for_selector(".proj-card")

        # Move the recap above the dashboard; the brain file keeps one pair of lines per page.
        page.click(f'{board} .vp-move[data-panel="recap"][data-step="-1"]')
        page.wait_for_function("() => document.querySelector('.vibe-board[data-page=projects] .vibe-panel').dataset.panel === 'recap'")
        page.wait_for_function("() => /^projects-order: recap, dashboard/m.test(window.cairn.state.files.find((f) => f.path === 'CAIRN-LAYOUT.md')?.text || '')")
        layout = page.evaluate("() => window.cairn.state.files.find((f) => f.path === 'CAIRN-LAYOUT.md').text")
        self.assertRegex(layout, r"(?m)^projects-folded: scoring$")
        self.assertNotRegex(layout, r"(?m)^vibe-order:", "a page nobody arranged gets no line")

        # Reset puts the default order back for this page.
        page.click(f'[data-action="vibe-layout-reset"][data-page="projects"]')
        page.wait_for_function("() => document.querySelector('.vibe-board[data-page=projects] .vibe-panel').dataset.panel === 'dashboard'")
        self.assertEqual(order(), ["dashboard", "recap", "ranking", "scoring"])
        self.assertEqual(self.errors, [])

    # ------------------------------------------------------------------ categories, A to Z, the filter, hidden blocks

    def test_categories_come_from_topics_name_and_description(self):
        page = self.open()
        result = page.evaluate(
            """async ([repos, now]) => {
                const P = await import('./projects.js');
                const cat = (repo) => P.categoryOf(repo).id;
                const at = Date.parse(now);
                const mixed = [
                    { ...repos[0], name: 'Zeta', full_name: 'juw12/Zeta', topics: ['game'], private: true },
                    { ...repos[1], name: 'alpha', full_name: 'juw12/alpha', topics: ['llm'], description: 'An agent in Rust', language: 'Rust' },
                    { ...repos[1], name: 'Beta', full_name: 'juw12/Beta', topics: ['cozy-game'] },
                    { ...repos[1], name: 'gamma10', full_name: 'juw12/gamma10', topics: [] },
                    { ...repos[1], name: 'gamma2', full_name: 'juw12/gamma2', topics: [] },
                ];
                const ranked = P.rankProjects(mixed, { now: at, keep: 'nobody/nothing' });
                const names = (options) => P.arrangeProjects(ranked, options).groups.map((g) => [g.label, g.projects.map((p) => p.repo.name)]);
                const az = P.arrangeProjects(ranked, { sort: 'name' }).groups[0].projects;
                return {
                    topic: cat({ name: 'x', topics: ['unity'], description: 'A second brain in markdown' }),
                    wholeTopic: cat({ name: 'x', topics: ['cozy-game'] }),
                    name: cat({ name: 'teduh-signing', topics: [] }),
                    described: cat({ name: 'x', description: 'Family ledger for the phone' }),
                    builtAs: cat({ name: 'x', topics: ['pwa', 'offline-first'] }),
                    aboutBeatsBuiltAs: cat({ name: 'x', topics: ['pwa', 'offline-first', 'capacitor', 'game'] }),
                    notThat: cat({ name: 'x', topics: ['no-ai', 'pwa'] }),
                    unknown: P.categoryOf({ name: 'x', topics: ['zzz'], description: 'Fast new idea' }),
                    why: P.categoryOf({ name: 'tolstack', topics: ['cad', 'gd-t'], description: '' }),
                    labels: Object.values(P.CATEGORY_LABELS),
                    ranks: ranked.map((p) => p.rank),
                    az: az.map((p) => p.repo.name),
                    azKeepsRanks: az.every((p) => p.rank === ranked.find((r) => r.repo.name === p.repo.name).rank),
                    grouped: names({ sort: 'name', group: true }),
                    games: names({ filter: { categories: ['games'] } }),
                    gamesOrAi: P.arrangeProjects(ranked, { filter: { categories: ['games', 'ai'] } }).shown,
                    privateGames: names({ sort: 'name', filter: { categories: ['games'], shown: ['private'] } }),
                    text: names({ sort: 'name', filter: { text: 'rust agent' } }),
                    nothing: P.arrangeProjects(ranked, { filter: { text: 'no such word' } }),
                    counts: P.filterCounts(ranked),
                };
            }""",
            [REPOS, DAY],
        )
        # A topic counts more than the description; a whole topic and a word of it both count.
        self.assertEqual(result["topic"], "games")
        self.assertEqual(result["wholeTopic"], "games")
        self.assertEqual(result["name"], "keep")
        self.assertEqual(result["described"], "money")
        # How a project is built only decides when nothing says what it is about.
        self.assertEqual(result["builtAs"], "apps")
        self.assertEqual(result["aboutBeatsBuiltAs"], "games")
        self.assertEqual(result["notThat"], "apps", "no-ai is not an AI project")
        self.assertEqual(result["unknown"], {"id": "other", "label": "Other", "why": []})
        self.assertEqual(result["why"], {"id": "science", "label": "Engineering and science", "why": ["cad", "gd-t"]})
        self.assertEqual(len(result["labels"]), len(set(result["labels"])), "every category has its own name")
        # A to Z ignores capitals, reads numbers as numbers and keeps each project's place in the ranking.
        self.assertEqual(result["ranks"], [1, 2, 3, 4, 5])
        self.assertEqual(result["az"], ["alpha", "Beta", "gamma2", "gamma10", "Zeta"])
        self.assertTrue(result["azKeepsRanks"])
        # Groups are in A to Z order with Other last.
        self.assertEqual(result["grouped"], [["AI and agents", ["alpha"]], ["Games", ["Beta", "Zeta"]], ["Other", ["gamma2", "gamma10"]]])
        # Chips of one row add up; rows narrow each other; every typed word must be found.
        self.assertEqual(sorted(result["games"][0][1]), ["Beta", "Zeta"])
        self.assertEqual(result["gamesOrAi"], 3)
        self.assertEqual(result["privateGames"], [["", ["Zeta"]]])
        self.assertEqual(result["text"], [["", ["alpha"]]])
        self.assertEqual(result["nothing"]["shown"], 0)
        self.assertEqual(result["nothing"]["total"], 5)
        self.assertEqual(result["counts"]["categories"], [["ai", 1], ["games", 2], ["other", 2]])
        self.assertEqual(result["counts"]["shown"], [["public", 4], ["private", 1]])

    def test_projects_list_a_to_z_by_category_and_filtered(self):
        page = self.open()
        page.click('#nav [data-hub="build"]')
        page.click('.hub-tabs [data-go="projects"]')
        page.wait_for_selector(".proj-card")
        names = lambda: page.eval_on_selector_all(".proj-card .proj-title a", "els => els.map((e) => e.textContent)")
        ranks = lambda: page.eval_on_selector_all(".proj-card .proj-rank > span", "els => els.map((e) => e.textContent)")
        self.assertEqual(names(), ["cairn", "rocket", "old-thing"])
        # Each card says its category, and why.
        pill = page.locator('.proj-card[data-project="juw12/cairn"] .proj-cat')
        self.assertEqual(pill.inner_text(), "Notes and learning")
        self.assertIn("obsidian, notes", pill.get_attribute("title"))

        # A to Z: by name, with the places of the overall ranking as the numbers. No new GitHub calls.
        before = len(self.calls)
        page.click('[data-action="projects-sort"][data-sort="name"]')
        self.assertEqual(names(), ["cairn", "old-thing", "rocket"])
        self.assertEqual(ranks(), ["1", "3", "2"])
        self.assertIn("Focus pick: cairn", page.inner_text(".proj-lead"), "the recap still follows the ranking")

        # By category: one folding group each, A to Z, Other last. The heading replaces the pill on the cards.
        page.click('[data-action="projects-group"][data-group="1"]')
        groups = lambda: page.eval_on_selector_all(".proj-group", "els => els.map((e) => [e.dataset.projGroup, e.querySelector('.count').textContent, e.open])")
        self.assertEqual(groups(), [["notes", "1", True], ["other", "2", True]])
        self.assertEqual(page.locator(".proj-cat").count(), 0)
        self.assertEqual(names(), ["cairn", "old-thing", "rocket"])
        # A category folded here stays folded when the page is drawn again.
        page.click('.proj-group[data-proj-group="other"] > summary')
        page.wait_for_function("() => (localStorage.getItem('cairn.projects.closedGroups') || '').includes('other')")
        page.click('[data-action="projects-sort"][data-sort="overall"]')
        self.assertEqual(groups(), [["notes", "1", True], ["other", "2", False]])
        page.click('.proj-group[data-proj-group="other"] > summary')

        # Filter by category: a chip per category with its count.
        chips = page.eval_on_selector_all('[data-action="projects-filter"][data-kind="categories"]', "els => els.map((e) => e.innerText.replace(/\\s+/g, ' ').trim())")
        self.assertEqual(chips, ["Notes and learning 1", "Other 2"])
        page.click('[data-action="projects-filter"][data-value="other"]')
        self.assertEqual(names(), ["rocket", "old-thing"])
        self.assertEqual(page.inner_text(".proj-filter-count"), "Showing 2 of 3")
        self.assertEqual(page.get_attribute('[data-action="projects-filter"][data-value="other"]', "aria-pressed"), "true")
        # A second row narrows the first.
        page.click('[data-action="projects-filter"][data-kind="tiers"][data-value="Dormant"]')
        self.assertEqual(names(), ["old-thing"])
        # The numbers above still count every project.
        self.assertTrue(page.inner_text('[data-page="projects"][data-panel="dashboard"] .figure').startswith("3"))
        # The choice is kept when the page is opened again.
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector(".proj-card")
        self.assertEqual(names(), ["old-thing"])
        page.click('[data-action="projects-filter-clear"]')
        self.assertEqual(len(names()), 3)
        self.assertEqual(page.locator(".proj-filter-count").count(), 0)

        # Typing narrows as you type and the box keeps the caret.
        page.click('[data-action="projects-group"][data-group=""]')
        page.click("#projects-filter")
        page.keyboard.type("rock")
        self.assertEqual(names(), ["rocket"])
        self.assertEqual(page.evaluate("() => [document.activeElement.id, document.activeElement.selectionStart]"), ["projects-filter", 4])
        page.keyboard.type("zz")
        self.assertEqual(page.locator(".proj-card").count(), 0)
        self.assertIn("Nothing matches the filter", page.inner_text('[data-page="projects"][data-panel="ranking"]'))
        page.click('.empty-state [data-action="projects-filter-clear"]')
        self.assertEqual(len(names()), 3)
        self.assertEqual(len(self.calls), before, "ordering, grouping and filtering ask GitHub nothing")
        self.assertEqual(self.errors, [])

    def test_a_block_can_be_hidden_and_shown_again(self):
        page = self.open()
        page.click('#nav [data-hub="build"]')
        page.click('.hub-tabs [data-go="projects"]')
        page.wait_for_selector(".proj-card")
        board = '.vibe-board[data-page="projects"]'
        hidden_line = '.vibe-hidden[data-page="projects"]'
        order = lambda: page.eval_on_selector_all(f"{board} .vibe-panel", "els => els.map((e) => e.dataset.panel)")
        layout = lambda: page.evaluate("() => window.cairn.state.files.find((f) => f.path === 'CAIRN-LAYOUT.md')?.text || ''")
        chips = lambda: page.eval_on_selector_all(f"{hidden_line} .chip", "els => els.map((e) => e.textContent.replace(/\\s+/g, ' ').trim())")
        self.assertEqual(page.locator(hidden_line).count(), 0, "nothing hidden: no line")
        # Every block has a Hide button.
        self.assertEqual(page.locator(f"{board} .vp-hide").count(), 4)

        page.click(f'{board} .vp-hide[data-panel="dashboard"]')
        self.assertEqual(order(), ["recap", "ranking", "scoring"])
        self.assertEqual(chips(), ["Dashboard +"])
        self.assertEqual(page.evaluate("() => document.activeElement.dataset.panel"), "dashboard", "focus goes to the chip that brings it back")
        page.wait_for_function("() => /^projects-hidden: dashboard$/m.test(window.cairn.state.files.find((f) => f.path === 'CAIRN-LAYOUT.md')?.text || '')")
        self.assertRegex(layout(), r"(?m)^projects-order: dashboard, recap, ranking, scoring$", "a hidden block keeps its place")
        self.assertNotRegex(layout(), r"(?m)^vibe-hidden:", "a page with nothing hidden gets no such line")

        # A second one joins the line. Another page is untouched.
        page.click(f'{board} .vp-hide[data-panel="scoring"]')
        self.assertEqual(order(), ["recap", "ranking"])
        self.assertEqual(chips(), ["Dashboard +", "How the score works +"])
        page.click('.hub-tabs [data-go="vibe"]')
        page.wait_for_selector('.vibe-board[data-page="vibe"] .vibe-panel')
        self.assertEqual(page.locator('.vibe-hidden[data-page="vibe"]').count(), 0)
        page.click('.hub-tabs [data-go="projects"]')
        page.wait_for_selector(".proj-card")

        # Hidden blocks stay hidden after a reload (the brain file carries it).
        page.wait_for_function("() => /^projects-hidden: dashboard, scoring$/m.test(window.cairn.state.files.find((f) => f.path === 'CAIRN-LAYOUT.md')?.text || '')")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector(".proj-card")
        self.assertEqual(order(), ["recap", "ranking"])

        # One press shows a block again, in the place it had.
        page.click(f'{hidden_line} [data-action="vibe-panel-show"][data-panel="dashboard"]')
        self.assertEqual(order(), ["dashboard", "recap", "ranking"])
        page.wait_for_function("() => /^projects-hidden: scoring$/m.test(window.cairn.state.files.find((f) => f.path === 'CAIRN-LAYOUT.md')?.text || '')")
        # Show all, and Reset layout, bring everything back.
        page.click(f'{board} .vp-hide[data-panel="recap"]')
        page.click(f'{hidden_line} [data-action="vibe-panel-show-all"]')
        self.assertEqual(order(), ["dashboard", "recap", "ranking", "scoring"])
        self.assertEqual(page.locator(hidden_line).count(), 0)
        page.click(f'{board} .vp-hide[data-panel="ranking"]')
        self.assertEqual(page.locator(".proj-card").count(), 0)
        page.click('[data-action="vibe-layout-reset"][data-page="projects"]')
        page.wait_for_selector(".proj-card")
        self.assertEqual(order(), ["dashboard", "recap", "ranking", "scoring"])
        page.wait_for_function("() => !/^projects-hidden:/m.test(window.cairn.state.files.find((f) => f.path === 'CAIRN-LAYOUT.md')?.text || '')")
        self.assertEqual(self.errors, [])

    def test_layout_file_reads_and_keeps_hidden_lines(self):
        page = self.open()
        result = page.evaluate(
            """async () => {
                const L = await import('./vibelayout.js');
                const text = L.layoutText({
                    vibe: { order: ['recap', 'overview'], folded: ['hook'], hidden: ['models', 'skills'] },
                    board: { order: ['dashboard', 'recap', 'cards'], folded: [] },
                }, 'future-order: a, b\\nfuture-hidden: b\\n');
                return {
                    text,
                    vibe: L.parseLayout(text, 'vibe'),
                    board: L.parseLayout(text, 'board'),
                    old: L.parseLayout('vibe-order: recap\\nvibe-folded: hook\\n', 'vibe'),
                    onlyHidden: L.parseLayout('proof-hidden: publish, nonsense\\n', 'proof'),
                };
            }"""
        )
        self.assertIn("vibe-hidden: models, skills\n", result["text"])
        self.assertNotIn("board-hidden", result["text"])
        self.assertIn("future-order: a, b\nfuture-hidden: b", result["text"], "lines of a page this build does not know are carried over")
        self.assertEqual(result["vibe"], {"order": ["recap", "overview"], "folded": ["hook"], "hidden": ["models", "skills"]})
        self.assertEqual(result["board"]["hidden"], [])
        self.assertEqual(result["old"], {"order": ["recap"], "folded": ["hook"], "hidden": []}, "a file written by an older build still reads")
        self.assertEqual(result["onlyHidden"], {"order": [], "folded": [], "hidden": ["publish"]})

    def test_database_block_renders_edits_and_switches_view(self):
        page = self.open()
        page.evaluate(
            """async () => {
                const { createNote, state } = await import('./state.js');
                const { openNote } = await import('./drawer.js');
                const note = await createNote({
                    type: 'note',
                    title: 'Release hub',
                    body: 'Open work:\\n\\n```database\\ntitle: Open tasks\\ntype: task\\nwhere: status != done\\ncolumns: status, tags\\nview: table\\n```\\n',
                });
                state.editorMode = 'preview';
                openNote(note.path);
            }"""
        )
        page.wait_for_selector(".db .db-table tbody tr")
        open_tasks = page.evaluate("() => window.cairn.state.notes.filter((n) => n.type === 'task' && n.status !== 'done').length")
        self.assertEqual(page.locator(".db .db-table tbody tr").count(), open_tasks)
        # Edit a status straight from the table: the task's front matter changes.
        first = page.get_attribute(".db .db-table tbody tr:first-child td[data-db-edit='status']", "data-path")
        page.click(".db .db-table tbody tr:first-child td[data-db-edit='status']")
        page.select_option(".db .db-input", "done")
        page.wait_for_function("(p) => window.cairn.state.notes.find((n) => n.path === p).status === 'done'", arg=first)
        self.assertIn("status: done", page.evaluate("(p) => window.cairn.state.files.find((f) => f.path === p).text", first))
        self.assertEqual(page.locator(".db .db-table tbody tr").count(), open_tasks - 1)
        # Switch to the board: the choice is written into the note's block.
        page.click('.db [data-db-view="board"]')
        page.wait_for_selector(".db .db-board .db-col")
        page.wait_for_function("() => window.cairn.state.notes.find((n) => n.title === 'Release hub').body.includes('view: board')")
        self.assertGreater(page.locator(".db .db-col").count(), 1)
        page.click('.db [data-db-view="calendar"]')
        page.wait_for_selector(".db .db-cal-grid")
        page.click('.db [data-db-view="gallery"]')
        page.wait_for_selector(".db .db-gallery")
        self.assertEqual(self.errors, [])

    def test_slash_menu_inserts_blocks_in_the_editor(self):
        page = self.open()
        page.evaluate(
            """async () => {
                const { createNote, state } = await import('./state.js');
                const { openNote } = await import('./drawer.js');
                const note = await createNote({ type: 'note', title: 'Slash test', body: '' });
                state.editorMode = 'write';
                openNote(note.path);
            }"""
        )
        page.wait_for_selector("#ed-body")
        page.click("#ed-body")
        page.keyboard.type("/to")
        page.wait_for_selector(".slash-menu .suggest-item")
        self.assertIn("To-do", page.inner_text(".slash-menu .suggest-item.on"))
        page.keyboard.press("Enter")
        page.keyboard.type("ship it")
        self.assertEqual(page.input_value("#ed-body"), "- [ ] ship it")
        self.assertEqual(page.locator(".slash-menu").count(), 0)
        page.keyboard.press("Enter")  # list continues, like Obsidian
        page.keyboard.press("Enter")  # empty item ends the list
        page.keyboard.type("/kanban")
        page.wait_for_selector(".slash-menu .suggest-item")
        page.keyboard.press("Enter")
        value = page.input_value("#ed-body")
        self.assertIn("```database\ntitle: Task board\ntype: task\ngroup: status\nview: board\n```", value)
        # Escape closes the menu without inserting anything.
        page.keyboard.type("/h")
        page.wait_for_selector(".slash-menu")
        page.keyboard.press("Escape")
        self.assertEqual(page.locator(".slash-menu").count(), 0)
        self.assertTrue(page.input_value("#ed-body").endswith("/h"))
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()
