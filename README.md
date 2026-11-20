# clockout

**Run it when you clock out.** `clockout` is a [Claude Code](https://claude.com/claude-code) skill. It looks at what you actually did (commits, merge requests, issue activity and the meetings you told it about) and drafts your timesheet **in each project's own format**. You review it, and it goes to the clipboard, ready to paste, or straight into a Google Sheet.

```
/clockout            → today, for whichever projects you worked on
/clockout yesterday
/clockout 2026-11-04..2026-11-20
```

| Person | Date | Task Description | Hours Spent | Remarks |
|---|---|---|---|---|
| Alex | Nov 19 | - Login errors now show proper toast messages<br>- Added review summary filter and translations<br>- Added confirmation dialog for deleting users and tags | 6.5 | |

---

## ⚠️ Disclaimer: read this

- **It drafts; you decide.** clockout builds a starting point from your GitLab activity, local commits and the meetings you describe. It isn't a record of the time you worked.
- **It can't see everything.** Team discussions, pairing, reviews done outside GitLab, research, support, debugging that never became a commit, and work in tools or repos you didn't configure are all invisible to it. Add them yourself.
- **Hours are assumed.** Each task line comes from real activity, but the time is an *estimate* of the effort behind it: planning, prompting, thinking, testing, design curation. A light day stays light, and it won't pad a day to 8 hours.
- **Log your true hours.** You are responsible for what you submit. Correct the estimates, add what's missing, remove what's wrong, and keep your timesheet honest and transparent for your team.

---

## How it works

```
                          ~/.config/clockout/
                          ├─ config.yaml          you: name, timezone, workdays, hours, sources
                          ├─ secrets.env          tokens
                          └─ projects/*.yaml      per project: match rules, layout, destinations, meetings

GitLab API ─┐                              ┌─ project A ─ layout A ─► clipboard
git log ────┼─► collect ─► routed to ──────┤
meetings ───┘   (real work) projects       └─ project B ─ layout B ─► Google Sheet
                         │
                 Claude drafts short bullets + estimated hours ─► you approve/edit ─► export
```

- **Personal settings** (`config.yaml`) apply everywhere: your name, timezone, workdays, working hours, how many focused hours a day is realistic, and your GitLab instances.
- **Projects** (`projects/<slug>.yaml`) are set up once each. A project file holds:
  - which repos belong to it (by remote, GitLab path or folder),
  - the exact columns of that team's timesheet,
  - where the rows go,
  - its meetings,
  - anything that differs from your defaults (a different name spelling, workdays, style).
- On every run, the day's activity is **routed to the right project automatically**. If a day spans two projects, you get two drafts, each in its own layout and sent to its own destination. Activity from repos you haven't set up is flagged so you can assign it, set it up or ignore it. It's never silently dropped.

---

## Install

Requirements: [Claude Code](https://claude.com/claude-code), Python 3.10+, git.

```bash
git clone https://github.com/devprjl/clockout.git ~/code/github/clockout
cd ~/code/github/clockout
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
ln -s "$PWD" ~/.claude/skills/clockout
```

## Set up (once)

Open Claude Code in a project repo and run:

```
/clockout setup
```

1. **You** (first time only). Claude asks for your name (as written in timesheets), timezone, workdays and working hours. It also asks for your GitLab URL and pre-fills your commit emails from git.
   - Create a GitLab **personal access token with only the `read_api` scope**: avatar → *Edit profile* → *Access tokens*.
   - Paste it into `~/.config/clockout/secrets.env` yourself, not into the chat.
2. **This project**:
   - Claude proposes match rules from the repo's remote.
   - **Paste your timesheet's header row, or share a screenshot of the sheet.** Claude maps each column (date format, hours format, one row per day or one per task, constant columns, columns it should ask you about) and shows a couple of your real recent rows in that layout to confirm.
   - Choose a destination: clipboard (works with any sheet), or write directly into a Google Sheet (below).
   - Optionally describe your meetings: "DSM on alternate days from Oct 27, about 20 minutes".

Run `/clockout setup` in any other project repo to add it. If the timesheet's columns change, say *"our timesheet columns changed"* and paste the new header.

Everything is plain YAML in `~/.config/clockout/`, and you can edit it by hand. See [`examples/`](examples/) for fully commented files. Check your setup with:

```bash
~/.claude/skills/clockout/.venv/bin/python ~/.claude/skills/clockout/scripts/config.py validate
```

## Daily use

| You type | What happens |
|---|---|
| `/clockout` | Today. Asks once about anything it can't see (discussions, reviews), previews, then exports |
| `/clockout yesterday` / `/clockout 2026-11-18` | A specific day |
| `/clockout 2026-11-04..2026-11-20` | Catch up on a range (one preview for everything) |
| `/clockout just print` | Show tab-separated rows instead of exporting |
| "DSM is weekly from today, 1 hour" | Updates that project's meetings; past days keep the old schedule |
| "No DSM today" / "had a 1h discussion with design" | Records it for that day |

**Which days get rows:** non-workdays and holidays get nothing unless you worked. A workday with only meetings gets just the meetings. A workday with nothing gets a date-only row in your main project, so the gap is visible.

**Pasting:** click the first empty cell of the first column and press **⌘V** / **Ctrl+V**. Each entry becomes one row, and multi-line task lists stay in one cell.

## Layouts

Each column in a project's `layout.columns` has a `header` (exact text from the sheet) and a `value`:

| value | fills in |
|---|---|
| `person`, `date`, `task`, `hours`, `remarks` | the usual fields |
| `project` | the code project (e.g. `group/repo`) |
| `ticket` | first issue/MR ref of the entry, e.g. `#21`, `!58` |
| `refs` | links to the MRs and issues behind the entry (`format: lines` or `comma`) |
| `const:<text>` | the same text on every row |
| `ask` | Claude asks you (optional `default`, `choices`) |
| `blank` | left empty |

Formats:
- **Dates:** `MMM d` → Nov 4, `D MMM` → 4 Nov, `DD/MM/YYYY`, `YYYY-MM-DD`, `dddd` for the weekday.
- **Hours:** `number` → 6.5, `h:mm` → 6:30, `~number` → ~6.5, `minutes` → 390.
- **Granularity:** `per_day` puts the day's tasks as bullets in one cell. `per_task` gives each task its own row and hours.

## Destinations

| type | what | config |
|---|---|---|
| `clipboard` | copies rows as a table, pastes cleanly into Google Sheets/Excel | `with_header: true` to include headers |
| `tsv` | prints tab-separated rows | |
| `gsheet_webapp` | appends to **your own** Google Sheet through a small Apps Script; no Google Cloud, sheet stays private | `url_env`, `token_env`, `tab` |
| `gsheet_api` | appends via the Sheets API | `sheet_id`, `tab` |

Both Google Sheet destinations:
- check that the sheet's header row still matches the layout, and stop if it doesn't;
- only ever **append** below existing data;
- refuse to write if you already have rows on those dates. `--replace-date` replaces only **your** rows for those dates.

### Google Sheet via Apps Script (`gsheet_webapp`)
1. Generate a token with `python3 -c "import secrets;print(secrets.token_urlsafe(32))"` and add it to `secrets.env`, e.g. `MY_SHEET_TOKEN=...`.
2. In the sheet, open **Extensions → Apps Script**. Paste [`apps-script/Code.gs`](apps-script/Code.gs), set `TOKEN` to the same value, and save.
3. Click **Deploy → New deployment → Web app**. Set *Execute as* to **Me** and *Who has access* to **Anyone**, then authorise it. Add the URL to `secrets.env`, e.g. `MY_SHEET_URL=...`.
4. In the project file, add `- {type: gsheet_webapp, url_env: MY_SHEET_URL, token_env: MY_SHEET_TOKEN, tab: Timesheet}`. Set the sheet's sharing back to **Restricted**.

### Google Sheet via Sheets API (`gsheet_api`)
Enable the Sheets API in a Google Cloud project, then either:
- save a service-account key as `~/.config/clockout/google/service-account.json` and share the sheet with its email, or
- save an OAuth Desktop client as `~/.config/clockout/google/credentials.json` and run `scripts/auth.py sheets`.

## Sources

- **GitLab** (one or more instances): pushes, MRs opened/merged/approved, issues, comments and commit messages. You need a `read_api` token.
- **Local git** (`git log`): every repo in your projects' `match.paths`, plus `sources.git.repos`.
- **Meetings**: whatever you describe, stored per project.
- **Google Calendar** (optional, `sources.calendar.enabled`): only useful if your calendar exposes event titles. Run `scripts/auth.py calendar`.

## Without Claude
```bash
S=~/.claude/skills/clockout/scripts; PY=~/.claude/skills/clockout/.venv/bin/python
$PY $S/collect.py --date yesterday             # activity routed per project (JSON)
$PY $S/config.py detect                        # which project is this folder?
$PY $S/export.py --entries entries.json --preview
$PY $S/export.py --entries entries.json        # each project → its destinations
```

## Adding a destination
Destinations live in `scripts/sinks/`. Each one is a module with `DESCRIPTION`, `REQUIRED` (config keys) and `export(entries, dest, layout, opts) -> str`; `check(dest)` is optional. Add the module and list it in `scripts/sinks/__init__.py`. Entries carry `project`, `refs` and `fields` as well as the layout columns. Tools that file time against projects or work items can use those fields, and drafting and layouts stay the same.

## Layout
```
SKILL.md               what Claude follows (/clockout)
scripts/config.py      personal + project config, project detection, validation
scripts/collect.py     activity → routed per project (GitLab, git log, meetings, Calendar)
scripts/layout.py      entries → a project's columns and formats
scripts/export.py      entries → each project's destinations
scripts/sinks/         clipboard, tsv, gsheet_webapp, gsheet_api
scripts/auth.py        Google OAuth (gsheet_api / Calendar only)
apps-script/Code.gs    sheet-side writer for gsheet_webapp
examples/              config.yaml, secrets.env, projects/*.yaml
```
