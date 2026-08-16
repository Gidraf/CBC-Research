#!/usr/bin/env python3
"""
No-JS Google Drive PDF Downloader & Form Extractor
==================================================
Simulates a browser with JavaScript DISABLED to extract direct PDF links
from Google Drive and download binary files.
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
import database

DOWNLOADS_DIR = Path("downloads")
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)

def download_gdrive_no_js_python(file_id: str, output_path: str = None) -> bool:
    """
    Simulates a browser with JavaScript DISABLED.
    Google Drive responds to No-JS requests by outputting a plain HTML page
    containing a direct `<form action="...">` or `<a href="...">` download link.
    """
    if not output_path:
        output_path = str(DOWNLOADS_DIR / f"{file_id}_document.pdf")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    cookie_jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))

    initial_url = f"https://drive.google.com/uc?export=download&id={file_id}"
    req = urllib.request.Request(initial_url, headers=headers)

    try:
        with opener.open(req, timeout=30) as resp:
            content_bytes = resp.read()

        if content_bytes.startswith(b'%PDF') and len(content_bytes) >= 5000:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_bytes(content_bytes)
            database.mark_as_downloaded(file_id, output_path, len(content_bytes))
            return True

        content_html = content_bytes.decode('utf-8', errors='ignore')

        direct_link_match = (
            re.search(r'<a\s+id=["\']uc-download-link["\']\s+class=["\'][^"\']*["\']\s+href=["\']([^"\']+)["\']', content_html, re.IGNORECASE) or
            re.search(r'href=["\'](https://drive\.usercontent\.google\.com/download[^"\']+)["\']', content_html, re.IGNORECASE) or
            re.search(r'<form[^>]*action=["\']([^"\']+)["\']', content_html, re.IGNORECASE)
        )

        if direct_link_match:
            download_url = html.unescape(direct_link_match.group(1))
            if not download_url.startswith("http"):
                download_url = urllib.parse.urljoin("https://drive.google.com", download_url)

            req2 = urllib.request.Request(download_url, headers=headers)
            with opener.open(req2, timeout=60) as resp2:
                pdf_bytes = resp2.read()

            if pdf_bytes.startswith(b'%PDF'):
                Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                Path(output_path).write_bytes(pdf_bytes)
                database.mark_as_downloaded(file_id, output_path, len(pdf_bytes))
                return True

        return False

    except Exception as e:
        print(f"Error downloading {file_id}: {e}")
        return False
