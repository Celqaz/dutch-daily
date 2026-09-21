"""Tiny zero-dependency OPDS server for the daily documents.

KOReader can browse OPDS catalogs, so with this running on the Raspberry Pi the
reader pulls each day's EPUB over Wi-Fi - no Amazon, no USB cable:

    python -m app.opds --dir output --port 8080
    # KOReader: Cloud storage -> OPDS catalog -> http://<pi>:8080/opds

Routes:
    /opds            acquisition feed (Atom/OPDS 1.2), newest first
    /files/<name>    the .epub / .html files themselves

Optional HTTP Basic auth via --user/--password (or OPDS_USER/OPDS_PASSWORD),
handy when the Pi is reachable from outside your LAN.
"""
from __future__ import annotations

import argparse
import base64
import html
import os
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlparse

FEED_TYPE = "application/atom+xml;profile=opds-catalog;kind=acquisition"
DEFAULT_PORT = 8080
DEFAULT_DIR = "output"

_MEDIA_TYPES = {
    ".epub": "application/epub+zip",
    ".html": "text/html; charset=utf-8",
    ".pdf": "application/pdf",
}

# Which format is offered as the download when a document exists in several.
_PREFERRED = (".epub", ".pdf", ".html")


def _esc(value: object) -> str:
    return html.escape("" if value is None else str(value))


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _rank(path: Path) -> int:
    try:
        return _PREFERRED.index(path.suffix.lower())
    except ValueError:
        return len(_PREFERRED)


def list_documents(directory: Path) -> list[list[Path]]:
    """Documents in the directory, newest first, one group per name.

    A document can exist as .epub and .html; the group keeps them together so the
    feed has a single entry per day with the EPUB as the download link.
    """
    groups: dict[str, list[Path]] = {}
    for path in directory.glob("*"):
        if path.is_file() and path.suffix.lower() in _MEDIA_TYPES:
            groups.setdefault(path.stem, []).append(path)
    ordered = [sorted(paths, key=_rank) for paths in groups.values()]
    ordered.sort(key=lambda paths: max(p.stat().st_mtime for p in paths), reverse=True)
    return ordered


def _entry(paths: list[Path], base_url: str) -> str:
    """Atom entry for one document (first path is the download, rest alternates)."""
    primary, *others = paths
    stat = primary.stat()
    updated = _iso(datetime.fromtimestamp(max(p.stat().st_mtime for p in paths)))
    url = f"{base_url}/files/{quote(primary.name)}"
    links = [
        f'    <link rel="http://opds-spec.org/acquisition" href="{_esc(url)}" '
        f'type="{_MEDIA_TYPES[primary.suffix.lower()]}" length="{stat.st_size}"/>'
    ]
    for other in others:
        links.append(
            f'    <link rel="alternate" href="{_esc(f"{base_url}/files/{quote(other.name)}")}" '
            f'type="{_MEDIA_TYPES[other.suffix.lower()]}" length="{other.stat().st_size}"/>'
        )
    body = "\n".join(links)
    return f"""  <entry>
    <title>{_esc(primary.stem)}</title>
    <id>urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, f"{base_url}/files/{primary.stem}")}</id>
    <updated>{updated}</updated>
    <author><name>Language Daily</name></author>
    <content type="text">Daily beginner lesson (Dutch + Japanese): vocabulary,
    grammar, word building and a paragraph-by-paragraph translation.</content>
{body}
  </entry>"""


def catalog(directory: Path, base_url: str) -> str:
    """The OPDS acquisition feed as an XML string."""
    documents = list_documents(directory)
    updated = (
        _iso(
            datetime.fromtimestamp(
                max(p.stat().st_mtime for p in documents[0])
            )
        )
        if documents
        else _iso(datetime.now())
    )
    entries = "\n".join(_entry(paths, base_url.rstrip("/")) for paths in documents)
    return f"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:opds="http://opds-spec.org/2010/catalog">
  <id>urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, "language-daily-opds")}</id>
  <title>Language Daily</title>
  <updated>{updated}</updated>
  <author><name>Language Daily</name></author>
  <link rel="self" href="{_esc(base_url.rstrip('/'))}/opds" type="{FEED_TYPE}"/>
  <link rel="start" href="{_esc(base_url.rstrip('/'))}/opds" type="{FEED_TYPE}"/>
  <link rel="http://opds-spec.org/sort/new" href="{_esc(base_url.rstrip('/'))}/opds" type="{FEED_TYPE}"/>
{entries}
</feed>
"""


def _make_handler(directory: Path, base_url: str, credentials: tuple[str, str] | None):
    root = directory.resolve()

    class Handler(BaseHTTPRequestHandler):
        server_version = "LanguageDailyOPDS/1.0"

        def _authorised(self) -> bool:
            if credentials is None:
                return True
            header = self.headers.get("Authorization", "")
            if not header.startswith("Basic "):
                return False
            try:
                decoded = base64.b64decode(header[6:]).decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                return False
            user, _, password = decoded.partition(":")
            return (user, password) == credentials

        def _deny(self) -> None:
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="Language Daily"')
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _base_url(self) -> str:
            if base_url:
                return base_url.rstrip("/")
            host = self.headers.get("Host") or f"localhost:{self.server.server_port}"
            return f"http://{host}"

        def do_GET(self) -> None:  # noqa: N802 - http.server API
            path = urlparse(self.path).path
            if not self._authorised():
                self._deny()
                return
            if path in ("/", "/opds", "/opds/"):
                payload = catalog(directory, self._base_url()).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", FEED_TYPE)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            if path.startswith("/files/"):
                name = path[len("/files/") :]
                target = (root / name).resolve()
                if (
                    not name
                    or target.parent != root
                    or not target.is_file()
                    or target.suffix.lower() not in _MEDIA_TYPES
                ):
                    self.send_error(404, "File not found")
                    return
                payload = target.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", _MEDIA_TYPES[target.suffix.lower()])
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Last-Modified", self.date_time_string(target.stat().st_mtime))
                self.end_headers()
                self.wfile.write(payload)
                return
            self.send_error(404, "Not found")

        def log_message(self, fmt: str, *args) -> None:
            print(f"[opds] {self.address_string()} {fmt % args}", flush=True)

    return Handler


def build_server(
    directory: Path,
    *,
    host: str = "0.0.0.0",
    port: int = DEFAULT_PORT,
    base_url: str = "",
    credentials: tuple[str, str] | None = None,
) -> ThreadingHTTPServer:
    handler = _make_handler(directory, base_url, credentials)
    return ThreadingHTTPServer((host, port), handler)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="app.opds", description=__doc__)
    parser.add_argument(
        "--dir",
        default=os.environ.get("OPDS_DIR", DEFAULT_DIR),
        help=f"directory that holds the daily files (default: {DEFAULT_DIR})",
    )
    parser.add_argument(
        "--host", default=os.environ.get("OPDS_HOST", "0.0.0.0"), help="bind address"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("OPDS_PORT", DEFAULT_PORT)),
        help=f"TCP port (default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("OPDS_BASE_URL", ""),
        help="public URL of this server (default: derived from the request Host header)",
    )
    parser.add_argument("--user", default=os.environ.get("OPDS_USER", ""), help="basic auth user")
    parser.add_argument(
        "--password", default=os.environ.get("OPDS_PASSWORD", ""), help="basic auth password"
    )
    args = parser.parse_args(argv)

    directory = Path(args.dir)
    if not directory.is_dir():
        raise SystemExit(f"Error: {directory} is not a directory.")
    credentials = (args.user, args.password) if args.user else None

    server = build_server(
        directory,
        host=args.host,
        port=args.port,
        base_url=args.base_url,
        credentials=credentials,
    )
    host, port = server.server_address[:2]
    count = len(list_documents(directory))
    print(
        f"[opds] serving {count} document(s) from {directory} on http://{host}:{port}/opds",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[opds] stopping.", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
