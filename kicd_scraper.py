#!/usr/bin/env python3
"""
KICD CBC Regular Curriculum Designs Scraper & Downloader
=========================================================
Scrapes https://kicd.ac.ke/cbc-materials/curriculum-designs/regular-curriculum-designs/
Records: Grade, Subject, Google Drive URL
Downloads PDFs from Google Drive
Saves manifest to JSON and zips everything up.

Usage:
    # Step 1: Scrape metadata (fast, no downloads)
    python3 kicd_scraper.py scrape

    # Step 2: Download all PDFs and create zip
    python3 kicd_scraper.py download

    # Or do both at once:
    python3 kicd_scraper.py all
"""

import sys
import json
import re
import os
import time
import zipfile
import html
import urllib.request
import urllib.error
import http.cookiejar
from pathlib import Path
from datetime import datetime

# ──────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────
OUTPUT_DIR = Path("kicd_curriculum_designs")
MANIFEST_FILE = OUTPUT_DIR / "manifest.json"

# All Regular Curriculum Design grade pages
# NOTE: KICD publishes a SINGLE combined curriculum design for the entire
# Lower Primary band (Grades 1-3). There is no separate page per grade.
# We list them as Grade 1, Grade 2, Grade 3 below — each maps to the same
# page_id (7816) and the same documents. The PDFs themselves cover all three grades.
#
# Similarly, Pre-Primary has PP1 and PP2 on a single page (7804).
GRADE_PAGES = [
    {"grade": "Pre-Primary 1 (PP1)",           "slug": "pre-primary",                  "page_id": 7804,  "url": "https://kicd.ac.ke/cbc-materials/pre-primary/",                                            "subject_filter": "Pre-Primary 1"},
    {"grade": "Pre-Primary 2 (PP2)",           "slug": "pre-primary",                  "page_id": 7804,  "url": "https://kicd.ac.ke/cbc-materials/pre-primary/",                                            "subject_filter": "Pre-Primary 2"},
    {"grade": "Grade 1",                       "slug": "lower-primary",                "page_id": 7816,  "url": "https://kicd.ac.ke/cbc-materials/lower-primary/",                                          "subject_filter": None},
    {"grade": "Grade 2",                       "slug": "lower-primary",                "page_id": 7816,  "url": "https://kicd.ac.ke/cbc-materials/lower-primary/",                                          "subject_filter": None},
    {"grade": "Grade 3",                       "slug": "lower-primary",                "page_id": 7816,  "url": "https://kicd.ac.ke/cbc-materials/lower-primary/",                                          "subject_filter": None},
    {"grade": "Grade 4",                       "slug": "grade-four-designs",           "page_id": 7824,  "url": "https://kicd.ac.ke/cbc-materials/curriculum-designs/grade-four-designs/",               "subject_filter": None},
    {"grade": "Grade 5",                       "slug": "grade-five-designs",           "page_id": 7828,  "url": "https://kicd.ac.ke/cbc-materials/curriculum-designs/grade-five-designs/",               "subject_filter": None},
    {"grade": "Grade 6",                       "slug": "grade-six-designs",            "page_id": 7831,  "url": "https://kicd.ac.ke/cbc-materials/curriculum-designs/grade-six-designs/",                "subject_filter": None},
    {"grade": "Grade 7",                       "slug": "grade-seven-designs",          "page_id": 7835,  "url": "https://kicd.ac.ke/cbc-materials/curriculum-designs/grade-seven-designs/",             "subject_filter": None},
    {"grade": "Grade 8",                       "slug": "grade-eight-designs",          "page_id": 7843,  "url": "https://kicd.ac.ke/cbc-materials/curriculum-designs/grade-eight-designs/",             "subject_filter": None},
    {"grade": "Grade 9",                       "slug": "grade-nine-designs",           "page_id": 7846,  "url": "https://kicd.ac.ke/cbc-materials/curriculum-designs/grade-nine-designs/",              "subject_filter": None},
    {"grade": "Grade 10",                      "slug": "grade-ten",                    "page_id": 7472,  "url": "https://kicd.ac.ke/cbc-materials/curriculum-designs/grade-ten/",                        "subject_filter": None},
    {"grade": "Grade 11",                      "slug": "grade-eleven",                 "page_id": 7661,  "url": "https://kicd.ac.ke/cbc-materials/curriculum-designs/grade-eleven/",                    "subject_filter": None},
    {"grade": "Grade 12",                      "slug": "grade-twelve",                 "page_id": 7791,  "url": "https://kicd.ac.ke/cbc-materials/curriculum-designs/grade-twelve/",                    "subject_filter": None},
    {"grade": "Diploma in Teacher Education",  "slug": "diploma-in-teacher-education", "page_id": 7526,  "url": "https://kicd.ac.ke/cbc-materials/curriculum-designs/diploma-in-teacher-education/",   "subject_filter": None},
]

WP_API_BASE = "https://kicd.ac.ke/wp-json/wp/v2/pages"


# ──────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────

def safe_filename(name):
    """Convert a name to a safe filesystem filename."""
    cleaned = re.sub(r'[^\w\s\-]', '', name).strip().replace(' ', '_')
    return cleaned or "unknown"


def gdrive_download_url(file_id):
    return f"https://drive.google.com/uc?export=download&id={file_id}"


def gdrive_view_url(file_id):
    return f"https://drive.google.com/file/d/{file_id}/view"


def fetch_url(url, retries=3):
    """Fetch a URL with retries."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }
    req = urllib.request.Request(url, headers=headers)
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            print(f"    HTTP {e.code} for {url}")
            if e.code == 429:
                wait = 10 * (attempt + 1)
                print(f"    Rate limited. Waiting {wait}s...")
                time.sleep(wait)
            elif attempt == retries - 1:
                raise
            else:
                time.sleep(3)
        except Exception as e:
            if attempt == retries - 1:
                raise
            time.sleep(3)
    return b""


def fetch_gdrive_file(file_id, pdf_path, txt_path, retries=3):
    """
    Download a file from Google Drive bypassing JavaScript preview wrappers.
    Uses non-JS CLI headers (Wget/curl) so Google Drive serves raw binary PDF streams directly.
    """
    # Non-JS CLI headers cause Google Drive to skip client-side JS app rendering
    headers = {
        "User-Agent": "Wget/1.21.3 (linux-gnu)",
        "Accept": "*/*",
        "Accept-Encoding": "identity",
        "Connection": "Keep-Alive"
    }

    cookie_jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
    opener.addheaders = list(headers.items())

    urls_to_try = [
        f"https://drive.usercontent.google.com/download?id={file_id}&export=download&confirm=t",
        f"https://drive.google.com/uc?export=download&id={file_id}&confirm=t",
        f"https://drive.google.com/uc?export=download&id={file_id}",
        f"https://drive.google.com/uc?id={file_id}&export=download",
    ]

    pdf_data = None

    for attempt in range(retries):
        for base_url in urls_to_try:
            try:
                req = urllib.request.Request(base_url, headers=headers)
                resp = opener.open(req, timeout=45)
                data = resp.read()

                # Handle Google Drive virus-scan confirmation token
                if (b"confirm=" in data or b"download_warning" in data) and b"Google" in data:
                    match = re.search(rb'confirm=([0-9A-Za-z_\-]+)', data) or re.search(rb'name="confirm"\s+value="([^"]+)"', data)
                    if match:
                        confirm = match.group(1).decode()
                        confirm_url = f"https://drive.google.com/uc?export=download&id={file_id}&confirm={confirm}"
                        req_confirm = urllib.request.Request(confirm_url, headers=headers)
                        resp2 = opener.open(req_confirm, timeout=60)
                        data = resp2.read()

                # Validate genuine PDF magic bytes (%PDF) and size > 5KB
                if data.startswith(b'%PDF') and len(data) >= 5000:
                    pdf_data = data
                    break
            except Exception:
                pass

        if pdf_data:
            break

        if attempt < retries - 1:
            time.sleep(2 * (attempt + 1))

    # If valid PDF was obtained:
    if pdf_data:
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        pdf_path.write_bytes(pdf_data)
        if txt_path.exists():
            txt_path.unlink(missing_ok=True)
        return True, "pdf", len(pdf_data)

    # ── FALLBACK: Scrape viewer text & save as .txt ──
    print(f"          ⚠️  PDF download restricted/failed. Scraping text viewer fallback...")
    fallback_text = []

    view_urls = [
        f"https://drive.google.com/file/d/{file_id}/preview",
        f"https://drive.google.com/file/d/{file_id}/view",
    ]

    for vurl in view_urls:
        try:
            resp = opener.open(vurl, timeout=30)
            html_raw = resp.read().decode('utf-8', errors='ignore')

            # Extract title
            title_m = re.search(r'<title>(.*?)</title>', html_raw, re.IGNORECASE)
            if title_m:
                title_clean = html.unescape(title_m.group(1)).strip()
                fallback_text.append(f"DOCUMENT TITLE: {title_clean}\n")

            # Extract any embedded text snippets or JSON metadata in preview
            text_chunks = re.findall(r'\\u[0-9a-fA-F]{4}|[A-Za-z0-9\s.,;:!?()\/\-\n]{15,}', html_raw)
            clean_html = re.sub(r'<script[\s\S]*?</script>', '', html_raw)
            clean_html = re.sub(r'<style[\s\S]*?</style>', '', clean_html)
            text_lines = [line.strip() for line in re.sub(r'<[^>]+>', '\n', clean_html).splitlines() if line.strip()]

            if text_lines:
                fallback_text.append("=== EXTRACTED VIEW CONTENT ===")
                fallback_text.extend(text_lines[:150])
                break
        except Exception as e:
            fallback_text.append(f"Error accessing viewer URL ({vurl}): {e}")

    final_txt_content = (
        f"SOURCE GOOGLE DRIVE URL: https://drive.google.com/file/d/{file_id}/view\n"
        f"DOWNLOAD DATE: {datetime.now().isoformat()}\n"
        f"STATUS: PDF Direct Download Restricted by Owner / Permission Needed.\n\n"
        + "\n".join(fallback_text)
    )

    txt_path.parent.mkdir(parents=True, exist_ok=True)
    txt_path.write_text(final_txt_content, encoding='utf-8')

    # Remove fake 1KB pdf if present
    if pdf_path.exists() and (pdf_path.stat().st_size < 5000 or not pdf_path.read_bytes().startswith(b'%PDF')):
        pdf_path.unlink(missing_ok=True)

    return True, "txt", len(final_txt_content.encode('utf-8'))


# ──────────────────────────────────────────────
# SCRAPING
# ──────────────────────────────────────────────

def parse_page_content(html_content, grade, subject_filter=None):
    """
    Parse a KICD grade page HTML content.
    Extracts subjects and their Google Drive file IDs.

    subject_filter: if set, only keep records whose subject exactly matches this string.
    Returns list of record dicts.
    """
    decoded = html.unescape(html_content)
    records = []
    seen_ids = set()

    # Strategy 1: h2/h3 heading directly followed by iframe
    pattern = (
        r'<h[23][^>]*>([\s\S]*?)</h[23]>'
        r'[\s\S]{0,200}?'
        r'<iframe[^>]*src=["\']https://drive\.google\.com/file/d/([A-Za-z0-9_\-]+)/preview["\']'
    )
    for m in re.finditer(pattern, decoded, re.IGNORECASE):
        subject_raw = m.group(1)
        file_id = m.group(2)
        subject = re.sub(r'<[^>]+>', '', subject_raw).strip()
        subject = re.sub(r'\s+', ' ', subject)
        # Skip entries that look like a TOC dump (long, contains multiple subjects)
        if len(subject) > 80 and subject.count(' ') > 8:
            # Still register the file_id to avoid duplicates later
            seen_ids.add(file_id)
            continue
        if file_id not in seen_ids and subject:
            seen_ids.add(file_id)
            records.append(_make_record(grade, subject, file_id))

    # Strategy 2: Find all headings and iframes by position and pair them
    if not records:
        heading_re = re.compile(r'<h[23][^>]*>([\s\S]*?)</h[23]>', re.IGNORECASE)
        iframe_re = re.compile(
            r'<iframe[^>]*src=["\']https://drive\.google\.com/file/d/([A-Za-z0-9_\-]+)/preview["\']',
            re.IGNORECASE
        )
        headings = [(m.start(), re.sub(r'<[^>]+>', '', m.group(1)).strip())
                    for m in heading_re.finditer(decoded)]
        iframes = [(m.start(), m.group(1)) for m in iframe_re.finditer(decoded)]

        for iframe_pos, file_id in iframes:
            if file_id in seen_ids:
                continue
            # Find the nearest heading before this iframe
            best_subject = "Unknown"
            for h_pos, h_text in headings:
                if h_pos < iframe_pos and h_text and h_text.lower() not in ("grade 4 designs", "grade 5 designs", "grade 6 designs",
                                                                              "grade 7 designs", "grade 8 designs", "grade 9 designs",
                                                                              "grade 10 designs", "grade 11 designs", "grade 12 designs",
                                                                              "pre-primary designs", "lower primary designs"):
                    best_subject = h_text
            seen_ids.add(file_id)
            records.append(_make_record(grade, best_subject, file_id))

    # Apply subject_filter if provided (e.g. to split PP1 / PP2 from a shared page)
    if subject_filter is not None:
        records = [r for r in records if r["subject"] == subject_filter]

    return records


def _make_record(grade, subject, file_id):
    return {
        "grade": grade,
        "subject": subject,
        "file_id": file_id,
        "google_drive_url": gdrive_view_url(file_id),
        "download_url": gdrive_download_url(file_id),
        "local_path": None,
        "downloaded": False,
    }


def scrape_all_grades():
    """Scrape all grade pages and return metadata records."""
    all_records = []

    print("=" * 65)
    print("  KICD CBC Curriculum Designs — Metadata Scraper")
    print("=" * 65)
    print(f"  Fetching {len(GRADE_PAGES)} grade entries via KICD WordPress API\n")

    # Cache fetched page content by page_id to avoid duplicate HTTP requests
    page_content_cache = {}

    for grade_info in GRADE_PAGES:
        grade       = grade_info["grade"]
        page_id     = grade_info["page_id"]
        subject_filter = grade_info.get("subject_filter")

        print(f"📚  {grade}")

        # Use cached content if we already fetched this page
        if page_id in page_content_cache:
            content = page_content_cache[page_id]
        else:
            api_url = f"{WP_API_BASE}/{page_id}"
            try:
                raw = fetch_url(api_url)
                page_data = json.loads(raw)
                content = page_data.get("content", {}).get("rendered", "")
                page_content_cache[page_id] = content
            except Exception as e:
                print(f"    ❌  Error fetching page: {e}")
                continue
            time.sleep(0.5)

        if not content:
            print(f"    ⚠️  No content returned")
            continue

        records = parse_page_content(content, grade, subject_filter=subject_filter)

        if records:
            for r in records:
                print(f"    ✅  {r['subject']}")
            all_records.extend(records)
        else:
            print(f"    ⚠️  No subjects found — structure may differ")

    return all_records


# ──────────────────────────────────────────────
# MANIFEST I/O
# ──────────────────────────────────────────────

def save_manifest(records):
    """Save records to JSON manifest."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    by_grade = {}
    for r in records:
        by_grade.setdefault(r["grade"], []).append(r)

    manifest = {
        "source": "https://kicd.ac.ke/cbc-materials/curriculum-designs/regular-curriculum-designs/",
        "scraped_at": datetime.now().isoformat(),
        "total_documents": len(records),
        "grades_count": len(by_grade),
        "records": records,
    }

    MANIFEST_FILE.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    print(f"\n📄  Manifest saved → {MANIFEST_FILE}")
    print(f"    Total documents : {len(records)}")
    print(f"    Grades covered  : {len(by_grade)}")


def load_manifest():
    """Load records from manifest file."""
    if not MANIFEST_FILE.exists():
        print(f"❌  Manifest not found: {MANIFEST_FILE}")
        print("    Run first:  python3 kicd_scraper.py scrape")
        sys.exit(1)
    data = json.loads(MANIFEST_FILE.read_text())
    return data["records"]


# ──────────────────────────────────────────────
# DOWNLOADING
# ──────────────────────────────────────────────

def download_all(records):
    """Download all PDFs from Google Drive with retry and .txt fallback. Updates records in-place."""
    total = len(records)
    success = 0
    failed = 0
    skipped = 0

    print("\n" + "=" * 65)
    print("  Downloading Files from Google Drive")
    print("=" * 65)
    print(f"  Files to download: {total}\n")

    for i, record in enumerate(records, 1):
        grade   = record["grade"]
        subject = record["subject"]
        file_id = record["file_id"]

        grade_dir        = safe_filename(grade)
        base_name        = safe_filename(subject)
        pdf_rel_path     = os.path.join(grade_dir, base_name + ".pdf")
        txt_rel_path     = os.path.join(grade_dir, base_name + ".txt")

        pdf_abs_path     = OUTPUT_DIR / pdf_rel_path
        txt_abs_path     = OUTPUT_DIR / txt_rel_path

        print(f"[{i:3d}/{total}] {grade} / {subject}")

        # Skip if already validly downloaded PDF (size > 5KB & magic %PDF)
        if pdf_abs_path.exists() and pdf_abs_path.stat().st_size >= 5000:
            try:
                if pdf_abs_path.read_bytes().startswith(b'%PDF'):
                    size = pdf_abs_path.stat().st_size
                    print(f"          ✅  Already downloaded PDF ({size:,} bytes) — skipping")
                    record["local_path"] = pdf_rel_path
                    record["file_type"] = "pdf"
                    record["downloaded"] = True
                    skipped += 1
                    success += 1
                    continue
            except Exception:
                pass

        # Skip if already downloaded .txt fallback
        if txt_abs_path.exists() and txt_abs_path.stat().st_size > 100:
            size = txt_abs_path.stat().st_size
            print(f"          ✅  Already saved TXT fallback ({size:,} bytes) — skipping")
            record["local_path"] = txt_rel_path
            record["file_type"] = "txt"
            record["downloaded"] = True
            skipped += 1
            success += 1
            continue

        ok, ftype, size_bytes = fetch_gdrive_file(file_id, pdf_abs_path, txt_abs_path)

        if ok:
            chosen_rel = pdf_rel_path if ftype == "pdf" else txt_rel_path
            print(f"          ✅  Saved ({ftype.upper()}) → {chosen_rel}  ({size_bytes:,} bytes)")
            record["local_path"] = chosen_rel
            record["file_type"] = ftype
            record["downloaded"] = True
            success += 1
        else:
            print(f"          ❌  Failed — {gdrive_view_url(file_id)}")
            record["local_path"] = None
            record["file_type"] = None
            record["downloaded"] = False
            failed += 1

        time.sleep(1.2)  # Be polite to Google Drive

    print(f"\n  ✅  Downloaded : {success - skipped}")
    print(f"  ⏭️   Skipped    : {skipped}")
    if failed:
        print(f"  ❌  Failed     : {failed}")

    return records


# ──────────────────────────────────────────────
# ZIP
# ──────────────────────────────────────────────

def create_zip(records):
    """Package all downloaded PDFs + manifest into a timestamped ZIP."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_path = Path(f"kicd_curriculum_designs_{timestamp}.zip")

    downloaded = [r for r in records if r.get("downloaded") and r.get("local_path")]

    print(f"\n📦  Creating ZIP archive...")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # Add manifest
        if MANIFEST_FILE.exists():
            zf.write(MANIFEST_FILE, "manifest.json")

        # Add PDFs
        for record in downloaded:
            abs_path = OUTPUT_DIR / record["local_path"]
            if abs_path.exists():
                zf.write(abs_path, record["local_path"])

    size_mb = zip_path.stat().st_size / (1024 * 1024)
    print(f"    ✅  {zip_path}  ({size_mb:.1f} MB, {len(downloaded)} files)")
    return zip_path


# ──────────────────────────────────────────────
# PRINT SUMMARY TABLE
# ──────────────────────────────────────────────

def print_summary(records):
    print("\n" + "=" * 90)
    print("  SCRAPED RECORDS")
    print("=" * 90)
    print(f"  {'#':<5} {'Grade':<35} {'Subject':<35} {'Google Drive URL'}")
    print("  " + "-" * 88)

    current_grade = None
    for idx, r in enumerate(records, 1):
        if r["grade"] != current_grade:
            if current_grade is not None:
                print()
            current_grade = r["grade"]
        print(f"  {idx:<5} {r['grade']:<35} {r['subject']:<35} {r['google_drive_url']}")

    print("=" * 90)
    print(f"  Total: {len(records)} curriculum design documents across "
          f"{len(set(r['grade'] for r in records))} grade levels")


# ──────────────────────────────────────────────
# COMMANDS
# ──────────────────────────────────────────────

def cmd_scrape():
    records = scrape_all_grades()
    if not records:
        print("\n❌  No records scraped. Check connectivity.")
        sys.exit(1)
    save_manifest(records)
    print_summary(records)
    print(f"\nNext →  python3 {sys.argv[0]} download")


def cmd_download():
    records = load_manifest()
    print(f"  Loaded {len(records)} records from manifest")
    records = download_all(records)
    save_manifest(records)
    zip_path = create_zip(records)
    print(f"\n🎉  Done!  ZIP file: {zip_path}")


def cmd_all():
    records = scrape_all_grades()
    if not records:
        print("\n❌  No records scraped.")
        sys.exit(1)
    save_manifest(records)
    print_summary(records)
    records = download_all(records)
    save_manifest(records)
    zip_path = create_zip(records)
    print(f"\n🎉  Done!  ZIP file: {zip_path}")


COMMANDS = {
    "scrape":   (cmd_scrape,   "Scrape metadata only (no downloads)"),
    "download": (cmd_download, "Download PDFs using saved manifest, then ZIP"),
    "all":      (cmd_all,      "Scrape + download + ZIP in one shot"),
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        print("Available commands:\n")
        for name, (_, desc) in COMMANDS.items():
            print(f"  python3 {sys.argv[0]} {name:<12}  {desc}")
        print()
        sys.exit(1)

    COMMANDS[sys.argv[1]][0]()


if __name__ == "__main__":
    main()