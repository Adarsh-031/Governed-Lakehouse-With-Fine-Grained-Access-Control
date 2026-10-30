"""Private catalog endpoints and secret-safe request helpers."""

import os
import time

import httpx

CATALOG = "http://catalog:8181"
TOKEN_ENDPOINT = "http://keycloak:8080/realms/lakehouse/protocol/openid-connect/token"
WAREHOUSE = "hospital"
PROJECT = "00000000-0000-0000-0000-000000000000"


def request(method, url, *, token=None, expected=(200,), **kwargs):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    # Safe GET retries cover brief dependency restarts (for example, Lakekeeper
    # querying OpenFGA immediately after startup). Never retry state-changing calls.
    retry_delays = (1, 2, 4, 8) if method.upper() == "GET" else ()
    for attempt in range(len(retry_delays) + 1):
        response = httpx.request(method, url, headers=headers, timeout=30, **kwargs)
        if response.status_code in expected:
            return response.json() if response.content else None
        if (
            method.upper() == "GET"
            and response.status_code in {502, 503, 504}
            and attempt < len(retry_delays)
        ):
            time.sleep(retry_delays[attempt])
            continue
        # Never echo response bodies, request payloads, or Authorization headers.
        raise RuntimeError(f"{method} {url.split('?')[0]} returned HTTP {response.status_code}")


def access_token(service):
    variable = service.upper().replace("-", "_") + "_OIDC_CLIENT_SECRET"
    result = request(
        "POST",
        TOKEN_ENDPOINT,
        data={
            "grant_type": "client_credentials",
            "client_id": service,
            "client_secret": os.environ[variable],
            "scope": "openid",
        },
    )
    return result["access_token"]


def management(method, path, token, **kwargs):
    return request(method, f"{CATALOG}/management/v1/{path}", token=token, **kwargs)


def run(main):
    try:
        main()
    except Exception as error:  # noqa: BLE001 - final boundary must not leak SDK credentials
        # Some SDK exceptions include headers or credentials; omit their text.
        message = str(error) if isinstance(error, RuntimeError) else type(error).__name__
        raise SystemExit(f"Catalog operation failed: {message}") from None
