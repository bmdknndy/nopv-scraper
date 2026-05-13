##### direct_http.py #####
##### brdyknndy #####

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Optional

import httpx


@dataclass
class DirectFetchResult:
    ok: bool
    status_code: Optional[int]
    content_type: str
    reason: str
    pdf_bytes: bytes = b""


def _looks_like_pdf(content_type: str, body: bytes) -> bool:
    ct = (content_type or "").lower()
    return ("pdf" in ct) or body.startswith(b"%PDF")


def fetch_pdf_direct(
    url: str,
    timeout_seconds: float = 20.0,
    retries: int = 3,
) -> DirectFetchResult:
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; nyc-nopv-scraper-v2/0.1)",
        "Accept": "application/pdf,text/html,application/xhtml+xml",
    }

    last_reason = "unknown_error"
    for attempt in range(1, retries + 1):
        try:
            with httpx.Client(timeout=timeout_seconds, follow_redirects=True) as client:
                resp = client.get(url, headers=headers)
            content_type = resp.headers.get("content-type", "")
            body = resp.content

            if resp.status_code != 200:
                last_reason = f"http_{resp.status_code}"
            elif not _looks_like_pdf(content_type, body):
                # Common on challenge pages
                last_reason = "not_pdf_response"
                return DirectFetchResult(
                    ok=False,
                    status_code=resp.status_code,
                    content_type=content_type,
                    reason=last_reason,
                )
            else:
                return DirectFetchResult(
                    ok=True,
                    status_code=resp.status_code,
                    content_type=content_type,
                    reason="ok",
                    pdf_bytes=body,
                )

        except httpx.TimeoutException:
            last_reason = "timeout"
        except Exception as e:
            last_reason = f"error:{type(e).__name__}"

        if attempt < retries:
            time.sleep(0.4 + random.random() * 0.6)

    return DirectFetchResult(
        ok=False,
        status_code=None,
        content_type="",
        reason=last_reason,
    )
