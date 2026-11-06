# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Literal

import pytest
from google.auth.credentials import AnonymousCredentials
from google.auth.exceptions import TransportError
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from michael_s_molina.sqllab_gsheets.google_http import GoogleHttp

ResponseMode = Literal[
    "lost_response", "server_error", "redirect", "unauthorized", "success"
]


@dataclass
class GoogleEndpoint:
    """State observed by the loopback server and the real Google client."""

    url: str
    mode: ResponseMode = "success"
    created_ids: list[str] = field(default_factory=list)
    reads: int = 0


@pytest.fixture
def google_endpoint(monkeypatch: pytest.MonkeyPatch) -> Iterator[GoogleEndpoint]:
    """Serve Google-shaped responses locally without using an account or credentials."""
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    endpoint = GoogleEndpoint("")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format_string: str, *args: object) -> None:
            """Keep expected connection failures out of the test output."""

        def send_json(self, status: int, payload: object) -> None:
            """Return a complete JSON response understood by googleapiclient."""
            content = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def do_POST(self) -> None:
            """Commit creation before simulating a lost or unsuccessful response."""
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            endpoint.created_ids.append(f"file-{len(endpoint.created_ids) + 1}")
            if len(endpoint.created_ids) == 1:
                if endpoint.mode == "lost_response":
                    self.close_connection = True
                    return
                if endpoint.mode == "redirect":
                    self.send_response(307)
                    self.send_header("Location", "/files?redirected=true")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if endpoint.mode in {"server_error", "unauthorized"}:
                    status = 503 if endpoint.mode == "server_error" else 401
                    self.send_json(status, {"error": {"message": "uncertain outcome"}})
                    return
            self.send_json(200, {"id": endpoint.created_ids[-1]})

        def do_GET(self) -> None:
            """Expose committed files to a subsequent read-only recovery request."""
            endpoint.reads += 1
            self.send_json(
                200, {"files": [{"id": file_id} for file_id in endpoint.created_ids]}
            )

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    endpoint.url = f"http://127.0.0.1:{server.server_port}/"
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield endpoint
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


@pytest.mark.parametrize(
    ("mode", "expected_status"),
    [
        ("lost_response", None),
        ("server_error", 503),
        ("redirect", 307),
        ("unauthorized", 401),
        ("success", 200),
    ],
)
def test_real_google_client_does_not_replay_creation(
    google_endpoint: GoogleEndpoint, mode: ResponseMode, expected_status: int | None
) -> None:
    """The transport sends one POST even when a committed creation has no usable reply."""
    google_endpoint.mode = mode
    http = GoogleHttp(AnonymousCredentials())
    try:
        drive = build(
            "drive",
            "v3",
            http=http,
            cache_discovery=False,
            static_discovery=True,
            client_options={"api_endpoint": google_endpoint.url},
        )
        request = drive.files().create(
            body={"name": "Test export", "appProperties": {"export_id": "marker"}}
        )
        if mode == "lost_response":
            with pytest.raises(TransportError):
                request.execute()
        elif expected_status != 200:
            with pytest.raises(HttpError) as error:
                request.execute()
            assert error.value.resp.status == expected_status
        else:
            assert request.execute() == {"id": "file-1"}

        assert google_endpoint.created_ids == ["file-1"]
        assert drive.files().list(
            q="appProperties has { key='export_id' and value='marker' }"
        ).execute() == {"files": [{"id": "file-1"}]}
        assert google_endpoint.reads == 1
    finally:
        http.close()
