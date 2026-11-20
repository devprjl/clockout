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

**Pushing a draft:** `/clockout` always stops at a preview of exactly what each destination will get. Reply "send it" (or ask for edits: "make #42 2h", "drop the last row", "append, don't replace") and Claude runs the export. The draft is saved in `.cache/entries-<date>.json`, so you can also push it yourself with `scripts/export.py --entries .cache/entries-<date>.json`.

**Pasting:** click the first empty cell of the first column and press **⌘V** / **Ctrl+V**. Each entry becomes one row, and multi-line task lists stay in one cell.

## Layouts

Each column in a project's `layout.columns` has a `header` (exact text from the sheet) and a `value`:

| value | fills in |
|---|---|
| `person`, `date`, `task`, `hours`, `remarks` | the usual fields |
| `project` | the code project (e.g. `group/repo`) |
| `ticket` | first issue/MR ref of the entry, e.g. `#21`, `!58` |
| `work_package` | the work package the entry is logged on (from `OP#123` / `Ref: <name>`); empty = the destination's default bucket |
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
| `openproject` | logs **time entries** in OpenProject (API v3), one per entry | `url_env`, `token_env`, `project`, `activity`, `work_package` |

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

### OpenProject (`openproject`)
1. In OpenProject: avatar → **My account** → **Access tokens** → **API** → **+ API token**. Put it in `secrets.env` as `OPENPROJECT_TOKEN`, and the instance URL as `OPENPROJECT_URL`. These are set **once** for all your projects through `destination_defaults` in `config.yaml`:
   ```yaml
   destination_defaults:
     openproject: {url_env: OPENPROJECT_URL, token_env: OPENPROJECT_TOKEN}
   ```
   A project on a different server or account just sets its own `url_env`/`token_env` on its destination; those win.
2. In the project file, add the destination (see [`examples/projects/openproject.yaml`](examples/projects/openproject.yaml)): the project identifier, a default activity (Development, Testing…) and the work package. Use a fixed id, `from_refs` (below), or `ask` to have Claude ask per entry. If your team picks the activity per entry, add a layout column `{header: Activity, value: ask, default: Development, choices: [...]}`; each entry's answer overrides the default. On **OpenProject 17+, time must be logged on a work package**.
3. Lookups that work without admin rights:
   ```bash
   $PY $S/op.py work-packages --project <slug> --query search    # find a work package id
   $PY $S/op.py activities    --project <slug>                   # allowed activities
   $PY $S/op.py logged        --project <slug> --date 2026-11-16..2026-11-20   # what you already logged
   $PY $S/op.py delete        --project <slug> --id 8,9         # delete your own entries (e.g. duplicates)
   ```
4. `--preview` lists exactly what will be sent (date, duration, work package, activity, comment). `--dry-run` checks the server (project, permissions, entries you already logged) without writing. Re-running a day with entries already logged is blocked until you choose: `--append` adds next to them (deletes nothing, skips identical entries; handy for a second run later in the day), or `--replace-date` deletes and re-logs only **your own** entries for those dates. Teammates' entries are never touched.

#### Link commits to work packages
Mention the work package in a commit message, merge request title/description, or branch name, and clockout logs that work's time on it:

```
Apply member discount once

Ref: OP#42                     ← by id (also works anywhere in the text, or as branch fix/OP-42-…)
Ref: Export ledger as CSV      ← by name (matched against the project's work packages)
```
Set the destination to `work_package: from_refs` with `default_work_package: <id>` (a bucket such as *General development* for work without a reference) and the layout's `granularity: per_work_package`. Each day then becomes one time entry per work package. `OP#42` is also the syntax OpenProject's built-in **GitLab integration** uses: with its webhook configured, the MR and commits show up on the work package's GitLab tab. Override the patterns per project with `work_refs: {id_patterns: [...], name_patterns: [...]}`.

Your user needs the **Log own time** permission in the project (the default *Member* role has it). Admin rights aren't needed.

> **What about OpenProject's MCP server?** OpenProject 17 ships one, but it's an Enterprise feature and has no time-entry tools (work packages, projects, users, etc. only), so it can't log time. clockout writes time through the REST API instead, with preview, duplicate guard and replace. If your instance has the MCP enabled, Claude can still use it for lookups.

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
$PY $S/export.py --entries entries.json --preview   # exactly what each destination gets
$PY $S/export.py --entries entries.json --dry-run   # check against destinations, write nothing
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
scripts/sinks/         clipboard, tsv, gsheet_webapp, gsheet_api, openproject
scripts/op.py          OpenProject lookups (work packages, activities, logged time)
scripts/auth.py        Google OAuth (gsheet_api / Calendar only)
apps-script/Code.gs    sheet-side writer for gsheet_webapp
examples/              config.yaml, secrets.env, projects/*.yaml
```
