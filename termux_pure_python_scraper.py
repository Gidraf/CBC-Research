#!/usr/bin/env python3
"""
KICD CBC Scraper for Termux — Pure Python (ZERO PIP DEPENDENCIES)
=================================================================
Runs 100% natively on Android Termux without needing Playwright or pip.

Usage in Termux:
  python3 termux_pure_python_scraper.py
"""

import sys
import json
import re
import os
import time
import html
import urllib.request
import urllib.error
import http.cookiejar
from pathlib import Path
from datetime import datetime

# ──────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────
OUTPUT_DIR = Path("kicd_termux_data")
MANIFEST_FILE = OUTPUT_DIR / "manifest.json"

GRADE_PAGES = [
    {"grade": "Pre-Primary_1",                  "slug": "pre-primary",                  "page_id": 7804},
    {"grade": "Pre-Primary_2",                  "slug": "pre-primary",                  "page_id": 7804},
    {"grade": "Grade_1",                        "slug": "lower-primary",                "page_id": 7816},
    {"grade": "Grade_2",                        "slug": "lower-primary",                "page_id": 7816},
    {"grade": "Grade_3",                        "slug": "lower-primary",                "page_id": 7816},
    {"grade": "Grade_4",                        "slug": "grade-four-designs",           "page_id": 7824},
    {"grade": "Grade_5",                        "slug": "grade-five-designs",           "page_id": 7828},
    {"grade": "Grade_6",                        "slug": "grade-six-designs",            "page_id": 7831},
    {"grade": "Grade_7",                        "slug": "grade-seven-designs",          "page_id": 7835},
    {"grade": "Grade_8",                        "slug": "grade-eight-designs",          "page_id": 7843},
    {"grade": "Grade_9",                        "slug": "grade-nine-designs",           "page_id": 7846},
    {"grade": "Grade_10",                       "slug": "grade-ten",                    "page_id": 7472},
    {"grade": "Grade_11",                       "slug": "grade-eleven",                 "page_id": 7661},
    {"grade": "Grade_12",                       "slug": "grade-twelve",                 "page_id": 7791},
    {"grade": "Diploma_in_Teacher_Education",   "slug": "diploma-in-teacher-education", "page_id": 7526},
]

WP_API_BASE = "https://kicd.ac.ke/wp-json/wp/v2/pages"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 13; Termux) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Mobile Safari/537.36"
    )
}


def fetch_url(url, retries=3):
    req = urllib.request.Request(url, headers=HEADERS)
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read()
        except Exception as e:
            if attempt == retries - 1:
                raise e
            time.sleep(2)
    return b""


def safe_name(name):
    return re.sub(r'[^\w\s\-]', '', name).strip().replace(' ', '_')


def extract_page_text_and_links(html_rendered):
    decoded = html.unescape(html_rendered)
    
    # Strip script/style
    clean = re.sub(r'<script[\s\S]*?</script>', '', decoded)
    clean = re.sub(r'<style[\s\S]*?</style>', '', clean)
    
    # Extract plain text
    plain_text = re.sub(r'<[^>]+>', '\n', clean)
    plain_text = "\n".join([line.strip() for line in plain_text.splitlines() if line.strip()])

    # Extract subjects and Google Drive IDs
    records = []
    seen = set()

    pattern = (
        r'<h[23][^>]*>([\s\S]*?)</h[23]>'
        r'[\s\S]{0,200}?'
        r'<iframe[^>]*src=["\']https://drive\.google\.com/file/d/([A-Za-z0-9_\-]+)/preview["\']'
    )
    for m in re.finditer(pattern, decoded, re.IGNORECASE):
        subject_raw, file_id = m.group(1), m.group(2)
        subject = re.sub(r'<[^>]+>', '', subject_raw).strip()
        subject = re.sub(r'\s+', ' ', subject)
        if len(subject) > 80:
            continue
        if file_id not in seen and subject:
            seen.add(file_id)
            records.append({"subject": subject, "file_id": file_id})

    return plain_text, records


def run_termux_scraper():
    print("=" * 65)
    print("  KICD CBC Scraper for Termux (Pure Python)")
    print("=" * 65)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    all_manifest = []
    page_cache = {}

    for item in GRADE_PAGES:
        grade = item["grade"]
        page_id = item["page_id"]

        print(f"\n📱 Processing {grade}...")
        grade_dir = OUTPUT_DIR / grade
        grade_dir.mkdir(parents=True, exist_ok=True)

        if page_id not in page_cache:
            api_url = f"{WP_API_BASE}/{page_id}"
            try:
                raw = fetch_url(api_url)
                data = json.loads(raw)
                page_cache[page_id] = data.get("content", {}).get("rendered", "")
            except Exception as e:
                print(f"   ❌ Error fetching page {page_id}: {e}")
                continue

        rendered_html = page_cache[page_id]
        plain_text, records = extract_page_text_and_links(rendered_html)

        # Save extracted page text
        text_file = grade_dir / f"{grade}_content.txt"
        text_file.write_text(plain_text, encoding="utf-8")
        print(f"   📄 Saved Page Text: {text_file.name}")

        print(f"   Found {len(records)} curriculum subject documents")
        for r in records:
            subj = r["subject"]
            file_id = r["file_id"]

            file_record = {
                "grade": grade,
                "subject": subj,
                "file_id": file_id,
                "drive_url": f"https://drive.google.com/file/d/{file_id}/view",
                "text_path": str(text_file.relative_to(OUTPUT_DIR))
            }
            all_manifest.append(file_record)
            print(f"   ✅ {subj} (Drive ID: {file_id})")

    MANIFEST_FILE.write_text(json.dumps(all_manifest, indent=2, ensure_ascii=False))
    print("\n" + "=" * 65)
    print(f"🎉 Done! Manifest saved: {MANIFEST_FILE}")
    print(f"   Total Documents Indexed: {len(all_manifest)}")
    print("=" * 65)


if __name__ == "__main__":
    run_termux_scraper()
