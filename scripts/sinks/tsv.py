"""Print tab-separated rows (multi-line cells quoted) to stdout."""
import csv
import io

import layout as L

DESCRIPTION = "print paste-ready tab-separated rows"
REQUIRED: list[str] = []


def render(entries, layout, with_header=False) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter="\t", lineterminator="\n")
    if with_header:
        w.writerow(L.headers(layout))
    w.writerows(L.render(entries, layout))
    return buf.getvalue()


def export(entries, dest, layout, opts) -> str:
    print(render(entries, layout, dest.get("with_header", False)), end="")
    return f"printed {len(entries)} rows"
