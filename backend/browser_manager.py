import base64
import asyncio
import atexit
import functools
import os
import subprocess
import sys


_browser = None
_context = None
_page = None
_playwright = None


async def _ensure_browsers():
    """If Playwright browsers aren't installed, install them automatically."""
    cache_dir = os.path.expanduser("~/Library/Caches/ms-playwright")
    if not os.path.isdir(cache_dir) or not any(
        d for d in os.listdir(cache_dir) if d.startswith("chromium")
    ):
        print("Playwright browsers not found. Installing Chromium...")
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, functools.partial(
            subprocess.run,
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=True, timeout=120,
        ))


async def start_browser(headless: bool = True, width: int = 1280, height: int = 720):
    global _browser, _context, _page, _playwright
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return {"success": False, "message": "Playwright not installed. Run: pip install playwright && python -m playwright install chromium"}

    if _browser:
        return {"success": True, "message": "Browser already running"}

    await _ensure_browsers()

    p = await async_playwright().start()
    _playwright = p
    _browser = await p.chromium.launch(headless=headless)
    _context = await _browser.new_context(viewport={"width": width, "height": height})
    _page = await _context.new_page()

    # Register cleanup on interpreter exit
    def _cleanup():
        try:
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            if loop.is_running():
                loop.create_task(_stop_inner())
            else:
                loop.run_until_complete(_stop_inner())
        except Exception as e:
            print(f"Browser cleanup error: {e}", file=sys.stderr)
    atexit.register(_cleanup)

    return {"success": True, "message": f"Browser started (headless={headless})"}


async def _stop_inner():
    global _browser, _context, _page, _playwright
    try:
        if _context:
            await _context.close()
    except Exception:
        pass
    try:
        if _browser:
            await _browser.close()
    except Exception:
        pass
    try:
        if _playwright:
            await _playwright.stop()
    except Exception:
        pass
    _browser = None
    _context = None
    _page = None
    _playwright = None


async def stop_browser():
    global _browser, _context, _page, _playwright
    if not _browser:
        return {"success": False, "message": "No browser running"}
    await _stop_inner()
    return {"success": True, "message": "Browser stopped"}


async def browser_status():
    return {
        "running": _browser is not None,
        "url": _page.url if _page else None,
    }


async def navigate(url: str):
    global _page
    if not _page:
        return {"success": False, "message": "No browser page open"}
    try:
        await _page.goto(url, timeout=30000)
        return {"success": True, "url": _page.url, "title": await _page.title()}
    except Exception as e:
        return {"success": False, "message": str(e)}


async def browser_click(selector: str):
    if not _page:
        return {"success": False, "message": "No browser page open"}
    try:
        await _page.click(selector, timeout=10000)
        return {"success": True, "message": f"Clicked: {selector}"}
    except Exception as e:
        return {"success": False, "message": str(e)}


async def browser_type(selector: str, text: str):
    if not _page:
        return {"success": False, "message": "No browser page open"}
    try:
        await _page.fill(selector, text)
        return {"success": True, "message": f"Typed into: {selector}"}
    except Exception as e:
        return {"success": False, "message": str(e)}


async def browser_screenshot() -> dict:
    if not _page:
        return {"success": False, "message": "No browser page open"}
    try:
        raw = await _page.screenshot(type="png", full_page=False)
        b64 = base64.b64encode(raw).decode()
        return {"success": True, "screenshot": f"data:image/png;base64,{b64}", "size": len(raw)}
    except Exception as e:
        return {"success": False, "message": str(e)}


async def browser_evaluate(js: str) -> dict:
    if not _page:
        return {"success": False, "message": "No browser page open"}
    try:
        result = await _page.evaluate(js)
        return {"success": True, "result": str(result)[:2000]}
    except Exception as e:
        return {"success": False, "message": str(e)}


async def get_page_text() -> dict:
    if not _page:
        return {"success": False, "message": "No browser page open"}
    try:
        text = await _page.evaluate("document.body.innerText")
        return {"success": True, "text": (text or "")[:5000]}
    except Exception as e:
        return {"success": False, "message": str(e)}
