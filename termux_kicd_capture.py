#!/usr/bin/env python3
"""
KICD CBC Curriculum Designs — Termux & Playwright Screenshot + Text Extractor
=============================================================================
Designed to run on Android Termux, macOS, Linux, and Windows.

What it does:
  1. Auto-detects installed Chromium (Termux `pkg install chromium` or standard Playwright).
  2. Opens each KICD grade page.
  3. Scrolls smoothly down the page to trigger all lazy-loaded Google Drive preview iframes.
  4. Takes a screenshot (.png) for each grade & subject design.
  5. Extracts all text content (.txt) from the page.
  6. Saves screenshots (.png) and text (.txt) side-by-side in structured grade folders.

Termux Setup Instructions:
--------------------------
  pkg update && pkg upgrade -y
  pkg install python nodejs chromium libx11 libxcomposite libxdamage -y
  pip install playwright
  playwright install-deps

Usage:
------
  python3 termux_kicd_capture.py
"""

import os
import sys
import time
import json
import re
import shutil
from pathlib import Path
from datetime import datetime

# ──────────────────────────────────────────────
# CONFIG & PATHS
# ──────────────────────────────────────────────
OUTPUT_DIR = Path("kicd_media_captures")
MANIFEST_FILE = OUTPUT_DIR / "capture_manifest.json"

GRADE_PAGES = [
    {"grade": "Pre-Primary_1",                  "url": "https://kicd.ac.ke/cbc-materials/pre-primary/"},
    {"grade": "Pre-Primary_2",                  "url": "https://kicd.ac.ke/cbc-materials/pre-primary/"},
    {"grade": "Grade_1",                        "url": "https://kicd.ac.ke/cbc-materials/lower-primary/"},
    {"grade": "Grade_2",                        "url": "https://kicd.ac.ke/cbc-materials/lower-primary/"},
    {"grade": "Grade_3",                        "url": "https://kicd.ac.ke/cbc-materials/lower-primary/"},
    {"grade": "Grade_4",                        "url": "https://kicd.ac.ke/cbc-materials/curriculum-designs/grade-four-designs/"},
    {"grade": "Grade_5",                        "url": "https://kicd.ac.ke/cbc-materials/curriculum-designs/grade-five-designs/"},
    {"grade": "Grade_6",                        "url": "https://kicd.ac.ke/cbc-materials/curriculum-designs/grade-six-designs/"},
    {"grade": "Grade_7",                        "url": "https://kicd.ac.ke/cbc-materials/curriculum-designs/grade-seven-designs/"},
    {"grade": "Grade_8",                        "url": "https://kicd.ac.ke/cbc-materials/curriculum-designs/grade-eight-designs/"},
    {"grade": "Grade_9",                        "url": "https://kicd.ac.ke/cbc-materials/curriculum-designs/grade-nine-designs/"},
    {"grade": "Grade_10",                       "url": "https://kicd.ac.ke/cbc-materials/curriculum-designs/grade-ten/"},
    {"grade": "Grade_11",                       "url": "https://kicd.ac.ke/cbc-materials/curriculum-designs/grade-eleven/"},
    {"grade": "Grade_12",                       "url": "https://kicd.ac.ke/cbc-materials/curriculum-designs/grade-twelve/"},
    {"grade": "Diploma_in_Teacher_Education",   "url": "https://kicd.ac.ke/cbc-materials/curriculum-designs/diploma-in-teacher-education/"},
]


def find_chromium_executable():
    """Locate Chromium executable across standard Termux / Linux / Mac paths."""
    candidates = [
        # Termux paths
        "/data/data/com.termux/files/usr/bin/chromium",
        "/data/data/com.termux/files/usr/bin/chromium-browser",
        # Linux standard paths
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
        # macOS paths
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ]

    # Check PATH environment variable via shutil.which
    for binary_name in ["chromium", "chromium-browser", "google-chrome", "chrome"]:
        found = shutil.which(binary_name)
        if found:
            return found

    for path in candidates:
        if os.path.exists(path):
            return path

    return None


def safe_name(name):
    return re.sub(r'[^\w\s\-]', '', name).strip().replace(' ', '_')


def run_capture():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("❌ Playwright module not installed.")
        print("   Install it using:  pip install playwright")
        sys.exit(1)

    executable_path = find_chromium_executable()
    print("=" * 65)
    print("  KICD CBC Curriculum Designs — Termux Playwright Capture")
    print("=" * 65)

    if executable_path:
        print(f"  🔍 Detected System Chromium: {executable_path}")
    else:
        print("  ℹ️  System Chromium not found, using Playwright default browser binaries.")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    records = []

    with sync_playwright() as p:
        launch_args = {
            "headless": True,
            "args": [
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--single-process" if "termux" in sys.prefix.lower() or os.path.exists("/data/data/com.termux") else "--no-zygote",
            ]
        }

        if executable_path:
            launch_args["executable_path"] = executable_path

        try:
            browser = p.chromium.launch(**launch_args)
        except Exception as e:
            print(f"❌ Failed to launch browser: {e}")
            if executable_path:
                print("   Retrying without explicit executable_path...")
                del launch_args["executable_path"]
                browser = p.chromium.launch(**launch_args)
            else:
                raise e

        # Set mobile/desktop viewport size
        context = browser.new_context(
            viewport={"width": 1280, "height": 960},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        # Cache URLs visited to avoid redundant page loads
        visited_urls = {}

        for item in GRADE_PAGES:
            grade = item["grade"]
            url = item["url"]

            print(f"\n📱 Processing {grade}...")
            grade_dir = OUTPUT_DIR / safe_name(grade)
            grade_dir.mkdir(parents=True, exist_ok=True)

            if url not in visited_urls:
                print(f"   Navigating to {url}")
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    time.sleep(2)
                    # Scroll down gradually to trigger lazy-loaded iframes & images
                    print("   Scrolling to load all sections & iframes...")
                    for s in range(5):
                        page.evaluate("window.scrollBy(0, 1000);")
                        time.sleep(1)
                    page.evaluate("window.scrollTo(0, 0);")
                    time.sleep(1)

                    visited_urls[url] = {
                        "html": page.content(),
                        "text": page.inner_text("body"),
                    }
                except Exception as e:
                    print(f"   ⚠️ Page load warning: {e}")
                    visited_urls[url] = {"html": page.content(), "text": ""}

            # Extract subject sections and iframes from page
            sections = page.query_selector_all("h3, h2")
            iframes = page.query_selector_all("iframe[src*='drive.google.com']")

            print(f"   Found {len(iframes)} embedded document viewer(s)")

            # Full page screenshot
            full_page_ss_path = grade_dir / f"{safe_name(grade)}_full_page.png"
            page.screenshot(path=str(full_page_ss_path), full_page=True)
            print(f"   📸 Saved Full Page Screenshot: {full_page_ss_path.name}")

            # Extract text
            page_text = visited_urls[url]["text"]
            text_file_path = grade_dir / f"{safe_name(grade)}_content.txt"
            text_file_path.write_text(page_text, encoding="utf-8")
            print(f"   📄 Saved Extracted Text: {text_file_path.name}")

            # Capture individual iframe element screenshots
            for idx, iframe_elem in enumerate(iframes, 1):
                try:
                    src = iframe_elem.get_attribute("src") or ""
                    file_id_match = re.search(r'/file/d/([A-Za-z0-9_\-]+)', src)
                    file_id = file_id_match.group(1) if file_id_match else f"doc_{idx}"

                    # Try to locate nearest section title
                    subject_name = f"Document_{idx}"
                    try:
                        heading = iframe_elem.evaluate(
                            "(el) => { let p = el.previousElementSibling; while(p) { if(['H2','H3'].includes(p.tagName)) return p.innerText; p = p.previousElementSibling; } return null; }"
                        )
                        if heading:
                            subject_name = heading.strip()
                    except Exception:
                        pass

                    subject_safe = safe_name(subject_name)
                    iframe_ss_path = grade_dir / f"{subject_safe}_preview.png"

                    # Scroll iframe into view and screenshot
                    iframe_elem.scroll_into_view_if_needed()
                    time.sleep(0.5)
                    iframe_elem.screenshot(path=str(iframe_ss_path))
                    print(f"   📸 [{idx}/{len(iframes)}] {subject_name} -> {iframe_ss_path.name}")

                    records.append({
                        "grade": grade,
                        "subject": subject_name,
                        "file_id": file_id,
                        "screenshot_path": str(iframe_ss_path.relative_to(OUTPUT_DIR)),
                        "text_path": str(text_file_path.relative_to(OUTPUT_DIR)),
                        "captured_at": datetime.now().isoformat()
                    })
                except Exception as err:
                    print(f"   ⚠️ Could not capture iframe {idx}: {err}")

        browser.close()

    # Save manifest
    MANIFEST_FILE.write_text(json.dumps(records, indent=2, ensure_ascii=False))
    print("\n" + "=" * 65)
    print(f"🎉 Capture Complete!")
    print(f"   Manifest: {MANIFEST_FILE}")
    print(f"   Captured Items: {len(records)}")
    print("=" * 65)


if __name__ == "__main__":
    run_capture()