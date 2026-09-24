from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from playwright.sync_api import sync_playwright
from wechat_agent.user import UserStore

BASE_URL = os.environ.get("MANUAL_BASE_URL", "http://127.0.0.1:8768")
OUT = ROOT / "docs" / "assets" / "user-manual"
OUT.mkdir(parents=True, exist_ok=True)
for old_image in OUT.glob("*.png"):
    old_image.unlink()

USERNAME = "manualcreator"
PASSWORD = "ManualCreator123!"
store = UserStore(ROOT / "output" / "auth.db")
existing = store.get_user_by_username(USERNAME)
if existing:
    try:
        store.delete_user(existing["id"])
    except Exception:
        pass
user = store.create_user(USERNAME, PASSWORD, role="creator")


def shot(page, name, full_page=True):
    page.wait_for_timeout(1200)
    page.screenshot(path=str(OUT / name), full_page=full_page)
    print(name)


try:
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            executable_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            args=["--disable-gpu"],
        )

        public = browser.new_context(
            viewport={"width": 1440, "height": 1000}, device_scale_factor=1
        )
        page = public.new_page()
        page.goto(BASE_URL, wait_until="networkidle")
        shot(page, "01-login.png")
        page.click("#showRegister")
        page.wait_for_selector("#registerForm")
        shot(page, "02-register.png")
        public.close()

        ctx = browser.new_context(
            viewport={"width": 1440, "height": 1000}, device_scale_factor=1
        )
        page = ctx.new_page()
        page.goto(BASE_URL, wait_until="networkidle")
        page.fill("#loginUsername", USERNAME)
        page.fill("#loginPassword", PASSWORD)
        page.click("#loginForm button[type=submit]")
        page.wait_for_selector("#userBox:not([style*='display:none'])", timeout=15000)
        assert "创作者" in page.locator("#userRole").inner_text()
        for admin_page in ("config", "users", "registrations", "payment-orders", "audit"):
            assert not page.locator(f'.nav a[data-page="{admin_page}"]').is_visible()
        shot(page, "03-dashboard.png")

        routes = [
            ("hotspots", "04-hotspots-ranking.png", False),
            ("articles", "06-articles.png", True),
            ("billing", "07-billing.png", True),
            ("my-api-config", "08-my-api-config.png", True),
            ("tasks", "09-tasks.png", True),
            ("history", "10-history.png", True),
        ]
        for route, filename, full_page in routes:
            page.goto(f"{BASE_URL}/#{route}", wait_until="domcontentloaded")
            page.wait_for_timeout(1600)
            shot(page, filename, full_page=full_page)
            if route == "hotspots":
                page.click('[data-search-mode="web"]')
                page.wait_for_timeout(400)
                shot(page, "05-hotspots-web.png")

        ctx.close()
        browser.close()
finally:
    current = store.get_user_by_username(USERNAME)
    if current:
        try:
            store.delete_user(current["id"])
        except Exception as exc:
            print(f"cleanup warning: {exc}")
