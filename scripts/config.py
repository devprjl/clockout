"""clockout configuration: personal defaults + per-project profiles, kept outside the repo.

  $CLOCKOUT_HOME (default ~/.config/clockout)/
    config.yaml            personal defaults for every project
    secrets.env            KEY=value tokens, referenced by name (token_env) from the YAML
    projects/<slug>.yaml   one per project: match rules, overrides, meetings, layout, destinations
    google/                Google OAuth client/tokens (only for Google exporters/Calendar)

CLI:
  config.py paths                 where things live
  config.py list                  configured projects
  config.py detect [--cwd DIR]    which project the directory belongs to
  config.py show [--project S]    effective settings (secrets never printed)
  config.py validate              check config, layouts and destinations
"""
from __future__ import annotations

import argparse
import copy
import fnmatch
import json
import os
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

import yaml

HOME = Path(os.environ.get("CLOCKOUT_HOME", "~/.config/clockout")).expanduser()
PERSONAL = HOME / "config.yaml"
SECRETS = HOME / "secrets.env"
PROJECTS = HOME / "projects"
GOOGLE = HOME / "google"

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
VALUES = {"person", "date", "task", "hours", "remarks", "project", "refs", "ticket", "work_package", "blank", "ask"}

DEFAULTS: dict = {
    "person": "",
    "timezone": "UTC",
    "workdays": ["mon", "tue", "wed", "thu", "fri"],
    "work_hours": {"start": "09:00", "end": "18:00", "max_focus": 7},
    "hours_step": 0.5,
    "holidays": [],
    "style": "short, human, past-tense bullets; 3-8 words each",
    "sources": {
        "gitlab": [],                      # [{url, token_env}]
        "git": {"emails": [], "repos": []},
        "calendar": {"enabled": False, "calendar_id": "primary",
                     "dsm_pattern": r"(?i)\bdsm\b|stand-?up|daily sync|daily scrum"},
    },
    "ignore": {"gitlab_projects": [], "paths": []},
}

DEFAULT_LAYOUT = {
    "granularity": "per_day",
    "columns": [
        {"header": "Person", "value": "person"},
        {"header": "Date", "value": "date", "format": "MMM d"},
        {"header": "Task Description", "value": "task"},
        {"header": "Hours Spent", "value": "hours", "format": "number"},
        {"header": "Remarks", "value": "remarks"},
    ],
}


class ConfigError(SystemExit):
    pass


def deep_merge(base: dict, over: dict | None) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else copy.deepcopy(v)
    return out


def _read_yaml(path: Path) -> dict:
    try:
        return yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"error: {path} is not valid YAML: {e}")


# ---------------------------------------------------------------- secrets
def load_secrets() -> None:
    """Load secrets.env into the environment (existing env vars win)."""
    if not SECRETS.exists():
        return
    for line in SECRETS.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def secret(name: str | None) -> str:
    return os.environ.get(name or "", "") if name else ""


load_secrets()


# ---------------------------------------------------------------- loading
@lru_cache(maxsize=None)
def personal() -> dict:
    return deep_merge(DEFAULTS, _read_yaml(PERSONAL) if PERSONAL.exists() else {})


@lru_cache(maxsize=None)
def projects() -> dict[str, dict]:
    out = {}
    if PROJECTS.exists():
        for f in sorted(PROJECTS.glob("*.yaml")):
            p = _read_yaml(f)
            p.setdefault("name", f.stem)
            p.setdefault("match", {})
            p.setdefault("overrides", {})
            p.setdefault("meetings", {"recurring": [], "one_off": []})
            p["layout"] = deep_merge(DEFAULT_LAYOUT, {}) if not p.get("layout") else p["layout"]
            p["layout"].setdefault("granularity", "per_day")
            p.setdefault("destinations", [{"type": "clipboard"}])
            p["_file"] = str(f)
            out[f.stem] = p
    return out


def project(slug: str) -> dict:
    ps = projects()
    if slug not in ps:
        raise ConfigError(f"error: no project '{slug}' (have: {', '.join(ps) or 'none'}); run /clockout setup")
    return ps[slug]


def settings(slug: str | None = None) -> dict:
    """Personal defaults with the project's overrides applied."""
    s = personal()
    return deep_merge(s, project(slug)["overrides"]) if slug else s


def workday_numbers(s: dict) -> set[int]:
    return {WEEKDAYS.index(str(d).lower()[:3]) for d in s.get("workdays", [])}


def expand(p: str) -> str:
    return str(Path(os.path.expanduser(p)).resolve())


# ---------------------------------------------------------------- matching
def git_remotes(path: str) -> list[str]:
    out = subprocess.run(["git", "-C", path, "remote", "-v"], capture_output=True, text=True)
    return sorted({ln.split()[1] for ln in out.stdout.splitlines() if len(ln.split()) >= 2})


def git_toplevel(path: str) -> str | None:
    out = subprocess.run(["git", "-C", path, "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    return out.stdout.strip() or None


def _glob_any(value: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(value.lower(), p.lower()) for p in patterns or [])


_CASE_INSENSITIVE_FS = sys.platform in ("darwin", "win32")  # default macOS/Windows file systems


def _norm(path: str) -> str:
    path = os.path.realpath(expand(path))
    return path.lower() if _CASE_INSENSITIVE_FS else path


def _under_any(path: str, roots: list[str]) -> bool:
    path = _norm(path)
    return any(path == _norm(r) or path.startswith(_norm(r) + os.sep) for r in roots or [])


def match(*, gitlab_project: str = "", repo_path: str = "", remotes: list[str] | None = None) -> str | None:
    """Return the project slug that owns this activity/location, or None."""
    for slug, p in projects().items():
        m = p["match"]
        if gitlab_project and _glob_any(gitlab_project, m.get("gitlab_projects", [])):
            return slug
        if repo_path and _under_any(repo_path, m.get("paths", [])):
            return slug
        if any(_glob_any(r, m.get("remotes", [])) for r in (remotes or [])):
            return slug
    return None


def ignored(*, gitlab_project: str = "", repo_path: str = "") -> bool:
    ig = personal()["ignore"]
    return bool((gitlab_project and _glob_any(gitlab_project, ig.get("gitlab_projects", [])))
                or (repo_path and _under_any(repo_path, ig.get("paths", []))))


def detect(cwd: str) -> dict:
    top = git_toplevel(cwd) or cwd
    remotes = git_remotes(top) if git_toplevel(cwd) else []
    return {"cwd": cwd, "repo": top if remotes or git_toplevel(cwd) else None, "remotes": remotes,
            "project": match(repo_path=top, remotes=remotes)}


def git_repos() -> list[str]:
    """Local repos to scan: personal sources.git.repos + every project's match.paths."""
    roots = list(personal()["sources"]["git"].get("repos", []))
    for p in projects().values():
        roots += p["match"].get("paths", [])
    seen, out = set(), []
    for r in roots:
        r = expand(r)
        if _norm(r) not in seen and (Path(r) / ".git").exists():
            seen.add(_norm(r))
            out.append(r)
    return out


# ---------------------------------------------------------------- validation
def validate() -> list[str]:
    import sinks  # local import: sinks import config

    problems = []
    s = personal()
    if not PERSONAL.exists():
        problems.append(f"missing {PERSONAL} (run /clockout setup)")
    if not s.get("person"):
        problems.append("config.yaml: 'person' is empty")
    for d in s.get("workdays", []):
        if str(d).lower()[:3] not in WEEKDAYS:
            problems.append(f"config.yaml: unknown workday {d!r}")
    for g in s["sources"].get("gitlab", []):
        if not g.get("url"):
            problems.append("config.yaml: a sources.gitlab entry has no url")
        if not secret(g.get("token_env")):
            problems.append(f"secrets.env: {g.get('token_env')} (GitLab token for {g.get('url')}) is not set")
    if not projects():
        problems.append(f"no projects in {PROJECTS} (run /clockout setup in a project repo)")
    for slug, p in projects().items():
        cols = p["layout"].get("columns") or []
        if not cols:
            problems.append(f"{slug}: layout has no columns")
        for c in cols:
            v = str(c.get("value", ""))
            if not c.get("header"):
                problems.append(f"{slug}: a layout column has no header")
            if v not in VALUES and not v.startswith("const:"):
                problems.append(f"{slug}: column {c.get('header')!r} has unknown value {v!r}")
        if p["layout"]["granularity"] not in ("per_day", "per_task", "per_work_package"):
            problems.append(f"{slug}: granularity must be per_day, per_task or per_work_package")
        import re as _re
        for key in ("id_patterns", "name_patterns"):
            for pat in (p.get("work_refs") or {}).get(key, []):
                try:
                    _re.compile(pat)
                except _re.error as e:
                    problems.append(f"{slug}: work_refs.{key} {pat!r} is not a valid regex: {e}")
        for d in p["destinations"]:
            try:
                problems += [f"{slug}: {msg}" for msg in sinks.get(d.get("type", "")).check(d)]
            except SystemExit as e:
                problems.append(f"{slug}: {e}")
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["paths", "list", "detect", "show", "validate"])
    ap.add_argument("--cwd", default=os.getcwd())
    ap.add_argument("--project")
    a = ap.parse_args()

    if a.cmd == "paths":
        print(json.dumps({"home": str(HOME), "config": str(PERSONAL), "secrets": str(SECRETS),
                          "projects": str(PROJECTS), "google": str(GOOGLE),
                          "exists": PERSONAL.exists()}, indent=2))
    elif a.cmd == "list":
        print(json.dumps({slug: {"name": p["name"], "match": p["match"],
                                 "destinations": [d.get("type") for d in p["destinations"]],
                                 "columns": [c["header"] for c in p["layout"]["columns"]]}
                          for slug, p in projects().items()}, indent=2))
    elif a.cmd == "detect":
        print(json.dumps(detect(a.cwd), indent=2))
    elif a.cmd == "show":
        out = {"settings": settings(a.project)}
        if a.project:
            p = {k: v for k, v in project(a.project).items() if k != "overrides"}
            out["project"] = p
        print(json.dumps(out, indent=2, default=str))
    elif a.cmd == "validate":
        problems = validate()
        print(json.dumps({"ok": not problems, "problems": problems}, indent=2))
        sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
