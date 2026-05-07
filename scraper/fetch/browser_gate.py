from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

try:
    from twocaptcha import TwoCaptcha
    _TWOCAPTCHA_AVAILABLE = True
except ImportError:
    _TWOCAPTCHA_AVAILABLE = False


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


def check_2captcha_balance(warn_below: float = 1.0) -> Optional[float]:
    """
    Returns current 2Captcha account balance in USD, or None if unavailable.
    Prints a warning if balance is below `warn_below` (default $1.00).
    Safe to call before a batch run; never raises.
    """
    api_key = os.environ.get("TWOCAPTCHA_API_KEY")
    if not api_key:
        print("ℹ️  2Captcha: TWOCAPTCHA_API_KEY not set — auto-solve disabled.")
        return None
    if not _TWOCAPTCHA_AVAILABLE:
        print("ℹ️  2Captcha: twocaptcha package not installed — auto-solve disabled.")
        return None
    try:
        solver = TwoCaptcha(api_key)
        balance = float(solver.balance())
        if balance < warn_below:
            print(f"⚠️  2Captcha balance is LOW: ${balance:.4f}. Top up before scaling.")
        else:
            print(f"✓ 2Captcha balance: ${balance:.4f}")
        return balance
    except Exception as e:
        print(f"ℹ️  2Captcha balance check failed (continuing anyway): {e}")
        return None


def _try_solve_recaptcha_via_2captcha(page, page_url: str) -> bool:
    """
    Attempts to auto-solve a reCAPTCHA v2 on the current page using 2Captcha.
    Returns True if a token was injected and the form was submitted.
    """
    api_key = os.environ.get("TWOCAPTCHA_API_KEY")
    if not api_key:
        print("  → TWOCAPTCHA_API_KEY not set — skipping auto-solve.")
        return False
    if not _TWOCAPTCHA_AVAILABLE:
        print("  → 2captcha-python not installed — skipping auto-solve.")
        return False

    # 1. Find the data-sitekey on the page
    try:
        sitekey = page.evaluate("""
            () => {
                const el = document.querySelector('[data-sitekey]');
                return el ? el.getAttribute('data-sitekey') : null;
            }
        """)
    except Exception as e:
        print(f"  ✗ Could not extract sitekey: {e}")
        return False

    if not sitekey:
        print("  → No data-sitekey on page — not a reCAPTCHA v2 challenge.")
        return False

    print(f"  → Found sitekey {sitekey[:20]}... Submitting to 2Captcha...")

    # 2. Send to 2Captcha and wait for token
    try:
        solver = TwoCaptcha(api_key)
        result = solver.recaptcha(sitekey=sitekey, url=page_url)
        token = result.get("code")
        if not token:
            print(f"  ✗ 2Captcha returned no token: {result}")
            return False
        print(f"  ✓ Token received (length {len(token)}). Injecting...")
    except Exception as e:
        print(f"  ✗ 2Captcha API error: {e}")
        return False

    # 3. Inject the token into the hidden response field
    try:
        page.evaluate(f"""
            () => {{
                const ta = document.querySelector('textarea[name="g-recaptcha-response"]');
                if (ta) {{
                    ta.style.display = '';
                    ta.value = '{token}';
                }}
            }}
        """)
    except Exception as e:
        print(f"  ✗ Could not inject token: {e}")
        return False

    # 4. Submit the form
    try:
        submit_btn = page.locator(
            'input[type="submit"], button[type="submit"], #submit'
        ).first
        if submit_btn.is_visible(timeout=2_000):
            submit_btn.click()
            print("  ✓ Submit clicked. Watching for PDF response...")
            return True
        # No visible submit button — try submitting the form via JS
        page.evaluate("""
            () => {
                const form = document.querySelector('form');
                if (form) form.submit();
            }
        """)
        print("  ✓ Form submitted via JS.")
        return True
    except Exception as e:
        print(f"  ✗ Submit failed: {e}")
        return False


def fetch_pdf_via_browser(
    url: str,
    headed: bool = True,
    timeout_ms: int = 120_000,
    interactive_wait_ms: int = 70_000,
    storage_state_path: str = "state/session.json",
    save_storage_state: bool = True,
) -> BrowserFetchResult:
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
                    print(f"  ✓ PDF captured: {len(body):,} bytes")
            except Exception as e:
                print(f"  ! Failed to read PDF body: {e}")

        page.on("response", on_response)
        page.on("close", lambda _: globals().__setitem__("_pc", True))

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
        except Exception as e:
            print(f"  ✗ Navigation failed: {e}")
            return _save_and_close(BrowserFetchResult(ok=False, reason="navigation_error"))
        

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

        # ── Wait briefly for page to render ──────────────────────────────────
        page.wait_for_timeout(2_000)

        # ── Attempt 2Captcha auto-solve ───────────────────────────────────────
        _try_solve_recaptcha_via_2captcha(page, url)

        # ── Poll for PDF (covers both auto-solve success and manual fallback) ─
        secs = interactive_wait_ms // 1000
        print(f"  → Polling up to {secs}s for PDF (manual solve possible if auto failed)...")
        elapsed = 0
        while elapsed < interactive_wait_ms:
            if captured_pdf is not None:
                print(f"  ✓ PDF captured after ~{elapsed // 1000}s.")
                break
            try:
                page.wait_for_timeout(_POLL_INTERVAL_MS)
            except Exception:
                page_closed = True
                break
            elapsed += _POLL_INTERVAL_MS

        if captured_pdf is not None:
            return _save_and_close(BrowserFetchResult(
                ok=True, reason="ok_network_capture",
                final_url=captured_url, content_type=captured_ct, pdf_bytes=captured_pdf,
            ))

        # ── Last resort: direct context request ───────────────────────────────
        if not page_closed:
            try:
                final_url = page.url
            except Exception:
                final_url = url
            print(f"  → No PDF via listener. Trying direct request: {final_url}")
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
            except Exception as e:
                print(f"  ✗ Direct request failed: {e}")

        print("  ✗ All strategies exhausted.")
        return _save_and_close(BrowserFetchResult(
            ok=False, reason="no_valid_pdf_captured",
            final_url=(page.url if not page_closed else url),
        ))