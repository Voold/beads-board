#!/usr/bin/env python3
"""Локальный просмотр Beads без команд изменения задач."""

import argparse
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


def read_live(project_dir):
    env = os.environ.copy()
    env["BEADS_DIR"] = str(project_dir / ".beads")
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


def load_project(config_path):
    slug = config_path.parent.name
    config = project_config(config_path)
    project = {
        "slug": slug,
        "root": config.get("root", ""),
        "prefix": config.get("prefix", ""),
        "issues": [],
        "source": "live",
        "updated_at": now_iso(),
        "error": None,
    }
    try:
        project["issues"] = read_live(config_path.parent)
    except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError) as live_error:
        try:
            project["issues"], project["updated_at"] = read_snapshot(slug)
            project["source"] = "snapshot"
            project["error"] = f"Beads недоступен: {live_error}"
        except (OSError, ValueError, json.JSONDecodeError) as snapshot_error:
            project["source"] = "error"
            project["error"] = f"Не удалось прочитать Beads и снимок: {snapshot_error}"
    return project


def load_projects():
    configs = sorted((BEADS_HOME / "projects").glob("*/project.conf"))
    with ThreadPoolExecutor(max_workers=min(4, max(1, len(configs)))) as executor:
        projects = list(executor.map(load_project, configs))
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
    parser = argparse.ArgumentParser(description="Локальный read-only интерфейс Beads")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
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
