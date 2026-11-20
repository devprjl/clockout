"""OpenProject lookups for a project's `openproject` destination (no MCP or Enterprise needed).

  op.py work-packages --project <slug> [--query text]     # pick a work package for `work_package: ask`
  op.py activities    --project <slug>                    # time entry activities you can use
  op.py logged        --project <slug> --date A[..B]      # what you already logged (to compare/verify)
  op.py resolve       --project <slug> --name "<text>"    # "Ref: <name>" → work package id (exact, then contains)
  op.py resolve       --project <slug> --id 123           # check an OP#123 reference exists in this project
  op.py delete        --project <slug> --id 8,9           # delete time entries — only your own, in this project
If the user's OpenProject also exposes its MCP server, Claude may use it for lookups instead;
time entries are always written by export.py (preview, duplicate guard, replace).
"""
import argparse
import json

import config
from common import parse_range
from config import secret
from sinks.openproject import OpenProject


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["work-packages", "activities", "logged", "resolve", "delete"])
    ap.add_argument("--name", default="")
    ap.add_argument("--id", default="")
    ap.add_argument("--project", required=True, help="clockout project slug")
    ap.add_argument("--query", default="")
    ap.add_argument("--date", default="today")
    a = ap.parse_args()

    dest = next((d for d in config.project(a.project)["destinations"] if d.get("type") == "openproject"), None)
    if not dest:
        raise SystemExit(f"error: {a.project} has no openproject destination")
    op = OpenProject(dest.get("url") or secret(dest.get("url_env")), secret(dest["token_env"]))
    proj = op.project(dest["project"])

    if a.cmd == "work-packages":
        filters = [{"status": {"operator": "o", "values": []}}]
        if a.query:
            filters.append({"subjectOrId": {"operator": "**", "values": [a.query]}})
        res = op.req("GET", f"/api/v3/projects/{proj['id']}/work_packages",
                     params={"filters": json.dumps(filters), "pageSize": 50})
        out = [{"id": w["id"], "subject": w["subject"], "status": w["_links"]["status"]["title"]}
               for w in res["_embedded"]["elements"]]
    elif a.cmd == "activities":
        wps = op.req("GET", f"/api/v3/projects/{proj['id']}/work_packages", params={"pageSize": 1})
        first = next((str(w["id"]) for w in wps["_embedded"]["elements"]), None)
        out = sorted(OpenProject.activities(op.schema(proj["_links"]["self"]["href"], first)))
    elif a.cmd == "delete":
        me = str(op.me()["id"])
        ids = [i.strip().lstrip("#") for i in a.id.split(",") if i.strip()]
        if not ids:
            raise SystemExit("error: delete needs --id <time entry id>[,<id>…] (see `op.py logged`)")
        out = []
        for i in ids:
            e = op.req("GET", f"/api/v3/time_entries/{i}")
            owner = e["_links"]["user"]["href"].rstrip("/").split("/")[-1]
            in_proj = e["_links"]["project"]["href"].rstrip("/").split("/")[-1] == str(proj["id"])
            if owner != me or not in_proj:
                out.append({"id": int(i), "deleted": False,
                            "reason": "not your entry" if owner != me else "entry is in another project"})
                continue
            op.req("DELETE", f"/api/v3/time_entries/{i}")
            out.append({"id": int(i), "deleted": True, "date": e["spentOn"], "hours": e["hours"],
                        "work_package": (e["_links"].get("entity") or e["_links"].get("workPackage") or {}).get("title")})
    elif a.cmd == "resolve":
        me = str(op.me()["id"])

        def describe(w: dict, match: str) -> dict:
            L = w["_links"]
            assignee = (L.get("assignee") or {})
            status = L["status"].get("title", "")
            closed = False
            try:
                closed = bool(op.req("GET", L["status"]["href"]).get("isClosed"))
            except SystemExit:
                pass
            d = {"match": match, "id": w["id"], "subject": w["subject"], "type": L["type"].get("title"),
                 "status": status, "closed": closed, "project": L["project"].get("title"),
                 "assignee": assignee.get("title"),
                 "assigned_to_you": (assignee.get("href") or "").rstrip("/").split("/")[-1] == me}
            flags = []
            if match == "other_project":
                flags.append(f"belongs to project {d['project']!r}, not {proj['identifier']!r}")
            if closed:
                flags.append(f"status is {status!r} (closed)")
            if d["assignee"] and not d["assigned_to_you"]:
                flags.append(f"assigned to {d['assignee']}")
            d["flags"] = flags
            return d

        if a.id:
            wid = a.id.strip().lstrip("#")
            wid = wid[2:] if wid[:2].upper() in ("OP", "PP") else wid
            wid = wid.lstrip("#").strip()
            r = op.s.get(f"{op.base}/api/v3/work_packages/{wid}", timeout=30)
            if r.status_code == 404:
                out = {"match": "not_found", "id": wid,
                       "flags": [f"OP#{wid} does not exist (or you can't see it) — pick another work package or the bucket"]}
            elif r.status_code == 403:
                out = {"match": "not_visible", "id": wid, "flags": [f"you can't access OP#{wid}"]}
            elif not r.ok:
                out = {"match": "error", "id": wid, "flags": [f"HTTP {r.status_code}"]}
            else:
                w = r.json()
                same = w["_links"]["project"]["href"].rstrip("/").split("/")[-1] == str(proj["id"])
                out = describe(w, "id" if same else "other_project")
        else:
            # "Ref:  improve ERROR messages. " → "improve ERROR messages"
            text = " ".join(a.name.split()).strip(" \t.,;:!?)]}\"'(")
            name = text.lower()
            filters = [{"subjectOrId": {"operator": "**", "values": [text]}},
                       {"status": {"operator": "*", "values": []}}]
            res = op.req("GET", f"/api/v3/projects/{proj['id']}/work_packages",
                         params={"filters": json.dumps(filters), "pageSize": 50})
            els = res["_embedded"]["elements"]
            exact = [w for w in els if " ".join(w["subject"].split()).lower() == name]
            if len(exact) == 1:
                out = describe(exact[0], "exact")
            elif len(els) == 1:
                out = describe(els[0], "contains")
            else:
                out = {"match": "ambiguous" if els else "none", "name": text,
                       "candidates": [{"id": w["id"], "subject": w["subject"],
                                       "status": w["_links"]["status"]["title"],
                                       "assignee": (w["_links"].get("assignee") or {}).get("title")} for w in els],
                       "flags": [f"{len(els)} work packages match {text!r} — ask which one" if els
                                 else f"no work package matches {text!r} — ask the user"]}
    else:
        dates = [d.isoformat() for d in parse_range(a.date, config.settings(a.project))]
        out = [{"id": e["id"], "date": e["spentOn"], "hours": e["hours"],
                "work_package": (e["_links"].get("entity") or e["_links"].get("workPackage") or {}).get("title"),
                "activity": e["_links"]["activity"].get("title"), "comment": e["comment"]["raw"]}
               for e in op.my_entries(proj["id"], dates)]
    print(json.dumps({"project": proj["identifier"], a.cmd: out}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
