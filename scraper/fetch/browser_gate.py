from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


@dataclass
class BrowserFetchResult:
    ok: bool
    reason: str
    final_url: str = ""
    content_type: str = ""
    pdf_bytes: bytes = b""


def is_valid_pdf_payload(body: bytes, content_type: str) -> bool:
    if not body:
        return False
    head = body[:300].lower()
    if head.startswith(b"<!doctype") or head.startswith(b"<html"):
        return False
    if len(body) < 10_000:
        return False
    ct = (content_type or "").lower()
    return body.startswith(b"%PDF") or ("application/pdf" in ct)


def fetch_pdf_via_browser(
    url: str,
    headed: bool = True,
    timeout_ms: int = 120_000,
    interactive_wait_ms: int = 30_000,
    storage_state_path: str = "state/session.json",
    save_storage_state: bool = True,
) -> BrowserFetchResult:
    """
    Browser fetch with session persistence and human-in-the-loop challenge handling.
    """
    state_path = Path(storage_state_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not headed)

        context_kwargs = {"accept_downloads": True}
        if state_path.exists():
            context_kwargs["storage_state"] = str(state_path)

        context = browser.new_context(**context_kwargs)
        page = context.new_page()

        captured_pdf: Optional[bytes] = None
        captured_ct: str = ""
        captured_url: str = ""

        def on_response(resp):
            nonlocal captured_pdf, captured_ct, captured_url
            if captured_pdf is not None:
                return
            try:
                ct = resp.headers.get("content-type", "")
                body = resp.body()
                if is_valid_pdf_payload(body, ct):
                    captured_pdf = body
                    captured_ct = ct
                    captured_url = resp.url
            except Exception:
                pass

        page.on("response", on_response)

        try:
            main_resp = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        except PlaywrightTimeoutError:
            if save_storage_state:
                context.storage_state(path=str(state_path))
            browser.close()
            return BrowserFetchResult(ok=False, reason="timeout_navigating")

        # Main response itself could be PDF
        if main_resp is not None:
            try:
                ct = main_resp.headers.get("content-type", "")
                body = main_resp.body()
                if is_valid_pdf_payload(body, ct):
                    if save_storage_state:
                        context.storage_state(path=str(state_path))
                    browser.close()
                    return BrowserFetchResult(
                        ok=True,
                        reason="ok_main_response",
                        final_url=main_resp.url,
                        content_type=ct,
                        pdf_bytes=body,
                    )
            except Exception:
                pass

        # Give user chance to pass challenge!
        page.wait_for_timeout(interactive_wait_ms)

        if captured_pdf is not None:
            if save_storage_state:
                context.storage_state(path=str(state_path))
            browser.close()
            return BrowserFetchResult(
                ok=True,
                reason="ok_network_capture",
                final_url=captured_url,
                content_type=captured_ct,
                pdf_bytes=captured_pdf,
            )

        # Last chance: request final URL directly from context
        final_url = page.url
        try:
            resp = context.request.get(final_url, timeout=timeout_ms)
            body = resp.body()
            ct = resp.headers.get("content-type", "")
            if is_valid_pdf_payload(body, ct):
                if save_storage_state:
                    context.storage_state(path=str(state_path))
                browser.close()
                return BrowserFetchResult(
                    ok=True,
                    reason="ok_context_request_final_url",
                    final_url=final_url,
                    content_type=ct,
                    pdf_bytes=body,
                )
        except Exception:
            pass

        if save_storage_state:
            context.storage_state(path=str(state_path))
        browser.close()
        return BrowserFetchResult(
            ok=False,
            reason="no_valid_pdf_captured",
            final_url=final_url,
        )