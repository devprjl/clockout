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
        if a.id:
            try:
                w = op.req("GET", f"/api/v3/work_packages/{a.id.lstrip('#OP')}")
                same = w["_links"]["project"]["href"].rstrip("/").split("/")[-1] == str(proj["id"])
                out = {"match": "id" if same else "other_project", "id": w["id"], "subject": w["subject"],
                       "project": w["_links"]["project"]["title"]}
            except SystemExit as e:
                out = {"match": "none", "error": str(e)}
        else:
            name = a.name.strip().strip('"').lower()
            filters = [{"subjectOrId": {"operator": "**", "values": [a.name.strip()]}}]
            res = op.req("GET", f"/api/v3/projects/{proj['id']}/work_packages",
                         params={"filters": json.dumps(filters), "pageSize": 50})
            els = [{"id": w["id"], "subject": w["subject"], "status": w["_links"]["status"]["title"]}
                   for w in res["_embedded"]["elements"]]
            exact = [w for w in els if w["subject"].strip().lower() == name]
            out = ({"match": "exact", **exact[0]} if len(exact) == 1 else
                   {"match": "contains", **els[0]} if len(els) == 1 else
                   {"match": "ambiguous" if els else "none", "candidates": els})
    else:
        dates = [d.isoformat() for d in parse_range(a.date, config.settings(a.project))]
        out = [{"id": e["id"], "date": e["spentOn"], "hours": e["hours"],
                "work_package": (e["_links"].get("entity") or e["_links"].get("workPackage") or {}).get("title"),
                "activity": e["_links"]["activity"].get("title"), "comment": e["comment"]["raw"]}
               for e in op.my_entries(proj["id"], dates)]
    print(json.dumps({"project": proj["identifier"], a.cmd: out}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
