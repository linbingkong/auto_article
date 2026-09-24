from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from wechat_agent.user import UserStore
from playwright.sync_api import sync_playwright

BASE_URL = "http://127.0.0.1:8768"
OUT = ROOT / "docs" / "assets" / "xiaohongshu"
OUT.mkdir(parents=True, exist_ok=True)
USERNAME = "xhsdemo"
PASSWORD = "XhsDemo12345!"
store = UserStore(ROOT / "output" / "auth.db")
old = store.get_user_by_username(USERNAME)
if old:
    try:
        store.delete_user(old["id"])
    except Exception:
        pass
user = store.create_user(USERNAME, PASSWORD, role="admin")

try:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe", args=["--disable-gpu"])
        ctx = browser.new_context(viewport={"width": 1440, "height": 1000}, device_scale_factor=2)
        page = ctx.new_page()
        page.goto(BASE_URL, wait_until="networkidle")
        page.fill("#loginUsername", USERNAME)
        page.fill("#loginPassword", PASSWORD)
        page.click("#loginForm button[type=submit]")
        page.wait_for_selector("#userBox:not([style*='display:none'])", timeout=15000)
        page.goto(f"{BASE_URL}/#hotspots", wait_until="domcontentloaded")
        page.wait_for_selector("#generationMode", timeout=20000)
        page.wait_for_timeout(1500)
        aside = page.locator("aside.sticky-card").first
        aside.screenshot(path=str(OUT / "05-settings.png"))
        print("05-settings.png")
        ctx.close()
        browser.close()
finally:
    current = store.get_user_by_username(USERNAME)
    if current:
        try:
            store.delete_user(current["id"])
        except Exception as exc:
            print(f"cleanup warning: {exc}")

