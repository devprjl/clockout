"""Copy rows to the clipboard as both an HTML table and TSV.

Google Sheets / Excel use the HTML flavour: one row per entry, line breaks stay inside the
cell. Plain-text editors get the TSV flavour. Set `with_header: true` to include headers.
"""
import html
import platform
import shutil
import subprocess

import layout as L
from sinks.tsv import render as render_tsv

DESCRIPTION = "copy rows to the clipboard, ready to paste into Google Sheets/Excel"
REQUIRED: list[str] = []

_JXA = """
ObjC.import("AppKit");
function run(argv) {
  const pb = $.NSPasteboard.generalPasteboard;
  pb.clearContents;
  pb.setStringForType($(argv[0]), $("public.html"));
  pb.setStringForType($(argv[1]), $("public.utf8-plain-text"));
  return "ok";
}
"""


def render_html(entries, layout, with_header=False) -> str:
    def cell(v: str, tag="td") -> str:
        return f"<{tag}>" + "<br>".join(html.escape(x) for x in v.split("\n")) + f"</{tag}>"

    rows = L.render(entries, layout)
    head = "<tr>" + "".join(cell(h, "th") for h in L.headers(layout)) + "</tr>" if with_header else ""
    body = "".join("<tr>" + "".join(cell(v) for v in r) + "</tr>" for r in rows)
    return f'<meta charset="utf-8"><table>{head}{body}</table>'


def copy(htm: str, tsv: str) -> None:
    system = platform.system()
    if system == "Darwin":
        subprocess.run(["osascript", "-l", "JavaScript", "-e", _JXA, htm, tsv], check=True, capture_output=True)
    elif shutil.which("wl-copy"):
        subprocess.run(["wl-copy", "--type", "text/html"], input=htm.encode(), check=True)
    elif shutil.which("xclip"):
        subprocess.run(["xclip", "-selection", "clipboard", "-t", "text/html"], input=htm.encode(), check=True)
    elif system == "Windows":
        subprocess.run(["clip"], input=tsv.encode("utf-16"), check=True)
    else:
        raise SystemExit("error: no clipboard tool found (install wl-clipboard or xclip), or use the tsv destination")


def export(entries, dest, layout, opts) -> str:
    wh = dest.get("with_header", False)
    copy(render_html(entries, layout, wh), render_tsv(entries, layout, wh))
    first = L.headers(layout)[0]
    return f"copied {len(entries)} rows — click the first empty cell under '{first}' and paste"
