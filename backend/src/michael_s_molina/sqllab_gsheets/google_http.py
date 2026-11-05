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
"""An authenticated Google JSON transport whose requests are never replayed implicitly."""

from __future__ import annotations

from collections.abc import Mapping

from google.auth.credentials import Credentials
from google.auth.exceptions import TransportError
from google.auth.transport.requests import AuthorizedSession
from httplib2 import Response
from requests.adapters import HTTPAdapter
from requests.exceptions import RequestException


class GoogleHttp:
    """Provide googleapiclient's HTTP contract while leaving retries to the writer."""

    def __init__(self, credentials: Credentials, timeout: float = 60.0) -> None:
        self.timeout = timeout
        self.session = AuthorizedSession(credentials, max_refresh_attempts=0)
        self.session.mount("http://", HTTPAdapter(max_retries=0))
        self.session.mount("https://", HTTPAdapter(max_retries=0))

    def request(
        self,
        uri: str,
        method: str = "GET",
        body: str | bytes | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> tuple[Response, bytes]:
        """Make one API request, without redirects or transport/authentication replays."""
        try:
            response = self.session.request(
                method,
                uri,
                data=body,
                headers=headers,
                timeout=self.timeout,
                allow_redirects=False,
            )
            content = response.content
        except RequestException as ex:
            raise TransportError("Google API transport failed") from ex
        metadata = dict(response.headers)
        metadata["status"] = str(response.status_code)
        metadata["reason"] = response.reason
        return Response(metadata), content

    def close(self) -> None:
        """Release the authenticated session and its connection pools."""
        self.session.close()
