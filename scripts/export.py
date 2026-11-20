"""Send approved entries to each project's destinations, in each project's layout.

  export.py --entries .cache/entries-2026-11-20.json             # each project's own destinations
  export.py --entries FILE --to clipboard                        # override destination type(s)
  export.py --entries FILE --replace-date                        # let sheet/API destinations replace same-day rows
  export.py --entries FILE --preview                             # print rows per project, export nothing
  export.py --entries FILE --project acme-web                    # one project only (e.g. clipboard, one at a time)
  export.py --list                                               # available destination types
"""
import argparse
import sys
from collections import OrderedDict

import config
import layout as L
import sinks
from common import load_entries
from sinks.tsv import render as render_tsv


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--entries")
    ap.add_argument("--to", help="comma-separated destination types, overriding the projects' own")
    ap.add_argument("--replace-date", action="store_true")
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--project", help="only export entries of this project slug")
    ap.add_argument("--list", action="store_true")
    opts = ap.parse_args()

    if opts.list:
        for name in sinks.AVAILABLE:
            print(f"{name:14} {sinks.get(name).DESCRIPTION}")
        return
    if not opts.entries:
        ap.error("--entries is required")

    groups: "OrderedDict[str, list]" = OrderedDict()
    for e in load_entries(opts.entries):
        if not opts.project or e["profile"] == opts.project:
            groups.setdefault(e["profile"], []).append(e)

    failed = False
    for slug, entries in groups.items():
        proj = config.project(slug)
        lay = proj["layout"]
        missing = L.missing_asks(entries, lay)
        if missing:
            sys.exit(f"error: {slug}: unanswered columns: {', '.join(missing)}")
        if opts.preview:
            print(f"## {proj['name']} ({slug})\n" + render_tsv(entries, lay, with_header=True))
            continue
        dests = ([{"type": t.strip()} for t in opts.to.split(",")] if opts.to else proj["destinations"])
        # clipboard holds one thing at a time: with several projects, each overwrites the last
        for d in dests:
            if len(groups) > 1 and d.get("type") == "clipboard":
                print(f"note: several projects in this export; the clipboard keeps only the last one — "
                      f"use --project <slug> to copy one project at a time")
            try:
                print(f"{slug} → {d['type']}: {sinks.get(d['type']).export(entries, d, lay, opts)}")
            except SystemExit as ex:
                failed = True
                print(f"{slug} → {d.get('type')}: {ex}", file=sys.stderr)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
