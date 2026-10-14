#!/usr/bin/env python3
"""Локальный просмотр Beads без команд изменения задач."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


HERE = Path(__file__).resolve().parent
HTML = HERE / "index.html"
BEADS_HOME = Path(os.environ.get("BEADS_HOME", Path.home() / "beads")).expanduser()
BD_BIN = os.environ.get("BD_BIN") or shutil.which("bd") or "/opt/homebrew/bin/bd"
LOCAL_PROJECTS = HERE / "projects.local.txt"
CLI_PROJECTS = ()
ISSUE_FIELDS = (
    "id",
    "title",
    "description",
    "acceptance_criteria",
    "status",
    "priority",
    "issue_type",
    "assignee",
    "updated_at",
    "closed_at",
    "close_reason",
    "parent",
    "dependencies",
)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def project_config(path):
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key] = value
    return values


def normalize_issue(issue):
    return {field: issue.get(field) for field in ISSUE_FIELDS}


def read_snapshot(slug):
    path = BEADS_HOME / "history" / slug / "issues.jsonl"
    issues = []
    with path.open(encoding="utf-8") as source:
        for line in source:
            if line.strip():
                issue = json.loads(line)
                if issue.get("_type") == "issue":
                    issues.append(normalize_issue(issue))
    return issues, datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()


def read_live(project_dir, beads_dir=None):
    env = os.environ.copy()
    if beads_dir:
        env["BEADS_DIR"] = str(beads_dir)
    else:
        env.pop("BEADS_DIR", None)
    env["DOLT_DISABLE_EVENT_FLUSH"] = "1"
    result = subprocess.run(
        [BD_BIN, "--readonly", "list", "--all", "--json", "--limit", "0"],
        cwd=project_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    issues = json.loads(result.stdout)
    if not isinstance(issues, list):
        raise ValueError("Beads вернул неожиданный формат")
    return [normalize_issue(issue) for issue in issues]


def configured_paths():
    paths = list(CLI_PROJECTS)
    try:
        lines = LOCAL_PROJECTS.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        lines = []
    for line in lines:
        value = line.strip()
        if value and not value.startswith("#"):
            path = Path(value).expanduser()
            paths.append((LOCAL_PROJECTS.parent / path).resolve() if not path.is_absolute() else path.resolve())
    return paths


def project_sources():
    sources = []
    seen_roots = set()
    used_slugs = set()
    for config_path in sorted((BEADS_HOME / "projects").glob("*/project.conf")):
        config = project_config(config_path)
        slug = config_path.parent.name
        root = config.get("root", "")
        sources.append({"slug": slug, "root": root, "workdir": config_path.parent, "beads_dir": config_path.parent / ".beads", "snapshot_slug": slug, "prefix": config.get("prefix", "")})
        used_slugs.add(slug)
        if root:
            seen_roots.add(Path(root).expanduser().resolve())
    for configured in configured_paths():
        path = configured.expanduser().resolve()
        workdir = path.parent if path.name == ".beads" else path
        if workdir in seen_roots:
            continue
        seen_roots.add(workdir)
        slug = workdir.name or "project"
        if slug in used_slugs:
            slug += "-" + hashlib.sha1(str(workdir).encode()).hexdigest()[:6]
        used_slugs.add(slug)
        sources.append({"slug": slug, "root": str(workdir), "workdir": workdir, "beads_dir": path if path.name == ".beads" else None, "snapshot_slug": None, "prefix": ""})
    return sources


def load_project(source):
    project = {
        "slug": source["slug"],
        "root": source["root"],
        "prefix": source["prefix"],
        "issues": [],
        "source": "live",
        "updated_at": now_iso(),
        "error": None,
    }
    try:
        workdir = source["workdir"]
        if not (workdir / ".beads").exists():
            raise FileNotFoundError(f"В каталоге {workdir} нет .beads")
        project["issues"] = read_live(workdir, source["beads_dir"])
    except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError) as live_error:
        if source["snapshot_slug"] is None:
            project["source"] = "error"
            project["error"] = f"Не удалось прочитать Beads: {live_error}"
        else:
            try:
                project["issues"], project["updated_at"] = read_snapshot(source["snapshot_slug"])
                project["source"] = "snapshot"
                project["error"] = f"Beads недоступен: {live_error}"
            except (OSError, ValueError, json.JSONDecodeError) as snapshot_error:
                project["source"] = "error"
                project["error"] = f"Не удалось прочитать Beads и снимок: {snapshot_error}"
    return project


def load_projects():
    sources = project_sources()
    with ThreadPoolExecutor(max_workers=min(4, max(1, len(sources)))) as executor:
        projects = list(executor.map(load_project, sources))
    return {"fetched_at": now_iso(), "projects": projects}


class Handler(BaseHTTPRequestHandler):
    def send_content(self, content, content_type, status=200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(content)

    def respond(self):
        if self.headers.get("Host", "").split(":", 1)[0] not in ("127.0.0.1", "localhost"):
            self.send_content(b"Forbidden", "text/plain; charset=utf-8", 403)
            return
        path = urlsplit(self.path).path
        if path in ("/", "/index.html"):
            self.send_content(HTML.read_bytes(), "text/html; charset=utf-8")
        elif path == "/api/projects":
            data = json.dumps(load_projects(), ensure_ascii=False).encode("utf-8")
            self.send_content(data, "application/json; charset=utf-8")
        else:
            self.send_content(b"Not found", "text/plain; charset=utf-8", 404)

    def do_GET(self):
        self.respond()

    def do_HEAD(self):
        self.respond()

    def do_POST(self):
        self.send_content(b"Read only", "text/plain; charset=utf-8", 405)

    def log_message(self, format_string, *args):
        print("[%s] %s" % (self.log_date_time_string(), format_string % args), file=sys.stderr)


def main():
    global CLI_PROJECTS
    parser = argparse.ArgumentParser(description="Локальный read-only интерфейс Beads")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--project", action="append", default=[], metavar="PATH", help="папка проекта Beads; можно указать несколько раз")
    args = parser.parse_args()
    CLI_PROJECTS = tuple(Path(path).expanduser().resolve() for path in args.project)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    except OSError as error:
        if args.port != 8765:
            parser.error(f"не удалось занять порт {args.port}: {error}")
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    url = "http://127.0.0.1:%s/" % server.server_port
    print("Beads tracker: %s" % url, flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
