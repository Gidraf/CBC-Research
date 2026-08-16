import json
import re
import html
import time
import urllib.request
import urllib.parse
import urllib.error
from typing import List, Dict, Any, Optional
import database

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

def gdrive_download_url(file_id: str) -> str:
    return f"https://drive.google.com/uc?export=download&id={file_id}"

def gdrive_view_url(file_id: str) -> str:
    return f"https://drive.google.com/file/d/{file_id}/view"

def fetch_url(url: str, retries: int = 3) -> bytes:
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
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read()
        except Exception:
            if attempt == retries - 1:
                return b""
            time.sleep(1)
    return b""

def parse_page_content(html_content: str, grade: str, subject_filter: Optional[str] = None) -> List[Dict[str, Any]]:
    decoded = html.unescape(html_content)
    records = []
    seen_ids = set()

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
        if len(subject) > 80 and subject.count(' ') > 8:
            seen_ids.add(file_id)
            continue
        if file_id not in seen_ids and subject:
            seen_ids.add(file_id)
            records.append({
                "grade": grade,
                "subject": subject,
                "file_id": file_id,
                "google_drive_url": gdrive_view_url(file_id),
                "download_url": gdrive_download_url(file_id)
            })

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
            best_subject = "Curriculum Design Document"
            for h_pos, h_text in headings:
                if h_pos < iframe_pos and h_text and not h_text.lower().endswith("designs"):
                    best_subject = h_text
            seen_ids.add(file_id)
            records.append({
                "grade": grade,
                "subject": best_subject,
                "file_id": file_id,
                "google_drive_url": gdrive_view_url(file_id),
                "download_url": gdrive_download_url(file_id)
            })

    if subject_filter is not None:
        records = [r for r in records if r["subject"] == subject_filter]

    return records

def extract_and_store_all_kicd_files() -> int:
    database.init_db()
    count = 0

    # 1. Check if manifest.json exists in root or output dir
    for manifest_path in [Path("manifest.json"), Path("kicd_curriculum_designs/manifest.json")]:
        if manifest_path.exists():
            try:
                data = json.loads(manifest_path.read_text())
                records = data.get("records", [data] if "file_id" in data else [])
                for r in records:
                    if "file_id" in r and "grade" in r and "subject" in r:
                        database.upsert_file(
                            grade=r["grade"],
                            subject=r["subject"],
                            file_id=r["file_id"],
                            google_drive_url=r.get("google_drive_url", gdrive_view_url(r["file_id"])),
                            download_url=r.get("download_url", gdrive_download_url(r["file_id"])),
                            local_path=r.get("local_path"),
                            downloaded=r.get("downloaded", False)
                        )
                        count += 1
                if count > 0:
                    print(f"Loaded {count} records from manifest file {manifest_path}")
            except Exception as e:
                print(f"Error loading manifest {manifest_path}: {e}")

    # 2. Scrape KICD grade pages
    page_content_cache = {}
    for grade_info in GRADE_PAGES:
        grade = grade_info["grade"]
        page_id = grade_info["page_id"]
        subject_filter = grade_info.get("subject_filter")

        if page_id in page_content_cache:
            content = page_content_cache[page_id]
        else:
            api_url = f"{WP_API_BASE}/{page_id}"
            raw = fetch_url(api_url)
            if not raw:
                continue
            try:
                page_data = json.loads(raw)
                content = page_data.get("content", {}).get("rendered", "")
                page_content_cache[page_id] = content
            except Exception:
                continue

        if not content:
            continue

        records = parse_page_content(content, grade, subject_filter=subject_filter)
        for r in records:
            database.upsert_file(
                grade=r["grade"],
                subject=r["subject"],
                file_id=r["file_id"],
                google_drive_url=r["google_drive_url"],
                download_url=r["download_url"]
            )
            count += 1

    return count


def add_custom_gdrive_url(url_or_id: str, grade: str = "Custom Grade", subject: str = "Custom Subject") -> Optional[Dict[str, Any]]:
    # Extract file_id from URL or plain string
    file_id_match = re.search(r'([a-zA-Z0-9_-]{25,})', url_or_id)
    if not file_id_match:
        return None
    
    file_id = file_id_match.group(1)
    gdrive_url = gdrive_view_url(file_id)
    dl_url = gdrive_download_url(file_id)
    
    return database.upsert_file(
        grade=grade,
        subject=subject,
        file_id=file_id,
        google_drive_url=gdrive_url,
        download_url=dl_url
    )
