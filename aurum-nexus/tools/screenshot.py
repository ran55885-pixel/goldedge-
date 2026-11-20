"""Capture dashboard screenshots from a RUNNING hub (live data) with Playwright.

    python tools/screenshot.py --url http://127.0.0.1:8787 --user operator --password ... --out screenshots
"""
import argparse
import asyncio
from pathlib import Path


async def main(a):
    from playwright.async_api import async_playwright
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path=a.executable) if a.executable else await p.chromium.launch()
        ctx = await b.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=a.scale)
        page = await ctx.new_page()
        await page.goto(a.url + "/login")
        await page.wait_for_timeout(1200)
        await page.screenshot(path=str(out / "login.png"))
        await page.fill("#u", a.user)
        await page.fill("#p", a.password)
        await page.click("#go")
        await page.wait_for_url(a.url + "/")
        await page.wait_for_timeout(a.wait * 1000)
        await page.screenshot(path=str(out / "main_dashboard.png"))
        await page.screenshot(path=str(out / "main_dashboard_full.png"), full_page=True)
        chart = page.locator(".col.center .panel").first
        await page.evaluate("document.querySelectorAll('#layers input').forEach(i => { if (!i.checked) i.click(); })")
        await page.wait_for_timeout(1500)
        await chart.screenshot(path=str(out / "chart_layers.png"))
        await page.locator(".col.right").screenshot(path=str(out / "ranking_levels_dxy.png"))
        await page.locator(".desk").first.click()
        await page.wait_for_timeout(1500)
        await page.screenshot(path=str(out / "bot_details.png"))
        await page.click("#modalClose")
        await page.click("nav button[data-view=accounts]")
        await page.wait_for_timeout(1500)
        await page.screenshot(path=str(out / "accounts_positions.png"), full_page=True)
        await page.click("nav button[data-view=strategy]")
        await page.wait_for_timeout(2500)
        await page.screenshot(path=str(out / "strategy_per_symbol.png"), full_page=True)
        m = await b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=a.scale, is_mobile=True,
                                has_touch=True, storage_state=await ctx.storage_state())
        mp = await m.new_page()
        await mp.goto(a.url + "/")
        await mp.wait_for_timeout(a.wait * 1000)
        await mp.screenshot(path=str(out / "mobile.png"))
        await mp.screenshot(path=str(out / "mobile_full.png"), full_page=True)
        overflow = await mp.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        print("mobile horizontal overflow px:", overflow)
        await b.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8787")
    ap.add_argument("--user", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--out", default="screenshots")
    ap.add_argument("--scale", type=float, default=2)
    ap.add_argument("--wait", type=float, default=8)
    ap.add_argument("--executable", default=None, help="path to a Chromium binary (optional)")
    asyncio.run(main(ap.parse_args()))
