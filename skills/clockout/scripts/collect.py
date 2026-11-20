# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "requests>=2.31",
#   "PyYAML>=6.0",
#   "google-api-python-client>=2.100",
#   "google-auth-oauthlib>=1.2",
#   "google-auth>=2.23",
# ]
# ///
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


DEFAULT_REFS = {
    # work item ids in text: OP#123, op#123, OP #123, [OP#123], PP#123; semantic ids OP#TL-42
    "id_patterns": [r"(?i)\b(?:OP|PP)[ \t]?#[ \t]?(\d+)\b", r"\b(?:OP|PP)#([A-Z][A-Z0-9_]*-\d+)\b"],
    # work item ids in branch names: fix/OP-123-…, OpenProject "Git actions" <type>/123-… (NOT GitLab's 12-title)
    "branch_patterns": [r"(?i)(?:^|/)(?:op|pp)-(\d+)(?=$|[-_/])", r"^[A-Za-z0-9._-]+/(\d+)-"],
    # work item names: a line "Ref: <name>" / "Refs: <name>" (not followed by an id)
    "name_patterns": [r"(?im)^[ \t]*refs?:[ \t]*(?![ \t]*(?:OP|PP)[ \t]?#)(\S.*?)[ \t]*$"],
}
TRAILING = " \t.,;:!?)]}\"'"


def _add(lst: list, v: str):
    if v and v not in lst:
        lst.append(v)


def work_refs(item: dict, cfg: dict | None) -> dict:
    """References in the item's own text (title/message/description) and its own branch."""
    cfg = {**DEFAULT_REFS, **(cfg or {})}
    text = "\n".join(str(item.get(k) or "") for k in ("title", "message", "description"))
    ids, names = [], []
    for pat in cfg["id_patterns"]:
        for m in re.findall(pat, text):
            _add(ids, m)
    for branch in [item.get("branch")] + list(item.get("branches") or []):
        for pat in cfg["branch_patterns"]:
            for m in re.findall(pat, str(branch or "")):
                _add(ids, m)
    for pat in cfg["name_patterns"]:
        for m in re.findall(pat, text):
            _add(names, " ".join(m.split()).strip(TRAILING))
    return {"ids": ids, "names": names}


def inherited_refs(item: dict, cfg: dict | None) -> dict:
    """References a commit inherits from the merge requests it belongs to (title, description, source branch)."""
    out = {"ids": [], "names": []}
    for mr in item.get("mrs") or []:
        r = work_refs({"title": mr.get("title"), "description": mr.get("description"),
                       "branch": mr.get("source_branch")}, cfg)
        for k in out:
            for v in r[k]:
                _add(out[k], v)
    return out


def annotate_commit(item: dict) -> dict:
    msg = item.get("message") or item.get("title") or ""
    item["revert"] = item["title"].startswith('Revert "') or "This reverts commit" in msg
    co = re.findall(r"(?im)^co-authored-by:[ \t]*(.+?)[ \t]*$", msg)
    if co:
        item["co_authors"] = co
    return item


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

    def merge_request(self, pid: int, iid: int) -> dict:
        key = ("mr", pid, iid)
        if key not in self._projects:
            try:
                self._projects[key] = self.get(f"/projects/{pid}/merge_requests/{iid}")
            except requests.RequestException:
                self._projects[key] = {}
        return self._projects[key]

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
    pushes: list = []          # (pid, push_data, push item) → which branch each commit was pushed to
    mrs_seen: set = set()      # (pid, iid) → commits inherit the MR's references
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
        review_note = False
        pid = ev.get("project_id")
        proj = gl.project(pid)["path_with_namespace"] if pid else ""
        web = gl.project(pid).get("web_url", "") if pid else ""
        action, ttype = ev.get("action_name", ""), ev.get("target_type") or ""
        title, url = ev.get("target_title") or "", ""
        if push := ev.get("push_data"):
            pushed.add(pid)
            item = {"source": "gitlab", "time": _local(ev["created_at"]), "project": proj, "kind": "push",
                    "title": f"{action} {push.get('ref_type')} {push.get('ref')}: "
                             f"{push.get('commit_title') or ''} ({push.get('commit_count')} commits)",
                    "branch": push.get("ref") or "", "url": ""}
            items.append(item)
            if push.get("ref_type") == "branch" and push.get("commit_to") and push.get("commit_count"):
                pushes.append((pid, push, item))
            continue
        if ttype in ("Note", "DiffNote", "DiscussionNote"):
            note = ev.get("note") or {}
            ntype, iid = note.get("noteable_type", ""), note.get("noteable_iid")
            kind = f"comment:{ntype}"
            if ntype == "MergeRequest" and iid:
                mr = gl.merge_request(pid, iid)
                mrs_seen.add((pid, iid))
                if (mr.get("author") or {}).get("username") not in (None, me.get("username")):
                    review_note = True
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
                mr = gl.merge_request(pid, iid)
                mrs_seen.add((pid, iid))
                author = (mr.get("author") or {}).get("username")
                items.append({"source": "gitlab", "time": _local(ev["created_at"]), "project": proj, "kind": kind,
                              "title": f"{title} !{iid}", "url": f"{web}/-/merge_requests/{iid}",
                              "description": (mr.get("description") or "")[:2000],
                              "branch": mr.get("source_branch", ""), "mr_author": author,
                              # approving/merging/closing someone else's MR is review work, not the MR's task
                              "review": bool(author and author != me.get("username"))})
                continue
            elif ttype in ("Issue", "WorkItem") and iid:
                title, url = f"{title} #{iid}", f"{web}/-/issues/{iid}"
        items.append({"source": "gitlab", "time": _local(ev["created_at"]), "project": proj,
                      "kind": kind, "title": title, "url": url})
        if review_note:
            items[-1]["review"] = True

    mine = lambda c: c.get("author_email", "").lower() in emails or c.get("author_name", "").lower() in names

    # Which branches each commit was pushed to, and pushes that carried other people's commits.
    sha_branches: dict[str, set] = {}
    candidates: dict[tuple, dict] = {}  # (pid, sha) → commit; only from what *you* pushed / MRs you touched
    for pid, push, pitem in pushes:
        frm = push.get("commit_from")
        if not frm or set(frm) == {"0"}:
            frm = gl.project(pid).get("default_branch") or "main"
        try:
            cmp_ = gl.get(f"/projects/{pid}/repository/compare", **{"from": frm, "to": push["commit_to"]})
        except requests.RequestException:
            continue
        commits = cmp_.get("commits") or []
        for c in commits:
            sha_branches.setdefault(c["id"], set()).add(push["ref"])
            candidates.setdefault((pid, c["id"]), c)
        if any(len(c.get("parent_ids") or []) > 1 for c in commits):
            continue  # a merge into the target branch: others' commits are expected there
        foreign = [{"sha": c["id"][:8], "title": c["title"], "author": f"{c.get('author_name')} <{c.get('author_email')}>"}
                   for c in commits if not mine(c)]
        if foreign:
            pitem["foreign_authors"] = foreign  # you pushed commits authored as someone else (wrong git identity?)

    # Commits of merge requests you touched (still available after the source branch is deleted / squashed).
    sha_mrs: dict[str, list] = {}
    for pid, iid in mrs_seen:
        mr = gl.merge_request(pid, iid)
        try:
            for c in gl.paged(f"/projects/{pid}/merge_requests/{iid}/commits"):
                candidates.setdefault((pid, c["id"]), c)
                sha_mrs.setdefault(c["id"], []).append({"iid": iid, "title": mr.get("title", ""),
                                                        "description": (mr.get("description") or "")[:2000],
                                                        "source_branch": mr.get("source_branch", "")})
        except requests.RequestException:
            pass

    # Branches you pushed to that still exist: their recent commits (covers new-branch pushes later merged).
    # NOTE: not the repo-wide `all=true` listing — on busy repos it can return only part of the history.
    for pid, push, _ in pushes:
        try:
            for c in gl.paged(f"/projects/{pid}/repository/commits", ref_name=push["ref"],
                              since=(start - timedelta(days=7)).isoformat(), until=(end + timedelta(days=1)).isoformat()):
                candidates.setdefault((pid, c["id"]), c)
        except requests.RequestException:
            pass  # branch deleted since (merged) — MR commits cover it

    # Your commits authored that day, with full message and stats.
    for (pid, sha), c in sorted(candidates.items(), key=lambda kv: kv[1].get("authored_date", "")):
        proj = gl.project(pid)
        if not mine(c) or len(c.get("parent_ids") or []) > 1 or not _in_day(c["authored_date"], start, end):
            continue  # someone else's, a merge commit, or authored another day
        try:
            c = gl.get(f"/projects/{pid}/repository/commits/{sha}", stats="true")
        except requests.RequestException as e:
            warnings.append(f"commit {sha[:8]} in {proj['path_with_namespace']}: {e}")
        if True:
                stats = c.get("stats") or {}
                item = {"source": "gitlab", "time": _local(c["authored_date"]),
                        "project": proj["path_with_namespace"], "kind": "commit", "title": c["title"],
                        "message": (c.get("message") or "")[:2000], "url": c.get("web_url", ""), "sha": c["id"],
                        "branches": sorted(sha_branches.get(c["id"], [])), "mrs": sha_mrs.get(c["id"], [])}
                if stats and not stats.get("total"):
                    item["empty"] = True  # e.g. OpenProject "Git actions" --allow-empty commit: a link, not work
                items.append(annotate_commit(item))
    return items


# ---------------------------------------------------------------- local git
def collect_git(start, end, emails: set[str]) -> list[dict]:
    items = []
    for path in config.git_repos():
        out = subprocess.run(
            # --since/--until match committer date (rebases move it), so widen and filter on author date.
            ["git", "-C", path, "log", "--exclude=refs/stash", "--all", "--no-merges",
             f"--since={(start - timedelta(days=7)).isoformat()}", f"--until={(end + timedelta(days=1)).isoformat()}",
             "--format=%H%x1f%aI%x1f%ae%x1f%s%x1f%B%x1e"],
            capture_output=True, text=True)
        if out.returncode:
            warnings.append(f"git log failed in {path}: {out.stderr.strip()}")
            continue
        for rec in out.stdout.split("\x1e"):
            if not rec.strip():
                continue
            sha, ts, email, subject, body = rec.strip("\n").split("\x1f", 4)
            if (emails and email.lower() not in emails) or not _in_day(ts, start, end):
                continue
            item = {"source": "git", "time": _local(ts), "project": os.path.basename(path),
                    "repo_path": path, "kind": "commit", "title": subject, "message": body.strip()[:2000],
                    "url": "", "sha": sha}
            changed = subprocess.run(["git", "-C", path, "diff-tree", "--no-commit-id", "--name-only", "-r", sha],
                                     capture_output=True, text=True).stdout.strip()
            if not changed:
                item["empty"] = True
            items.append(annotate_commit(item))
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
    seen: dict = {}
    deduped = []
    for i in sorted(work, key=lambda i: (i["time"], i["source"] != "gitlab")):  # API copy first
        key = ("commit", i["title"].strip().lower()) if i["kind"] == "commit" else id(i)
        if key not in seen:   # same commit via API + git log, or rebased copies: keep one, merge what each knows
            seen[key] = i
            deduped.append(i)
        else:
            kept = seen[key]
            for k, v in i.items():
                if k not in kept or kept[k] in (None, "", [], {}):
                    kept[k] = v
            if i.get("empty") is False or (kept.get("empty") and not i.get("empty", True)):
                kept.pop("empty", None)

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
        refs_summary: dict = {"ids": {}, "names": {}}
        for i in items:
            i["work_refs"] = work_refs(i, proj.get("work_refs"))
            inh = inherited_refs(i, proj.get("work_refs"))
            inh = {k: [v for v in vs if v not in i["work_refs"][k]] for k, vs in inh.items()}
            if any(inh.values()):
                i["inherited_refs"] = inh
            for k in ("ids", "names"):
                for r in i["work_refs"][k] + (i.get("inherited_refs") or {}).get(k, []):
                    refs_summary[k][r] = refs_summary[k].get(r, 0) + 1
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
            "work_refs": refs_summary,
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
