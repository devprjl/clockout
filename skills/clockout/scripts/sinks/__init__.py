"""Destinations ("sinks") for approved entries.

Each module defines:
    DESCRIPTION: str
    REQUIRED: list[str]                                  # keys the destination config must have
    def export(entries, dest, layout, opts) -> str       # returns a one-line summary
    def check(dest) -> list[str]                         # optional extra validation

`entries` come from common.load_entries() and all belong to one project; `dest` is that
project's destination block from projects/<slug>.yaml; `layout` is the project's layout.
To add a destination: add a module here and list it in AVAILABLE.
"""
import importlib

AVAILABLE = ["clipboard", "tsv", "gsheet_webapp", "gsheet_api", "openproject"]


def get(name: str):
    name = (name or "").replace("-", "_")
    if name not in AVAILABLE:
        raise SystemExit(f"unknown destination type {name!r}; available: {', '.join(AVAILABLE)}")
    mod = importlib.import_module(f"sinks.{name}")
    if not hasattr(mod, "check"):
        mod.check = lambda dest, _m=mod: [f"{dest.get('type')}: missing '{k}'"
                                          for k in getattr(_m, "REQUIRED", []) if not dest.get(k)]
    return mod
