"""Render entries into a project's timesheet columns.

A layout is a list of columns, each {header, value, format?, default?}:
  value:  person | date | task | hours | remarks | project | refs | ticket | work_package | blank | ask | const:<text>
  format: dates  -> tokens YYYY YY MMMM MMM MM M DD D dddd ddd   e.g. "MMM d", "DD/MM/YYYY", "YYYY-MM-DD"
          hours  -> number | h:mm | ~number | minutes
          refs   -> lines | comma
`entry["fields"][header]` always wins (answers to `ask` columns, or manual overrides).
"""
from __future__ import annotations

import re
from datetime import date

_DATE_TOKENS = re.compile(r"YYYY|YY|MMMM|MMM|MM|M|DD|D|dddd|ddd|d")


def format_date(iso: str, fmt: str | None) -> str:
    d = date.fromisoformat(iso)
    if not fmt:
        return iso
    fmt = fmt.replace("d", "D") if not re.search(r"ddd", fmt) else fmt  # "MMM d" == "MMM D"
    table = {
        "YYYY": f"{d.year}", "YY": f"{d.year % 100:02d}",
        "MMMM": d.strftime("%B"), "MMM": d.strftime("%b"), "MM": f"{d.month:02d}", "M": f"{d.month}",
        "DD": f"{d.day:02d}", "D": f"{d.day}", "dddd": d.strftime("%A"), "ddd": d.strftime("%a"),
    }
    return _DATE_TOKENS.sub(lambda m: table.get(m.group(0), m.group(0)), fmt)


def format_hours(h, fmt: str | None) -> str:
    if h in ("", None):
        return ""
    h = float(h)
    fmt = (fmt or "number").lower()
    if fmt == "h:mm":
        mins = round(h * 60)
        return f"{mins // 60}:{mins % 60:02d}"
    if fmt == "minutes":
        return f"{round(h * 60)}"
    if fmt == "~number":
        return f"~{h:g}"
    return f"{h:g}"


def _ticket(refs: list[str]) -> str:
    for r in refs:
        m = re.search(r"/-/(issues|work_items|merge_requests)/(\d+)", r)
        if m:
            return ("#" if m.group(1) != "merge_requests" else "!") + m.group(2)
    return ""


def cell(entry: dict, col: dict) -> str:
    header, value, fmt = col["header"], str(col.get("value", "blank")), col.get("format")
    if header in entry.get("fields", {}):
        return str(entry["fields"][header])
    if value.startswith("const:"):
        return value[len("const:"):]
    if value == "date":
        return format_date(entry["date"], fmt)
    if value == "hours":
        return format_hours(entry.get("hours", ""), fmt)
    if value == "refs":
        sep = ", " if (fmt or "lines") == "comma" else "\n"
        return sep.join(entry.get("refs", []))
    if value == "ticket":
        return _ticket(entry.get("refs", []))
    if value in ("person", "task", "remarks", "project"):
        return str(entry.get(value, "") or "")
    if value == "work_package":  # set while drafting from OP#123 / "Ref: <name>"; empty = destination's default bucket
        return str(entry.get("fields", {}).get("Work package", "") or col.get("default", ""))
    if value == "ask":
        return str(col.get("default", ""))
    return ""  # blank


def headers(layout: dict) -> list[str]:
    return [c["header"] for c in layout["columns"]]


def render(entries: list[dict], layout: dict) -> list[list[str]]:
    """One row per entry (per_task layouts are drafted as one entry per task)."""
    return [[cell(e, c) for c in layout["columns"]] for e in entries]


def missing_asks(entries: list[dict], layout: dict) -> list[str]:
    """`ask` columns without an answer or default — the skill must ask before exporting."""
    out = []
    for c in layout["columns"]:
        if c.get("value") == "ask" and "default" not in c:
            for e in entries:
                if e.get("kind") != "empty" and c["header"] not in e.get("fields", {}):
                    out.append(f"{e['date']}: {c['header']}")
    return out


# ---------------------------------------------------------------- spreadsheet typing
def sheets_date_pattern(fmt: str | None) -> str:
    """Our date tokens -> a Google Sheets number-format pattern."""
    fmt = fmt or "YYYY-MM-DD"
    fmt = fmt.replace("d", "D") if "ddd" not in fmt else fmt
    table = {"YYYY": "yyyy", "YY": "yy", "MMMM": "mmmm", "MMM": "mmm", "MM": "mm", "M": "m",
             "DD": "dd", "D": "d", "dddd": "dddd", "ddd": "ddd"}
    return _DATE_TOKENS.sub(lambda m: table.get(m.group(0), m.group(0)), fmt)


def column_kinds(layout: dict) -> list[dict]:
    """Per column: {"kind": "date", "pattern"} | {"kind": "number"} | {"kind": "text"}."""
    out = []
    for c in layout["columns"]:
        v, fmt = c.get("value"), (c.get("format") or "")
        if v == "date":
            out.append({"kind": "date", "pattern": sheets_date_pattern(fmt)})
        elif v == "hours" and fmt.lower() in ("", "number"):
            out.append({"kind": "number"})
        else:
            out.append({"kind": "text"})
    return out


def typed_rows(entries: list[dict], layout: dict) -> list[list]:
    """Like render(), but date cells are ISO strings and numeric hours are numbers/''."""
    kinds = column_kinds(layout)
    rows = []
    for e in entries:
        row = []
        for c, k in zip(layout["columns"], kinds):
            if c["header"] in e.get("fields", {}):
                row.append(str(e["fields"][c["header"]]))
            elif k["kind"] == "date":
                row.append(e["date"])
            elif k["kind"] == "number":
                row.append("" if e.get("hours") in ("", None) else float(e["hours"]))
            else:
                row.append(cell(e, c))
        rows.append(row)
    return rows


def key_columns(layout: dict) -> tuple[int | None, int | None]:
    """Indices of the person and date columns (used to find a person's existing rows)."""
    vals = [c.get("value") for c in layout["columns"]]
    return (vals.index("person") if "person" in vals else None,
            vals.index("date") if "date" in vals else None)
