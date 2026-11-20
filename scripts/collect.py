"""Collect real work activity per day and route it to configured projects, as JSON.

Usage: collect.py [--date today|yesterday|YYYY-MM-DD|A..B] [--cwd DIR] [--no-gitlab] [--no-git] [--no-calendar]

Sources come from ~/.config/clockout/config.yaml (GitLab instances, local git, Calendar);
meetings come from each project's `meetings`. Each activity item is assigned to the project whose
`match` rules it fits; the rest is listed under `unassigned` (minus `ignore` rules).
Missing tokens/config produce warnings, never failures.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from datetime import date, datetime, timedelta, timezone

import requests

import config
from common import day_bounds, parse_range, tz

warnings: list[str] = []


def _in_day(ts: str, start: datetime, end: datetime) -> bool:
    t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return start <= t < end


def _local(ts: str) -> str:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(tz()).isoformat(timespec="minutes")


# ---------------------------------------------------------------- GitLab
class GitLab:
    def __init__(self, url: str, token: str):
        self.url = url.rstrip("/")
        self.base = self.url + "/api/v4"
        self.s = requests.Session()
        self.s.headers["PRIVATE-TOKEN"] = token
        self._projects: dict[int, dict] = {}
        self.me: dict | None = None

    def get(self, path: str, **params):
        r = self.s.get(self.base + path, params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    def paged(self, path: str, **params):
        page = 1
        while True:
            batch = self.get(path, per_page=100, page=page, **params)
            yield from batch
            if len(batch) < 100:
                return
            page += 1

    def project(self, pid: int) -> dict:
        if pid not in self._projects:
            try:
                self._projects[pid] = self.get(f"/projects/{pid}")
            except requests.HTTPError:
                self._projects[pid] = {"path_with_namespace": str(pid), "web_url": ""}
        return self._projects[pid]


def gitlab_clients() -> list[GitLab]:
    out = []
    for g in config.personal()["sources"].get("gitlab", []):
        token = config.secret(g.get("token_env"))
        if not token:
            warnings.append(f"{g.get('token_env')} not set in secrets.env; skipped GitLab {g.get('url')}")
            continue
        gl = GitLab(g["url"], token)
        try:
            gl.me = gl.get("/user")
        except requests.RequestException as e:
            warnings.append(f"GitLab {g['url']}: auth failed: {e}")
            continue
        out.append(gl)
    return out


def collect_gitlab(gl: GitLab, day: date, start, end, emails: set[str]) -> list[dict]:
    me = gl.me
    emails = emails | {e.lower() for e in (me.get("email"), me.get("public_email"), me.get("commit_email")) if e}
    names = {me.get("name", "").lower(), me.get("username", "").lower()} - {""}
    items: list[dict] = []
    pushed: set[int] = set()
    try:
        events = list(gl.paged(f"/users/{me['id']}/events", sort="asc",
                               after=(day - timedelta(days=1)).isoformat(),
                               before=(day + timedelta(days=1)).isoformat()))
    except requests.RequestException as e:
        warnings.append(f"GitLab {gl.url}: events failed: {e}")
        return []
    for ev in events:
        if not _in_day(ev["created_at"], start, end):
            continue
        pid = ev.get("project_id")
        proj = gl.project(pid)["path_with_namespace"] if pid else ""
        web = gl.project(pid).get("web_url", "") if pid else ""
        action, ttype = ev.get("action_name", ""), ev.get("target_type") or ""
        title, url = ev.get("target_title") or "", ""
        if push := ev.get("push_data"):
            pushed.add(pid)
            items.append({"source": "gitlab", "time": _local(ev["created_at"]), "project": proj, "kind": "push",
                          "title": f"{action} {push.get('ref_type')} {push.get('ref')}: "
                                   f"{push.get('commit_title') or ''} ({push.get('commit_count')} commits)",
                          "url": ""})
            continue
        if ttype in ("Note", "DiffNote", "DiscussionNote"):
            note = ev.get("note") or {}
            ntype, iid = note.get("noteable_type", ""), note.get("noteable_iid")
            kind = f"comment:{ntype}"
            title = f"{title} {('!' if ntype == 'MergeRequest' else '#') + str(iid) if iid else ''}".strip()
            body = (note.get("body") or "").strip().replace("\n", " ")
            if body:
                title += f" — \"{body[:140]}\""
            if iid and web:
                url = f"{web}/-/{'merge_requests' if ntype == 'MergeRequest' else 'issues'}/{iid}"
        else:
            iid = ev.get("target_iid")
            kind = f"{ttype or 'project'}:{action}"
            if ttype == "MergeRequest" and iid:
                title, url = f"{title} !{iid}", f"{web}/-/merge_requests/{iid}"
            elif ttype in ("Issue", "WorkItem") and iid:
                title, url = f"{title} #{iid}", f"{web}/-/issues/{iid}"
        items.append({"source": "gitlab", "time": _local(ev["created_at"]), "project": proj,
                      "kind": kind, "title": title, "url": url})

    # Full commit messages for projects pushed to that day (push events only carry one title).
    for pid in pushed:
        proj = gl.project(pid)
        try:
            for c in gl.paged(f"/projects/{pid}/repository/commits", all="true",
                              since=(start - timedelta(days=7)).isoformat(), until=end.isoformat()):
                if c.get("author_email", "").lower() not in emails and c.get("author_name", "").lower() not in names:
                    continue
                if len(c.get("parent_ids") or []) > 1 or not _in_day(c["authored_date"], start, end):
                    continue  # merge commit, or authored another day (since/until use committed date)
                items.append({"source": "gitlab", "time": _local(c["authored_date"]),
                              "project": proj["path_with_namespace"], "kind": "commit",
                              "title": c["title"], "url": c.get("web_url", ""), "sha": c["id"]})
        except requests.RequestException as e:
            warnings.append(f"commits for {proj['path_with_namespace']}: {e}")
    return items


# ---------------------------------------------------------------- local git
def collect_git(start, end, emails: set[str]) -> list[dict]:
    items = []
    for path in config.git_repos():
        out = subprocess.run(
            # --since/--until match committer date (rebases move it), so widen and filter on author date.
            ["git", "-C", path, "log", "--exclude=refs/stash", "--all", "--no-merges",
             f"--since={(start - timedelta(days=7)).isoformat()}", f"--until={(end + timedelta(days=1)).isoformat()}",
             "--format=%H%x1f%aI%x1f%ae%x1f%s"],
            capture_output=True, text=True)
        if out.returncode:
            warnings.append(f"git log failed in {path}: {out.stderr.strip()}")
            continue
        for line in out.stdout.splitlines():
            sha, ts, email, subject = line.split("\x1f", 3)
            if (emails and email.lower() not in emails) or not _in_day(ts, start, end):
                continue
            items.append({"source": "git", "time": _local(ts), "project": os.path.basename(path),
                          "repo_path": path, "kind": "commit", "title": subject, "url": "", "sha": sha})
    return items


# ---------------------------------------------------------------- calendar
def collect_calendar(start, end) -> list[dict]:
    cal = config.personal()["sources"].get("calendar", {})
    if not cal.get("enabled"):
        return []
    from common import google_creds

    creds = google_creds("calendar")
    if not creds:
        warnings.append("Calendar enabled but not authorised (scripts/auth.py calendar); skipped")
        return []
    from googleapiclient.discovery import build

    svc = build("calendar", "v3", credentials=creds, cache_discovery=False)
    dsm = re.compile(cal.get("dsm_pattern") or r"(?i)\bdsm\b")
    resp = svc.events().list(calendarId=cal.get("calendar_id", "primary"), timeMin=start.isoformat(),
                             timeMax=end.isoformat(), singleEvents=True, orderBy="startTime", maxResults=250).execute()
    items = []
    for ev in resp.get("items", []):
        if "dateTime" not in ev.get("start", {}) or ev.get("eventType") in ("workingLocation", "outOfOffice"):
            continue
        me = next((a for a in ev.get("attendees", []) if a.get("self")), None)
        if me and me.get("responseStatus") == "declined":
            continue
        s, e = datetime.fromisoformat(ev["start"]["dateTime"]), datetime.fromisoformat(ev["end"]["dateTime"])
        title = ev.get("summary", "(no title)")
        items.append({"source": "calendar", "time": s.astimezone(tz()).isoformat(timespec="minutes"),
                      "kind": "dsm" if dsm.search(title) else "meeting", "title": title,
                      "hours": round((e - s).total_seconds() / 3600, 2),
                      "description": (ev.get("description") or "")[:300], "url": ev.get("htmlLink", "")})
    return items


# ---------------------------------------------------------------- meetings (per project)
def scheduled_meetings(proj: dict, day: date, settings: dict) -> list[dict]:
    m_cfg, out = proj.get("meetings") or {}, []
    wd = config.workday_numbers(settings)
    for m in m_cfg.get("recurring", []) or []:
        start = date.fromisoformat(str(m["start"]))
        until = date.fromisoformat(str(m["until"])) if m.get("until") else None
        if day < start or (until and day > until) or day.isoformat() in [str(x) for x in m.get("skip_dates", [])]:
            continue
        if m.get("skip_weekends", True) and day.weekday() not in wd:
            continue
        if m.get("weekdays"):
            hit = config.WEEKDAYS[day.weekday()] in [str(w).lower()[:3] for w in m["weekdays"]]
        else:
            hit = (day - start).days % int(m.get("every_days", 1)) == 0
        if hit:
            out.append({"name": m["name"], "hours": float(m["hours"]), "kind": "recurring", "note": m.get("note", "")})
    for m in m_cfg.get("one_off", []) or []:
        if str(m["date"]) == day.isoformat():
            out.append({"name": m["name"], "hours": float(m["hours"]), "kind": "one_off", "note": m.get("note", "")})
    return out


# ---------------------------------------------------------------- per day
def route(item: dict) -> str | None:
    return config.match(gitlab_project=item.get("project", "") if item["source"] == "gitlab" else "",
                        repo_path=item.get("repo_path", ""))


def collect_day(day: date, clients, emails, primary: str | None, use_git: bool, use_cal: bool) -> dict:
    start, end = day_bounds(day)
    work: list[dict] = []
    for gl in clients:
        work += collect_gitlab(gl, day, start, end, emails)
    if use_git:
        work += collect_git(start, end, emails)
    seen, deduped = set(), []
    for i in sorted(work, key=lambda i: i["time"]):  # same commit via API + git log, or rebased copies
        key = ("commit", i["title"].strip().lower()) if i["kind"] == "commit" else id(i)
        if key not in seen:
            seen.add(key)
            deduped.append(i)

    per_project: dict[str, list] = {}
    unassigned = []
    for i in deduped:
        slug = route(i)
        if slug:
            per_project.setdefault(slug, []).append(i)
        elif not config.ignored(gitlab_project=i.get("project", "") if i["source"] == "gitlab" else "",
                                repo_path=i.get("repo_path", "")):
            unassigned.append(i)

    projects_out = {}
    for slug, proj in config.projects().items():
        s = config.settings(slug)
        sched = scheduled_meetings(proj, day, s)
        items = per_project.get(slug, [])
        if not (items or sched or slug == primary):
            continue
        times = [i["time"] for i in items]
        projects_out[slug] = {
            "name": proj["name"],
            "primary": slug == primary,
            "person": s.get("person", ""),
            "is_workday": day.weekday() in config.workday_numbers(s),
            "is_holiday": day.isoformat() in [str(h) for h in s.get("holidays", [])],
            "work_hours": s.get("work_hours"),
            "hours_step": s.get("hours_step", 0.5),
            "style": s.get("style"),
            "granularity": proj["layout"]["granularity"],
            "ask_columns": [c["header"] for c in proj["layout"]["columns"] if c.get("value") == "ask"],
            "scheduled_meetings": sched,
            "scheduled_hours": round(sum(m["hours"] for m in sched), 2),
            "first_activity": times[0] if times else None,
            "last_activity": times[-1] if times else None,
            "work": items,
        }
    return {"date": day.isoformat(), "weekday": day.strftime("%A"), "projects": projects_out,
            "unassigned": unassigned,
            "calendar": collect_calendar(start, end) if use_cal else []}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="today")
    ap.add_argument("--cwd", default=os.getcwd())
    ap.add_argument("--no-gitlab", action="store_true")
    ap.add_argument("--no-git", action="store_true")
    ap.add_argument("--no-calendar", action="store_true")
    a = ap.parse_args()

    if not config.PERSONAL.exists():
        warnings.append(f"no personal config at {config.PERSONAL}; run /clockout setup")
    primary = config.personal().get("default_project") or config.detect(a.cwd)["project"]
    emails = {e.lower() for e in config.personal()["sources"]["git"].get("emails", [])}
    clients = [] if a.no_gitlab else gitlab_clients()
    days = [collect_day(d, clients, emails, primary, not a.no_git, not a.no_calendar) for d in parse_range(a.date)]
    print(json.dumps({"primary_project": primary, "days": days, "warnings": warnings,
                      "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")},
                     indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
