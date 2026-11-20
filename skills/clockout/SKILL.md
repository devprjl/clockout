---
name: clockout
allowed-tools: Bash(uv run --quiet --script ${CLAUDE_SKILL_DIR}/scripts/*)
description: Draft the user's timesheet from what they actually did — GitLab activity (MRs, issues, commits), local git log and meetings they describe — in each project's own timesheet layout, then export after approval (clipboard, Google Sheet, OpenProject time entries). Handles first-time setup and per-project setup (layout from a pasted header row or screenshot, destination, meetings). Use when the user says "/clockout", "/clockout:clockout", "/timesheet", "fill/log my timesheet", "clock out", "timesheet for yesterday/<date>/<range>", "set up clockout", "our timesheet columns changed", "push/send my timesheet", "remove duplicate time entries", or describes their DSM/meeting schedule.
---

# clockout

Run it when you clock out: it reads what you really did and drafts your timesheet, per project, in each project's own format.

Scripts ship next to this file and run with **uv**, which provides Python and the dependencies (cached after the first run, no venv). Run every script exactly as written below, e.g. `uv run --quiet --script ${CLAUDE_SKILL_DIR}/scripts/collect.py --date today`.

**Preflight (once per session):** `command -v uv`. If it's missing, stop and tell the user to install it — `brew install uv` or `curl -LsSf https://astral.sh/uv/install.sh | sh` — then run clockout again. Never install it yourself.

All personal state lives in `~/.config/clockout/` (`uv run --quiet --script ${CLAUDE_SKILL_DIR}/scripts/config.py paths`): `config.yaml` (personal defaults), `secrets.env` (tokens), `projects/<slug>.yaml` (one per project). Examples: `${CLAUDE_SKILL_DIR}/examples/`. Edit these YAML files directly with your file tools; keep comments; validate after every change with `uv run --quiet --script ${CLAUDE_SKILL_DIR}/scripts/config.py validate`.

## Disclaimer — show once per session, in one or two lines, before the first preview
- clockout **drafts** a timesheet from commits, merge requests, issue activity and the meetings you described. It's an assistant, not a record of truth.
- **It can't see everything**: team discussions, pairing, reviews outside GitLab, research, support, debugging that never became a commit, repos/tools it isn't configured for.
- **Hours are estimates**; every task line comes from real activity, only the time is assumed.
- **You're responsible for the hours you log** — correct them to the true time and add what's missing. Log honestly and transparently.

## Hard rules
- **Never invent work.** Every bullet traces to an activity item, a configured/declared meeting, or something the user said.
- Only **hours** are assumed, and the preview says so.
- Nothing is exported before the user approves the preview.
- Never print secrets (`secrets.env`, tokens, web-app URLs) — refer to them by name. Remote URLs from `detect` are already redacted; never print raw `git remote -v` output.
- **Never delete or overwrite logged time without an explicit yes** for those specific entries (`--replace-date`, `op.py delete`). When unsure, add (`--append`) or ask.

---

## A. Routing — what to do on `/clockout …`
1. `uv run --quiet --script ${CLAUDE_SKILL_DIR}/scripts/config.py paths` → if `exists` is false → **Setup: personal** (B1), then continue.
2. `uv run --quiet --script ${CLAUDE_SKILL_DIR}/scripts/config.py detect` (run it **from the repo**, not from `~/.config/clockout`) → relay its `warnings` (credentials embedded in a remote → suggest revoking that token and switching to SSH/credential helper; repo commits as an email not in `sources.git.emails` → offer to add it). If `project` is null and the cwd is a git repo the user works in → offer **Setup: project** (B2) for it (or proceed with activity-based routing if they decline).
3. Arguments:
   - `setup` → B1 (if needed) then B2 for the cwd repo; `setup <slug>` → edit that project.
   - "columns/layout changed", a pasted header row, or a screenshot of a sheet → **B3 re-map layout**.
   - meeting descriptions → **C. Meetings** (then continue if a date was also given).
   - `today` (default, **daily mode**), `yesterday`, `YYYY-MM-DD`, `A..B` (**bulk mode**) → **D. Run**.
   - Words like "just print" → `--to tsv`; "to the sheet" → that project's sheet destination; "replace" → `--replace-date`; "add"/"append"/"keep what's there" → `--append`.
   - "send it" / "push it" / "looks good" after a preview → **D7 Export** that saved draft. "how do I push?" → explain: the user approves the preview in chat and Claude runs the export (or they run `export.py --entries <file>` themselves).
   - "delete/remove these entries", "clean up duplicates" → **E. Fix logged time**.

## B. Setup (once per person, once per project)
Ask only what you can't infer; prefill from git config, the remote, and existing config. Keep it to a few short questions.

**B1. Personal** → write `~/.config/clockout/config.yaml` (start from `${CLAUDE_SKILL_DIR}/examples/config.yaml`) and `secrets.env` (chmod 600):
- name as it appears in timesheets; timezone; workdays; work hours + realistic focused hours (`max_focus`, default 7).
- GitLab: instance URL(s) (prefill from the cwd remote host). Tell the user to create a **personal access token with only `read_api`** (avatar → Edit profile → Access tokens) and paste it into `secrets.env` **themselves** (`! open -e ~/.config/clockout/secrets.env`) — never ask them to paste it in chat.
- commit emails: prefill from `git config user.email` (+ global). Each repo can commit with its own email — on every B2, add the repo's `commit_email` from `detect` if it's missing.

**B2. Project** → write `~/.config/clockout/projects/<slug>.yaml` (slug: short kebab-case, e.g. `acme-web`). Start from the closest file in `${CLAUDE_SKILL_DIR}/examples/projects/`.
1. **Match rules** — propose from `config.py detect`: `remotes` (glob of the remote URL without credentials, e.g. `'*host:port*group/repo*'`), `gitlab_projects` (`group/repo`), `paths`. Several repos can belong to one project. If the remote's GitLab host isn't in `sources.gitlab`, add it (`{url, token_env: GITLAB_<NAME>_TOKEN}`) — tokens are per GitLab instance, shared by all its projects.
2. **Layout** — ask the user to paste the timesheet's header row (or a row with data), share a screenshot, or describe it. Map every column, in order, to a `value`: `person, date, task, hours, remarks, project, refs, ticket, work_package, blank, ask, const:<text>`, plus a `format` where relevant (dates: `MMM d`, `D MMM`, `DD/MM/YYYY`, `YYYY-MM-DD`…; hours: `number`, `h:mm`, `~number`, `minutes`; refs: `lines`/`comma`). Columns you can't fill from activity (work package, billable, category…) → `ask` (with `default`/`choices` if the user gives them; a described dropdown = `choices`). Choose `granularity`: `per_day` (the day's bullets in one cell — when the sample looks like that) or `per_task` (one row per task) or `per_work_package` (OpenProject). Headers must match the sheet's header text exactly.
3. **Show it working**: render 1–2 real recent rows in the new layout (draft quickly from `collect.py --date yesterday`, save a temp entries file, `uv run --quiet --script ${CLAUDE_SKILL_DIR}/scripts/export.py --entries <tmp> --preview`) and confirm with the user.
4. **Destination(s)** (`uv run --quiet --script ${CLAUDE_SKILL_DIR}/scripts/export.py --list`): `clipboard` (default; paste into any sheet), `tsv`, `gsheet_webapp` (writes into the user's own Google Sheet; README → Apps Script), `gsheet_api`, `openproject` (time entries; README → OpenProject). Secrets go in `secrets.env` under the names given in `url_env`/`token_env`. **Credentials are shared by default**: config.yaml `destination_defaults: {<type>: {url_env, token_env}}` is merged into every project's destination of that type, so the first project sets them up once (add empty `KEY=` lines to `secrets.env` for the user to fill) and later projects only set `url_env`/`token_env` when they use a different server/account (e.g. `token_env: OPENPROJECT_TOKEN_CLIENTX`).
   - **OpenProject**: ask for the instance URL and project identifier (a pasted work-package link like `…/projects/<id>/work_packages/43` gives both); the user creates an API token (My account → Access tokens → API) and pastes it into `secrets.env` themselves. Find work packages/activities with `uv run --quiet --script ${CLAUDE_SKILL_DIR}/scripts/op.py work-packages|activities --project <slug>`. On 17+ every entry needs a work package: prefer `work_package: from_refs` + `default_work_package: <bucket id>` with layout `granularity: per_work_package` (commits say `OP#123` or `Ref: <name>`); or a fixed `work_package: <id>`; or `ask` (then add an `ask` column with header `Work package` and fill `fields["Work package"]` per entry, offering ids from `op.py work-packages`). Pick the bucket from `op.py work-packages` yourself when one is obviously named (*General development* → `default_work_package`, *Meetings*/*DSM* → meetings) and say so; ask only if unclear. **Activity**: the destination's `activity` is the default; if the team picks an activity per entry, add an `ask` column with header `Activity`, `default` and `choices` (from `op.py activities`) — an entry's `fields["Activity"]` overrides the default. If an OpenProject MCP server is available, you may use it for lookups, but always write time through `export.py`.
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
1. **Collect**: `uv run --quiet --script ${CLAUDE_SKILL_DIR}/scripts/collect.py --date <today|yesterday|YYYY-MM-DD|A..B>` → `days[].projects[slug]` with `work`, `scheduled_meetings`, `is_workday`, `is_holiday`, `work_hours`, `hours_step`, `style`, `granularity`, `ask_columns`, `primary`; plus `days[].unassigned` and `warnings`. Mention warnings briefly.
2. **Unassigned activity** (repos/projects not configured): list them once and ask: assign to an existing project (add a match rule), set it up now (B2), or ignore (add to `ignore` in config.yaml so it never asks again).
3. **Ask once** (daily: "Anything today besides <scheduled meetings> — discussions, reviews, research?"; bulk: once for the whole range). Meetings → C; other answers become bullets (user-reported = real).
4. **Draft entries per project**, following that project's `style`, `granularity` and layout:
   - Days: not a workday or a holiday → no row unless there's activity. Workday with work → main row (per_day) or one row per task (per_task). Workday with only meetings → just the meeting(s). Workday with nothing → a date-only entry **only for the `primary` project** (so the gap is visible); other projects get nothing.
   - Style: `- ` bullets, 1–6 per row, 3–8 words, plain past tense; group commits by feature; drop commit prefixes, merge commits, branch/file/env names, WIP/lint noise. A commit message that says nothing ("fix stuff") → read its diff (`git show --stat`/`git show <sha>`) and describe what it actually changed; if it's trivial (debug prints, formatting), say so in the preview and let the user decide whether to log it. Reviews → "- Reviewed <topic>". Meetings < 1h → first bullet(s) (`- DSM`); ≥ 1h → their own entry, details in remarks.
   - Hours (the only assumption): per task, effort incl. planning, prompting AI tools, thinking, testing, design curation — tiny 0.5–1, small fix 1–1.5, medium 2–3, large 3–5; timestamps are hints. Day total (all projects, incl. meetings) stays within `work_hours.max_focus` unless activity clearly shows more; a light day stays light. Meetings use their stated hours. Round to `hours_step`. Daily mode before the day ends → estimate so far and say so.
   - `ask` columns: collect answers once per distinct value and put them in `fields: {"<header>": "<value>"}`.
   - **`per_work_package` projects** (time logged per work item, e.g. OpenProject): draft **one entry per (date, work package)**. Each `work` item carries `work_refs` (`ids` from `OP#123`/branch `OP-123`, `names` from a `Ref: <name>` line in commits/MR descriptions); the project has a day-level `work_refs` summary.
     - ids → check with `uv run --quiet --script ${CLAUDE_SKILL_DIR}/scripts/op.py resolve --project <slug> --id <n>` (must exist in that project); names → `uv run --quiet --script ${CLAUDE_SKILL_DIR}/scripts/op.py resolve --project <slug> --name "<name>"` (`exact`/`contains` → use it; `ambiguous`/`none` → ask the user, offering candidates).
     - Put the result in `fields: {"Work package": "<id> <subject>", "Work package source": "OP#12 in commit" | "Ref: <name> in commit" | "user"}`.
     - Work with no reference → leave `Work package` unset; the destination's `default_work_package` (a bucket like *General development*) takes it. Meetings → the meetings bucket if one exists (ask once which).
     - Split the day's hours by effort per work package; the bullets of each entry describe only that work package's work.
     - Use what `collect.py` marks on items and what `op.py resolve` returns in `flags` (tested; see README → OpenProject):
       - Refs also come from the **branch** (`fix/OP-12-…`, OpenProject Git-actions `bug/12-…`; GitLab's own `12-title` branches are NOT OpenProject ids) and are **inherited** from the commit's merge request (`inherited_refs`: MR title/description/source branch — works after squash and branch deletion).
       - `empty: true` commits (e.g. OpenProject's Git-actions `--allow-empty` commit) link a work package but are **not effort**. `revert: true` → mention as a revert, small effort.
       - `review: true` events (approving/merging/commenting on someone else's MR) → the **Code review** bucket (ask once which work package that is), never the MR's own work package — unless the user also committed to it.
       - `foreign_authors` on a push → the user pushed commits authored under another identity (forgot to switch `git config user.email`, or a second email). Tell them which; if those are theirs, offer to add the email to `sources.git.emails` and re-collect. Never silently credit or drop them.
       - `co_authors` → credit the author only; mention the co-author in the bullet if relevant.
       - `resolve` flags: `not_found` / `not_visible` / `other_project` / `none` / `ambiguous` → **ask** (offer candidates, another work package, or the default bucket); never log on them silently. `closed` → warn (OpenProject still accepts time on closed work packages) and ask. `assigned to <someone else>` → allowed, but show it in the preview so the user sees it.
5. **Preview — show exactly what will be sent, per destination**: disclaimer (once per session), then `uv run --quiet --script ${CLAUDE_SKILL_DIR}/scripts/export.py --entries <file> --preview`. It lists, per project and per destination, what that destination will receive: rows in the project's layout for clipboard/sheets, or the exact records for API destinations (e.g. date, duration, work item, activity, comment). Show each as a markdown table (newlines as `<br>`, no emojis/icons), with totals per day, one line on how hours were estimated, and the days you were unsure about. For API destinations also run `--dry-run` (checks the server — project, permissions, existing entries — writes nothing) and mention anything it reports. **If entries already exist on those dates**, list them (`op.py logged --project <slug> --date <d>`) with the resulting day total, and offer: (a) keep them and send nothing / only what's new with `--append` (exact duplicates are skipped), (b) `--replace-date` (replaces only entries **clockout created** — their comment ends with `[clockout]`; entries the user logged by hand are always kept, so say which ones stay and what the day total becomes), (c) edit the draft. Never pick (b) on your own. **Wait for approval**; apply edits; re-preview if substantial. If the user narrows the draft ("only the last one"), save it as a separate entries file and re-preview.
6. **Save** `~/.config/clockout/drafts/entries-<first>[_<last>].json` (create the folder if needed; never inside the skill folder — plugin updates replace it):
   ```json
   {"entries": [
     {"profile": "acme-web", "date": "2026-11-19", "task": "- DSM\n- Fixed login error messages", "hours": 6.5,
      "remarks": "", "kind": "work", "estimated": true, "project": "acme/website",
      "refs": ["https://gitlab.example.com/acme/website/-/merge_requests/58"], "fields": {}},
     {"profile": "acme-web", "date": "2026-11-20", "kind": "empty"}
   ]}
   ```
   `profile` = project slug (routing); `project`/`refs` = where the work happened and the links backing it.
7. **Export**: `uv run --quiet --script ${CLAUDE_SKILL_DIR}/scripts/export.py --entries <file>` sends every project's entries to its own destinations in its own layout. With several projects and a clipboard destination, export one project at a time (`--project <slug>`) and let the user paste between them. Flags: `--append` (API destinations: add next to existing same-day entries, delete nothing, skip identical ones) or `--replace-date` (sheets: replace the user's own rows on those dates; OpenProject: replace only the user's `[clockout]`-tagged entries, keeping hand-logged ones) — never both. Hours are validated first (> 0 per entry, ≤ 24 per day) and comments are trimmed to OpenProject's 1000-character limit. If an export fails part-way it prints `PARTIAL — written N of M`; fix the failing entry and re-run with `--append` (already-written identical entries are skipped). If a destination says rows/entries already exist → tell the user which, and re-run with the flag they choose. If it says the header changed → B3. Report what each destination printed, then verify API destinations with `op.py logged` and give the day total; if it now looks doubled (the same work logged twice), point it out and offer **E**.
   - Running again later the same day: draft only work newer than what's logged and export with `--append`.

## E. Fix logged time
- See it: `uv run --quiet --script ${CLAUDE_SKILL_DIR}/scripts/op.py logged --project <slug> --date <d|A..B>` (id, hours, work package, activity, comment).
- Delete: `uv run --quiet --script ${CLAUDE_SKILL_DIR}/scripts/op.py delete --project <slug> --id 8,9` — only the user's own entries in that project; refuses anything else. Confirm the exact ids first (when the user says "remove the duplicates", propose which side to keep — usually the user's own manual entries — and state it).
- Change hours/comment: delete + re-export the corrected entry with `--append`. Show the remaining entries and day total afterwards.
