"""Log entries as OpenProject time entries (API v3).

dest:
  type: openproject
  url: https://openproject.example.com        # or url_env: OPENPROJECT_URL
  token_env: OPENPROJECT_TOKEN                # My account → Access tokens → API
  project: my-project                         # project identifier (or numeric id)
  activity: Development                       # optional; time entry activity name (default for every entry)
                                              #   an entry's fields["Activity"] (e.g. an `ask` column) overrides it
  work_package: from_refs | ask | <id> | none # from_refs/ask: entry fields["Work package"] (set while drafting
                                              #   from OP#123 / "Ref: <name>" in commits/MRs); required on 17+
  default_work_package: <id>                  # optional bucket for entries with no work package (e.g. General development)
  comment: task | first_line                  # optional; default task (all bullets)

One time entry per entry with hours (date-only entries are skipped). Before writing, it looks for
time entries you already logged in that project on those dates and stops, unless --replace-date,
which deletes only those entries of yours first, or --append, which adds next to them (deletes nothing)
and skips an entry identical to one already logged (same date, work package, duration and comment).
"""
from __future__ import annotations

import json
import re
import sys

import requests

from config import secret

DESCRIPTION = "log time entries in OpenProject (API v3)"
REQUIRED = ["token_env", "project"]
WP_FIELD = "Work package"
ACTIVITY_FIELD = "Activity"


def check(dest) -> list[str]:
    out = [f"openproject: missing '{k}'" for k in REQUIRED if not dest.get(k)]
    if not (dest.get("url") or dest.get("url_env")):
        out.append("openproject: missing 'url' (or 'url_env')")
    if dest.get("token_env") and not secret(dest["token_env"]):
        out.append(f"openproject: secret {dest['token_env']} is not set")
    if dest.get("url_env") and not secret(dest["url_env"]):
        out.append(f"openproject: secret {dest['url_env']} is not set")
    return out


def iso_duration(hours: float) -> str:
    mins = round(float(hours) * 60)
    h, m = divmod(mins, 60)
    return "PT" + (f"{h}H" if h else "") + (f"{m}M" if m or not h else "")


class OpenProject:
    def __init__(self, url: str, token: str):
        self.base = url.rstrip("/")
        self.s = requests.Session()
        self.s.auth = ("apikey", token)
        self.s.headers.update({"Content-Type": "application/json", "Accept": "application/hal+json"})

    def req(self, method: str, path: str, **kw):
        r = self.s.request(method, self.base + path, timeout=30, **kw)
        if r.status_code >= 400:
            try:
                body = r.json()
                msg = body.get("message", "")
                details = [e.get("message") for e in body.get("_embedded", {}).get("errors", [])]
                msg = "; ".join([msg] + [d for d in details if d])
            except ValueError:
                msg = r.text[:300]
            raise SystemExit(f"error: openproject {method} {path}: HTTP {r.status_code}: {msg}")
        return r.json() if r.content else {}

    def project(self, ident: str) -> dict:
        return self.req("GET", f"/api/v3/projects/{ident}")

    def me(self) -> dict:
        return self.req("GET", "/api/v3/users/me")

    def schema(self, project_href: str, wp_id: str | None = None) -> dict:
        """Time-entry schema. Non-admins on 17+ may only open the form for a work package
        (`entity`), older versions use `workPackage`; admins can also use the project alone."""
        attempts = []
        if wp_id:
            wp_href = f"/api/v3/work_packages/{wp_id}"
            attempts += [{"entity": {"href": wp_href}},
                         {"project": {"href": project_href}, "workPackage": {"href": wp_href}}]
        attempts.append({"project": {"href": project_href}})
        last = None
        for links in attempts:
            r = self.s.post(self.base + "/api/v3/time_entries/form", data=json.dumps({"_links": links}), timeout=30)
            if r.ok:
                return r.json().get("_embedded", {}).get("schema", {})
            last = r
        try:
            msg = last.json().get("message", "")
        except ValueError:
            msg = last.text[:200]
        raise SystemExit(f"error: openproject: can't open the time entry form (HTTP {last.status_code}: {msg}). "
                         "Check that your user may log time in this project"
                         + ("" if wp_id else " and set `work_package` on the destination"))

    @staticmethod
    def activities(schema: dict) -> dict[str, str]:
        allowed = schema.get("activity", {}).get("_embedded", {}).get("allowedValues", [])
        return {a["name"].lower(): a["_links"]["self"]["href"] for a in allowed}

    @staticmethod
    def wp_link(schema: dict) -> tuple[str, bool]:
        """Which link carries the work package, and whether it's required.
        OpenProject 17+: `entity` (required); older: `workPackage` (optional)."""
        if "entity" in schema:
            return "entity", bool(schema["entity"].get("required"))
        return "workPackage", bool(schema.get("workPackage", {}).get("required"))

    def my_entries(self, project_id, dates: list[str]) -> list[dict]:
        filters = [{"user": {"operator": "=", "values": ["me"]}},
                   {"project_id": {"operator": "=", "values": [str(project_id)]}},
                   {"spent_on": {"operator": "<>d", "values": [min(dates), max(dates)]}}]
        out, offset = [], 1
        while True:
            page = self.req("GET", "/api/v3/time_entries",
                            params={"filters": json.dumps(filters), "pageSize": 100, "offset": offset})
            els = page.get("_embedded", {}).get("elements", [])
            out += [e for e in els if e.get("spentOn") in dates]
            if len(els) < 100:
                return out
            offset += 1


def _payloads(entries, dest) -> list[tuple[dict, dict]]:
    """(entry, what will be sent) for every entry that has hours."""
    wp_mode = str(dest.get("work_package", "none"))
    out = []
    for e in entries:
        if e.get("hours") in ("", None) or float(e["hours"]) <= 0:
            continue
        comment = e["task"] if dest.get("comment", "task") == "task" else e["task"].split("\n")[0]
        if e.get("remarks"):
            comment = f"{comment}\n\n{e['remarks']}"
        wp, why = None, ""
        m = re.match(r"\s*(?:OP|PP)?#?(\d+)", str(e.get("fields", {}).get(WP_FIELD, "")))  # "41", "#41", "OP#41 x"
        if m and wp_mode in ("ask", "from_refs"):
            wp, why = m.group(1), e.get("fields", {}).get("Work package source") or "from entry"
        elif re.fullmatch(r"\d+", wp_mode):
            wp, why = wp_mode, "fixed"
        if not wp and dest.get("default_work_package"):
            wp, why = str(dest["default_work_package"]), "default (no reference)"
        out.append((e, {"spentOn": e["date"], "hours": iso_duration(e["hours"]), "comment": comment,
                        "project": str(dest["project"]),
                        "activity": e.get("fields", {}).get(ACTIVITY_FIELD) or dest.get("activity") or "(default)",
                        "workPackage": wp, "why": why}))
    return out


def preview(entries, dest, layout) -> str:
    """What will be sent, without contacting the server."""
    url = dest.get("url") or (f"${dest['url_env']}" if dest.get("url_env") else "?")
    rows = _payloads(entries, dest)
    skipped = len(entries) - len(rows)
    lines = [f"OpenProject time entries → project '{dest['project']}' at {url}"
             f" (activity: {dest.get('activity') or 'server default'}, work package: {dest.get('work_package', 'none')})",
             "| Date | Hours | Duration sent | Activity | Work package | Why this work package | Comment |",
             "|---|---|---|---|---|---|---|"]
    for e, b in rows:
        label = e.get("fields", {}).get(WP_FIELD) or (f"#{b['workPackage']}" if b["workPackage"] else "-")
        if b["workPackage"] and not str(label).lstrip("#OP").startswith(str(b["workPackage"])):
            label = f"#{b['workPackage']}"
        lines.append(f"| {b['spentOn']} | {float(e['hours']):g} | {b['hours']} | {b['activity']} | {label} | {b['why'] or '-'} | "
                     + b["comment"].replace("\n", "<br>") + " |")
    lines.append(f"{len(rows)} entries, {sum(float(e['hours']) for e, _ in rows):g}h total"
                 + (f"; {skipped} date-only entr{'y' if skipped == 1 else 'ies'} not sent" if skipped else ""))
    return "\n".join(lines)


def export(entries, dest, layout, opts) -> str:
    url = dest.get("url") or secret(dest.get("url_env"))
    op = OpenProject(url, secret(dest["token_env"]))
    proj = op.project(dest["project"])
    proj_href = proj["_links"]["self"]["href"]
    version = op.req("GET", "/api/v3").get("coreVersion") or "version hidden for non-admins"

    first_wp = next((b["workPackage"] for _, b in _payloads(entries, dest) if b["workPackage"]), None)
    schema = op.schema(proj_href, first_wp)
    wp_key, wp_required = op.wp_link(schema)
    acts = op.activities(schema)
    wanted = {b["activity"] for _, b in _payloads(entries, dest) if b["activity"] != "(default)"}
    bad = sorted(a for a in wanted if str(a).lower() not in acts)
    if bad:
        sys.exit(f"error: openproject: activit{'y' if len(bad) == 1 else 'ies'} {bad} not available; "
                 f"choose from {sorted(acts)}")

    todo = [e for e in entries if e.get("hours") not in ("", None) and float(e["hours"]) > 0]
    dates = sorted({e["date"] for e in todo})
    if not todo:
        return "nothing to log (no entries with hours)"

    if wp_required:
        missing = [e["date"] for e, b in _payloads(todo, dest) if not b["workPackage"]]
        if missing:
            sys.exit(f"error: openproject {version}: time must be logged on a work package here; set "
                     f"`work_package` (from_refs/ask/<id>) or `default_work_package` on this destination — "
                     f"missing for {missing}")
    existing = op.my_entries(proj["id"], dates)
    append = getattr(opts, "append", False)
    dupes = set()
    if append:
        seen = {(x.get("spentOn"), x.get("_links", {}).get(wp_key, {}).get("href", "").rsplit("/", 1)[-1],
                 x.get("hours"), (x.get("comment") or {}).get("raw", "")) for x in existing}
        dupes = {i for i, (e, b) in enumerate(_payloads(todo, dest))
                 if (b["spentOn"], str(b["workPackage"] or ""), b["hours"], b["comment"]) in seen}
    if getattr(opts, "dry_run", False):
        if append:
            return (f"dry run: OpenProject {version}, project '{proj['name']}' found; would add {len(todo) - len(dupes)} "
                    f"entries next to your {len(existing)} existing ones (none deleted)"
                    + (f"; {len(dupes)} identical entr{'y' if len(dupes) == 1 else 'ies'} skipped" if dupes else "")
                    + (f", activities {sorted(wanted)} ok" if wanted else ""))
        return (f"dry run: OpenProject {version}, project '{proj['name']}' found; would log {len(todo)} entries"
                + (f", activities {sorted(wanted)} ok" if wanted else "")
                + (f"; {len(existing)} of your entries already exist on those dates "
                   f"({'would be replaced' if getattr(opts, 'replace_date', False) else 'would block, needs --replace-date'})"
                   if existing else "; no existing entries on those dates"))
    if existing and not append and not getattr(opts, "replace_date", False):
        sys.exit(f"error: openproject: you already logged {len(existing)} time entr"
                 f"{'y' if len(existing) == 1 else 'ies'} in {proj['identifier']} on "
                 f"{sorted({e['spentOn'] for e in existing})}; re-run with --replace-date to replace them, or --append to add next to them")
    for e in ([] if append else existing):
        op.req("DELETE", f"/api/v3/time_entries/{e['id']}")

    created = []
    for i, (e, b) in enumerate(_payloads(todo, dest)):
        if i in dupes:
            continue
        if str(dest.get("work_package", "none")) in ("ask", "from_refs") and not b["workPackage"]:
            sys.exit(f"error: openproject: entry {e['date']} needs fields['{WP_FIELD}'] (work package id)")
        links = {"project": {"href": proj_href}}
        if b["activity"] != "(default)":
            links["activity"] = {"href": acts[str(b["activity"]).lower()]}
        if b["workPackage"]:
            links[wp_key] = {"href": f"/api/v3/work_packages/{b['workPackage']}"}
        body = {"hours": b["hours"], "spentOn": b["spentOn"], "comment": {"raw": b["comment"]}, "_links": links}
        res = op.req("POST", "/api/v3/time_entries", data=json.dumps(body))
        created.append(res["id"])
    return (f"logged {len(created)} time entries in '{proj['name']}' (OpenProject {version}); "
            + (f"appended next to {len(existing)} existing, {len(dupes)} duplicates skipped" if append
               else f"replaced {len(existing)}") + f" — {op.base}/projects/{proj['identifier']}/cost_reports")
