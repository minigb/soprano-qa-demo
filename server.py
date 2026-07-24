#!/usr/bin/env python3
"""Soprano Studio -- dependency-free local web demo server.

Serves the frontend, audio from the source dataset, the demo's concatenated
sheet-music images and geometry, the alignment JSON needed for playback sync,
and the merged measure-aware RAG plus local-LLM question pipeline.

Usage:
    python3 server.py --host 127.0.0.1 --port 8765
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import re
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

from backend import dataset, qa  # noqa: E402

STATIC_ROOT = Path(__file__).resolve().parent / "static"

RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")


def _json_bytes(payload) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "SopranoStudio/1.0"

    def log_message(self, fmt, *args):  # quieter default logging
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # -- helpers ---------------------------------------------------------
    def _send_json(self, payload, status=200):
        body = _json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, status, message):
        self._send_json({"error": message}, status=status)

    def _send_file(self, path: Path, content_type: str):
        try:
            data = path.read_bytes()
        except OSError:
            self._send_error_json(404, "file not found")
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            # Browsers may cancel an image request while switching pieces.
            # The response is already complete from the server's perspective.
            return

    def _send_audio(self, path: Path):
        try:
            size = path.stat().st_size
        except OSError:
            self._send_error_json(404, "audio not found")
            return

        range_header = self.headers.get("Range")
        start, end = 0, size - 1
        status = 200
        if range_header:
            m = RANGE_RE.match(range_header)
            if m:
                status = 206
                if m.group(1):
                    start = int(m.group(1))
                if m.group(2):
                    end = int(m.group(2))
                else:
                    end = size - 1
                start = max(0, min(start, size - 1))
                end = max(start, min(end, size - 1))

        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", "audio/mpeg")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            remaining = length
            chunk_size = 1024 * 256
            while remaining > 0:
                chunk = f.read(min(chunk_size, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return
                remaining -= len(chunk)

    # -- routing -----------------------------------------------------------
    def do_GET(self):
        try:
            path = unquote(urlsplit(self.path).path)
            if path == "/":
                self._send_file(STATIC_ROOT / "index.html", "text/html; charset=utf-8")
                return
            if path.startswith("/static/"):
                self._serve_static(path[len("/static/") :])
                return
            if path == "/api/pieces":
                self._send_json({"pieces": dataset.list_pieces()})
                return
            if path == "/api/corpus_stats":
                self._send_json(qa.corpus_stats())
                return

            m = re.match(r"^/api/pieces/([a-z0-9-]+)/score$", path)
            if m:
                payload = dataset.score_payload(m.group(1))
                if payload is None:
                    self._send_error_json(404, "no score geometry for this piece yet")
                else:
                    self._send_json(payload)
                return

            m = re.match(r"^/api/pieces/([a-z0-9-]+)/alignment$", path)
            if m:
                self._send_json(dataset.alignment_payload(m.group(1)))
                return

            m = re.match(r"^/media/audio/([a-z0-9-]+)\.mp3$", path)
            if m:
                self._send_audio(dataset.audio_path(m.group(1)))
                return

            m = re.match(
                r"^/media/score/([a-z0-9-]+)/(Concatenated)\.png$", path
            )
            if m:
                self._send_file(
                    dataset.image_path(m.group(1), m.group(2)), "image/png"
                )
                return

            self._send_error_json(404, "not found")
        except Exception:
            traceback.print_exc()
            self._send_error_json(500, "internal error")

    def _serve_static(self, rel_path: str):
        rel_path = rel_path or "index.html"
        candidate = (STATIC_ROOT / rel_path).resolve()
        if STATIC_ROOT not in candidate.parents and candidate != STATIC_ROOT:
            self._send_error_json(403, "forbidden")
            return
        if not candidate.is_file():
            self._send_error_json(404, "not found")
            return
        content_type = mimetypes.guess_type(str(candidate))[0] or "application/octet-stream"
        self._send_file(candidate, content_type)

    def do_POST(self):
        try:
            path = urlsplit(self.path).path
            if path == "/api/ask":
                self._handle_ask()
                return
            self._send_error_json(404, "not found")
        except Exception:
            traceback.print_exc()
            self._send_error_json(500, "internal error")

    def _handle_ask(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_error_json(400, "invalid JSON body")
            return
        if not isinstance(body, dict):
            self._send_error_json(400, "request body must be a JSON object")
            return

        piece_id = body.get("piece_id")
        question_value = body.get("question")
        measure_range = body.get("measure_range")
        want_generation = body.get("generate", True)

        if piece_id not in dataset.PIECE_IDS:
            self._send_error_json(400, "unknown piece_id")
            return
        if not isinstance(question_value, str):
            self._send_error_json(400, "question must be a string")
            return
        question = question_value.strip()
        if not question:
            self._send_error_json(400, "question is required")
            return
        if not isinstance(want_generation, bool):
            self._send_error_json(400, "generate must be true or false")
            return

        piece_status = dataset.piece_status(piece_id)

        range_tuple = None
        if measure_range is not None:
            if (
                not isinstance(measure_range, list)
                or len(measure_range) != 2
                or any(
                    isinstance(value, bool) or not isinstance(value, int)
                    for value in measure_range
                )
            ):
                self._send_error_json(400, "measure_range must be [start, end]")
                return
            start, end = measure_range
            if start < 1 or end < start:
                self._send_error_json(400, "measure_range must be ascending and >= 1")
                return
            max_measure = piece_status["max_measure_number"]
            if max_measure is not None and end > max_measure:
                self._send_error_json(
                    400, f"measure_range exceeds this piece's known range (1-{max_measure})"
                )
                return
            range_tuple = (start, end)

        try:
            result = qa.ask(
                piece_id=piece_id,
                question=question,
                measure_range=range_tuple,
                generate=want_generation,
            )
        except ValueError as exc:
            self._send_error_json(400, str(exc))
            return
        self._send_json(result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Soprano Studio listening on http://{args.host}:{args.port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
