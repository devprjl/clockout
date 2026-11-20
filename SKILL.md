---
name: clockout
description: Draft the user's timesheet from what they actually did — GitLab activity (MRs, issues, commits), local git log and meetings they describe — in each project's own timesheet layout, then export after approval (clipboard, Google Sheet, …). Handles first-time setup and per-project setup (layout from a pasted header row or screenshot, destination, meetings). Use when the user says "/clockout", "/timesheet", "fill/log my timesheet", "clock out", "timesheet for yesterday/<date>/<range>", "set up clockout", "our timesheet columns changed", or describes their DSM/meeting schedule.
---

# clockout

Run it when you clock out: it reads what you really did and drafts your timesheet, per project, in each project's own format.

```
CO=~/.claude/skills/clockout          # the repo (symlink)
PY=$CO/.venv/bin/python
S=$CO/scripts
```
All personal state lives in `~/.config/clockout/` (`$PY $S/config.py paths`): `config.yaml` (personal defaults), `secrets.env` (tokens), `projects/<slug>.yaml` (one per project). Examples: `$CO/examples/`. Edit these YAML files directly with your file tools; keep comments; validate after every change with `$PY $S/config.py validate`.

## Disclaimer — show once per session, in one or two lines, before the first preview
- clockout **drafts** a timesheet from commits, merge requests, issue activity and the meetings you described. It's an assistant, not a record of truth.
- **It can't see everything**: team discussions, pairing, reviews outside GitLab, research, support, debugging that never became a commit, repos/tools it isn't configured for.
- **Hours are estimates**; every task line comes from real activity, only the time is assumed.
- **You're responsible for the hours you log** — correct them to the true time and add what's missing. Log honestly and transparently.

## Hard rules
- **Never invent work.** Every bullet traces to an activity item, a configured/declared meeting, or something the user said.
- Only **hours** are assumed, and the preview says so.
- Nothing is exported before the user approves the preview.
- Never print secrets (`secrets.env`, tokens, web-app URLs) — refer to them by name.

---

## A. Routing — what to do on `/clockout …`
1. `$PY $S/config.py paths` → if `exists` is false → **Setup: personal** (B1), then continue.
2. `$PY $S/config.py detect` (cwd) → if `project` is null and the cwd is a git repo the user works in → offer **Setup: project** (B2) for it (or proceed with activity-based routing if they decline).
3. Arguments:
   - `setup` → B1 (if needed) then B2 for the cwd repo; `setup <slug>` → edit that project.
   - "columns/layout changed", a pasted header row, or a screenshot of a sheet → **B3 re-map layout**.
   - meeting descriptions → **C. Meetings** (then continue if a date was also given).
   - `today` (default, **daily mode**), `yesterday`, `YYYY-MM-DD`, `A..B` (**bulk mode**) → **D. Run**.
   - Words like "just print" → `--to tsv`; "to the sheet" → that project's sheet destination; "replace" → `--replace-date`.

## B. Setup (once per person, once per project)
Ask only what you can't infer; prefill from git config, the remote, and existing config. Keep it to a few short questions.

**B1. Personal** → write `~/.config/clockout/config.yaml` (start from `$CO/examples/config.yaml`) and `secrets.env` (chmod 600):
- name as it appears in timesheets; timezone; workdays; work hours + realistic focused hours (`max_focus`, default 7).
- GitLab: instance URL(s) (prefill from the cwd remote host). Tell the user to create a **personal access token with only `read_api`** (avatar → Edit profile → Access tokens) and paste it into `secrets.env` **themselves** (`! open -e ~/.config/clockout/secrets.env`) — never ask them to paste it in chat.
- commit emails: prefill from `git config user.email` (+ global).

**B2. Project** → write `~/.config/clockout/projects/<slug>.yaml` (slug: short kebab-case, e.g. `acme-web`). Start from the closest file in `$CO/examples/projects/`.
1. **Match rules** — propose from `config.py detect`: `remotes` (glob of the remote URL), `gitlab_projects` (`group/repo`), `paths`. Several repos can belong to one project.
2. **Layout** — ask the user to paste the timesheet's header row (or a row with data), share a screenshot, or describe it. Map every column, in order, to a `value`: `person, date, task, hours, remarks, project, refs, ticket, blank, ask, const:<text>`, plus a `format` where relevant (dates: `MMM d`, `D MMM`, `DD/MM/YYYY`, `YYYY-MM-DD`…; hours: `number`, `h:mm`, `~number`, `minutes`; refs: `lines`/`comma`). Columns you can't fill from activity (work package, billable, category…) → `ask` (with `default`/`choices` if the user gives them). Choose `granularity`: `per_day` (the day's bullets in one cell — when the sample looks like that) or `per_task` (one row per task). Headers must match the sheet's header text exactly.
3. **Show it working**: render 1–2 real recent rows in the new layout (draft quickly from `collect.py --date yesterday`, save a temp entries file, `$PY $S/export.py --entries <tmp> --preview`) and confirm with the user.
4. **Destination(s)** (`$PY $S/export.py --list`): `clipboard` (default; paste into any sheet), `tsv`, `gsheet_webapp` (writes into the user's own Google Sheet; README → Apps Script), `gsheet_api`. Secrets go in `secrets.env` under the names given in `url_env`/`token_env`.
5. Optional: meetings (→ C), and `overrides` of any personal key (person name, workdays, max_focus, style, holidays) for this project only.
6. `config.py validate` → fix anything reported.

**B3. Layout changed** → re-map only the affected project's `layout` the same way, show a rendered sample, validate. Sheet destinations refuse to write when the live header ≠ layout headers; when that error appears, offer B3.

## C. Meetings the user describes
Stored per project under `meetings` in its YAML (`recurring` / `one_off`). Which project: the one named, else the cwd project, else ask. Nothing is assumed beyond what the user says.
- Recurring ("DSM on alternate days from Oct 27, ~20 min", "weekly sync on Mondays, 1h") → `{name, start, every_days | weekdays, hours, skip_dates: [], note}`; "weekly" without a weekday → `every_days: 7`. Round durations up to the project's `hours_step` (20 min → 0.5).
- Changed/ended ("DSM was until Nov 20", "from today it's weekly, 1h") → set `until` on the existing entry and add a new one; never rewrite past schedules.
- Cancelled once → add the date to `skip_dates`. One-off ("1h discussion with a teammate today") → `one_off: {date, name, hours, note}`.
- Confirm in one line ("DSM: every 2 days, Oct 27 → Nov 20, 0.5h").

## D. Run
1. **Collect**: `$PY $S/collect.py --date <today|yesterday|YYYY-MM-DD|A..B>` → `days[].projects[slug]` with `work`, `scheduled_meetings`, `is_workday`, `is_holiday`, `work_hours`, `hours_step`, `style`, `granularity`, `ask_columns`, `primary`; plus `days[].unassigned` and `warnings`. Mention warnings briefly.
2. **Unassigned activity** (repos/projects not configured): list them once and ask: assign to an existing project (add a match rule), set it up now (B2), or ignore (add to `ignore` in config.yaml so it never asks again).
3. **Ask once** (daily: "Anything today besides <scheduled meetings> — discussions, reviews, research?"; bulk: once for the whole range). Meetings → C; other answers become bullets (user-reported = real).
4. **Draft entries per project**, following that project's `style`, `granularity` and layout:
   - Days: not a workday or a holiday → no row unless there's activity. Workday with work → main row (per_day) or one row per task (per_task). Workday with only meetings → just the meeting(s). Workday with nothing → a date-only entry **only for the `primary` project** (so the gap is visible); other projects get nothing.
   - Style: `- ` bullets, 1–6 per row, 3–8 words, plain past tense; group commits by feature; drop commit prefixes, merge commits, branch/file/env names, WIP/lint noise. Reviews → "- Reviewed <topic>". Meetings < 1h → first bullet(s) (`- DSM`); ≥ 1h → their own entry, details in remarks.
   - Hours (the only assumption): per task, effort incl. planning, prompting AI tools, thinking, testing, design curation — tiny 0.5–1, small fix 1–1.5, medium 2–3, large 3–5; timestamps are hints. Day total (all projects, incl. meetings) stays within `work_hours.max_focus` unless activity clearly shows more; a light day stays light. Meetings use their stated hours. Round to `hours_step`. Daily mode before the day ends → estimate so far and say so.
   - `ask` columns: collect answers once per distinct value and put them in `fields: {"<header>": "<value>"}`.
5. **Preview — show exactly what will be sent, per destination**: disclaimer (once per session), then `$PY $S/export.py --entries <file> --preview`. It lists, per project and per destination, what that destination will receive: rows in the project's layout for clipboard/sheets, or the exact records for API destinations (e.g. date, duration, work item, activity, comment). Show each as a markdown table (newlines as `<br>`, no emojis/icons), with totals per day, one line on how hours were estimated, and the days you were unsure about. For API destinations also run `--dry-run` (checks the server — project, permissions, existing entries — writes nothing) and mention anything it reports. **Wait for approval**; apply edits; re-preview if substantial.
6. **Save** `$CO/.cache/entries-<first>[_<last>].json`:
   ```json
   {"entries": [
     {"profile": "acme-web", "date": "2026-11-19", "task": "- DSM\n- Fixed login error messages", "hours": 6.5,
      "remarks": "", "kind": "work", "estimated": true, "project": "acme/website",
      "refs": ["https://gitlab.example.com/acme/website/-/merge_requests/58"], "fields": {}},
     {"profile": "acme-web", "date": "2026-11-20", "kind": "empty"}
   ]}
   ```
   `profile` = project slug (routing); `project`/`refs` = where the work happened and the links backing it.
7. **Export**: `$PY $S/export.py --entries <file>` sends every project's entries to its own destinations in its own layout. With several projects and a clipboard destination, export one project at a time (`--project <slug>`) and let the user paste between them. If a sheet destination says rows already exist → tell the user which, and re-run with `--replace-date` only if they agree. If it says the header changed → B3. Report what each destination printed.
