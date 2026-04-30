from __future__ import annotations

from dataclasses import dataclass, field
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
    pdf_bytes: bytes = field(default_factory=bytes)


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


_POLL_INTERVAL_MS = 500


def fetch_pdf_via_browser(
    url: str,
    headed: bool = True,
    timeout_ms: int = 120_000,
    interactive_wait_ms: int = 90_000,
    storage_state_path: str = "state/session.json",
    save_storage_state: bool = True,
) -> BrowserFetchResult:
    """
    Fetches a PDF via headed browser. Lets the user solve any CAPTCHA manually.
    Polls the response listener and exits the moment a PDF is captured.
    """
    state_path = Path(storage_state_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        # No stealth flags. They make reCAPTCHA *more* suspicious, not less.
        browser = p.chromium.launch(headless=not headed)

        context_kwargs = {"accept_downloads": True}
        if state_path.exists():
            context_kwargs["storage_state"] = str(state_path)

        context = browser.new_context(**context_kwargs)
        page = context.new_page()

        captured_pdf: Optional[bytes] = None
        captured_ct: str = ""
        captured_url: str = ""
        page_closed = False

        def on_response(resp):
            nonlocal captured_pdf, captured_ct, captured_url
            if captured_pdf is not None:
                return
            ct = (resp.headers.get("content-type") or "").lower()
            if "application/pdf" not in ct:
                return
            try:
                body = resp.body()
                if is_valid_pdf_payload(body, ct):
                    captured_pdf = body
                    captured_ct = ct
                    captured_url = resp.url
                    print(f"  ✓ PDF captured via response listener: {len(body):,} bytes")
            except Exception as e:
                print(f"  ! Failed to read PDF body: {e}")

        def on_page_close():
            nonlocal page_closed
            page_closed = True

        page.on("response", on_response)
        page.on("close", lambda _: on_page_close())

        def _save_and_close(result: BrowserFetchResult) -> BrowserFetchResult:
            if save_storage_state:
                try:
                    context.storage_state(path=str(state_path))
                except Exception:
                    pass
            try:
                browser.close()
            except Exception:
                pass
            return result

        # ── Navigate ──────────────────────────────────────────────────────────
        print(f"  → Navigating to: {url}")
        try:
            main_resp = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        except PlaywrightTimeoutError:
            print("  ✗ Timed out during navigation.")
            return _save_and_close(BrowserFetchResult(ok=False, reason="timeout_navigating"))

        # Main response itself might be a PDF
        if main_resp is not None:
            try:
                ct = main_resp.headers.get("content-type", "")
                body = main_resp.body()
                if is_valid_pdf_payload(body, ct):
                    print("  ✓ PDF found in main navigation response.")
                    return _save_and_close(BrowserFetchResult(
                        ok=True, reason="ok_main_response",
                        final_url=main_resp.url, content_type=ct, pdf_bytes=body,
                    ))
            except Exception:
                pass

        # ── Poll, with safe failure if the page is closed by the user ─────────
        secs = interactive_wait_ms // 1000
        print(f"  → Solve any CAPTCHA in the browser. Polling up to {secs}s for PDF...")
        elapsed = 0
        while elapsed < interactive_wait_ms:
            if captured_pdf is not None:
                print(f"  ✓ PDF captured after ~{elapsed // 1000}s.")
                break
            if page_closed:
                print("  → Browser tab closed by user. Stopping poll.")
                break
            try:
                page.wait_for_timeout(_POLL_INTERVAL_MS)
            except Exception:
                # Page/browser was closed mid-poll
                page_closed = True
                break
            elapsed += _POLL_INTERVAL_MS

        if captured_pdf is not None:
            return _save_and_close(BrowserFetchResult(
                ok=True, reason="ok_network_capture",
                final_url=captured_url, content_type=captured_ct, pdf_bytes=captured_pdf,
            ))

        # ── Last resort: re-request the current URL via the context ───────────
        if not page_closed:
            try:
                final_url = page.url
            except Exception:
                final_url = url
            print(f"  → No PDF via listener. Trying direct context request: {final_url}")
            try:
                resp = context.request.get(final_url, timeout=timeout_ms)
                ct = resp.headers.get("content-type", "")
                body = resp.body()
                print(f"     status={resp.status}, content-type={ct}, size={len(body)}")
                if is_valid_pdf_payload(body, ct):
                    print("  ✓ PDF retrieved via direct context request.")
                    return _save_and_close(BrowserFetchResult(
                        ok=True, reason="ok_context_request_final_url",
                        final_url=final_url, content_type=ct, pdf_bytes=body,
                    ))
                print(f"  ✗ Direct request was not a valid PDF (first bytes: {body[:30]!r})")
            except Exception as e:
                print(f"  ✗ Direct context request failed: {e}")

        print("  ✗ All strategies exhausted.")
        return _save_and_close(BrowserFetchResult(
            ok=False, reason="no_valid_pdf_captured",
            final_url=(page.url if not page_closed else url),
        ))