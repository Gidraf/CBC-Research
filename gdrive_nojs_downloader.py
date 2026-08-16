#!/usr/bin/env python3
"""
No-JS Google Drive PDF Downloader & Interceptor
==============================================
Demonstrates downloading Google Drive PDFs by explicitly disabling JavaScript.

Mode 1: Pure Python No-JS Form Extractor (Fast, lightweight, works on Termux)
Mode 2: Playwright No-JS Browser Interceptor (java_script_enabled=False)

Usage:
  python3 gdrive_nojs_downloader.py
"""

import os
import sys
import re
import html
import time
import urllib.request
import urllib.parse
import http.cookiejar
from pathlib import Path

# ──────────────────────────────────────────────
# MODE 1: PURE PYTHON NO-JS HTML FORM EXTRACTOR
# ──────────────────────────────────────────────
def download_gdrive_no_js_python(file_id, output_path):
    """
    Simulates a browser with JavaScript DISABLED.
    Google Drive responds to No-JS requests by outputting a plain HTML page
    containing a direct `<form action="...">` or `<a href="...">` download link.
    """
    print(f"  [No-JS Python] Requesting Google Drive file {file_id}...")

    # Header mimicking a browser with JS disabled
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    cookie_jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))

    # Step 1: Request initial download page
    initial_url = f"https://drive.google.com/uc?export=download&id={file_id}"
    req = urllib.request.Request(initial_url, headers=headers)

    try:
        with opener.open(req, timeout=30) as resp:
            content_bytes = resp.read()

        # Check if we directly received a binary PDF (%PDF magic bytes)
        if content_bytes.startswith(b'%PDF') and len(content_bytes) >= 5000:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_bytes(content_bytes)
            print(f"  ✅ Direct PDF downloaded ({len(content_bytes):,} bytes)")
            return True

        content_html = content_bytes.decode('utf-8', errors='ignore')

        # Step 2: In No-JS mode, Google Drive outputs a <form action="..."> or <a href="..."> download link
        direct_link_match = (
            re.search(r'<a\s+id=["\']uc-download-link["\']\s+class=["\'][^"\']*["\']\s+href=["\']([^"\']+)["\']', content_html, re.IGNORECASE) or
            re.search(r'href=["\'](https://drive\.usercontent\.google\.com/download[^"\']+)["\']', content_html, re.IGNORECASE) or
            re.search(r'<form[^>]*action=["\']([^"\']+)["\']', content_html, re.IGNORECASE)
        )

        if direct_link_match:
            download_url = html.unescape(direct_link_match.group(1))
            if not download_url.startswith("http"):
                download_url = urllib.parse.urljoin("https://drive.google.com", download_url)

            print(f"  🔗 Extracted No-JS Direct Download URL: {download_url[:90]}...")
            
            # Step 3: Follow direct link
            req2 = urllib.request.Request(download_url, headers=headers)
            with opener.open(req2, timeout=60) as resp2:
                pdf_bytes = resp2.read()

            if pdf_bytes.startswith(b'%PDF'):
                Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                Path(output_path).write_bytes(pdf_bytes)
                print(f"  ✅ PDF saved from No-JS link ({len(pdf_bytes):,} bytes) → {output_path}")
                return True

        print(f"  ⚠️ Could not parse No-JS download link from HTML response.")
        return False

    except Exception as e:
        print(f"  ❌ Error downloading: {e}")
        return False


# ──────────────────────────────────────────────
# MODE 2: PLAYWRIGHT BROWSER WITH JAVASCRIPT DISABLED
# ──────────────────────────────────────────────
def download_gdrive_playwright_no_js(file_id, output_path):
    """
    Launches Playwright Chromium with `java_script_enabled=False`.
    Navigates to Google Drive, clicks the No-JS download element, and intercepts the file download.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright not installed.")
        return False

    print(f"  [Playwright No-JS] Launching browser with JavaScript DISABLED...")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
        )

        # 💡 KEY CONFIGURATION: Disable JavaScript execution in the browser context
        context = browser.new_context(
            java_script_enabled=False,
            accept_downloads=True,
            viewport={"width": 1280, "height": 960}
        )
        page = context.new_page()

        target_url = f"https://drive.google.com/uc?export=download&id={file_id}"
        print(f"  Navigating to: {target_url}")

        try:
            # Expect browser download trigger event
            with page.expect_download(timeout=30000) as download_info:
                page.goto(target_url, wait_until="domcontentloaded")
                
                # If a confirmation page or submit button appears, click it
                if page.locator("#uc-download-link").is_visible():
                    page.locator("#uc-download-link").click()
                elif page.locator("input[type='submit']").is_visible():
                    page.locator("input[type='submit']").click()

            download = download_info.value
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            download.save_as(output_path)
            print(f"  ✅ Saved PDF via Playwright No-JS Download Event → {output_path}")
            browser.close()
            return True

        except Exception as e:
            print(f"  ⚠️ Playwright No-JS download warning: {e}")
            browser.close()
            return False


if __name__ == "__main__":
    test_file_id = "1YlwoCFAVxhjUo_V1A-89GRcho0r0Gq1u"
    output = "downloaded_nojs_test.pdf"

    print("=" * 65)
    print("  Testing Google Drive No-JS Downloader")
    print("=" * 65)
    
    # Try Mode 1 (Pure Python No-JS)
    success = download_gdrive_no_js_python(test_file_id, output)
    if not success:
        # Try Mode 2 (Playwright No-JS)
        download_gdrive_playwright_no_js(test_file_id, output)
