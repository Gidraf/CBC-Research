import os
import glob
import shutil
import logging
from pathlib import Path
from PIL import Image
from celery import Celery
import database

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("celery_worker")

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

celery_app = Celery("gdrive_tasks", broker=REDIS_URL, backend=REDIS_URL)

TEMP_SCREENSHOTS_DIR = Path("temp_screenshots")
EXTRACTED_TEXT_DIR = Path("extracted_text")
EXTRACTED_TEXT_DIR.mkdir(parents=True, exist_ok=True)

def extract_text_from_pdf_file(pdf_path: Path, file_id: str) -> str:
    extracted_pages = []
    try:
        import pypdf
        reader = pypdf.PdfReader(str(pdf_path))
        num_pages = len(reader.pages)
        logger.info(f"Extracting all {num_pages} pages from PDF binary {pdf_path}...")
        for idx, page in enumerate(reader.pages, start=1):
            txt = page.extract_text() or ""
            txt = txt.strip()
            if txt:
                header = f"================================================================================\n📄 PAGE {idx} OF {num_pages}\n================================================================================\n\n"
                extracted_pages.append(header + txt)
    except Exception as pdf_err:
        logger.warning(f"PyPDF extraction warning for {pdf_path}: {pdf_err}")
        return ""

    if extracted_pages:
        full_text = "\n\n".join(extracted_pages)
        text_filename = f"{file_id}_extracted.txt"
        text_file_path = EXTRACTED_TEXT_DIR / text_filename
        text_file_path.write_text(full_text, encoding="utf-8")
        database.update_extracted_text(file_id, str(text_file_path))
        logger.info(f"Successfully extracted {len(extracted_pages)} pages via PyPDF -> {text_file_path}")
        return str(text_file_path)
    return ""

def perform_ocr_extraction(file_id: str, dom_text: str = "") -> str:
    # 1. Check if a local downloaded PDF file exists in downloads/ directory
    downloads_dir = Path("downloads")
    pdf_candidates = list(downloads_dir.glob(f"*{file_id}*.pdf")) + list(downloads_dir.glob(f"{file_id}*.pdf"))
    for pdf_file in pdf_candidates:
        if pdf_file.exists() and pdf_file.stat().st_size > 5000:
            res_path = extract_text_from_pdf_file(pdf_file, file_id)
            if res_path:
                session_dir = TEMP_SCREENSHOTS_DIR / file_id
                if session_dir.exists():
                    shutil.rmtree(session_dir, ignore_errors=True)
                return res_path

    session_dir = TEMP_SCREENSHOTS_DIR / file_id
    full_text = ""
    clean_dom = dom_text.strip() if dom_text else ""

    # Process OCR on all recorded screenshots across the full document
    screenshot_files = sorted(session_dir.glob("step_*.png")) if session_dir.exists() else []
    if not screenshot_files and session_dir.exists():
        screenshot_files = sorted(session_dir.glob("*.png")) + sorted(session_dir.glob("*.jpg"))

    logger.info(f"Processing {len(screenshot_files)} screenshots for file {file_id}...")

    extracted_sections = []
    has_tesseract = True
    try:
        import pytesseract
    except ImportError:
        has_tesseract = False

    for idx, img_path in enumerate(screenshot_files, start=1):
        section_text = f"=== SECTION {idx} ==="
        try:
            img = Image.open(img_path)
            if has_tesseract:
                try:
                    text = pytesseract.image_to_string(img)
                    text = text.strip()
                    if text:
                        extracted_sections.append(f"{section_text}\n{text}")
                except Exception as ocr_err:
                    logger.warning(f"Tesseract OCR warning on step {idx}: {ocr_err}")
        except Exception as e:
            logger.error(f"Error reading screenshot {img_path}: {e}")

    ocr_text = "\n\n".join(extracted_sections)

    if clean_dom and ocr_text:
        full_text = f"{clean_dom}\n\n==================== SCREENSHOT OCR TEXT ====================\n\n{ocr_text}"
    elif clean_dom:
        full_text = clean_dom
    elif ocr_text:
        full_text = ocr_text
    else:
        full_text = "No text content extracted."



    # Save to extracted_text/{file_id}_extracted.txt
    text_filename = f"{file_id}_extracted.txt"
    text_file_path = EXTRACTED_TEXT_DIR / text_filename
    text_file_path.write_text(full_text, encoding="utf-8")
    logger.info(f"Extracted text saved to {text_file_path}")

    # DISCARD & CLEANUP TEMPORARY SCREENSHOTS TO SAVE DISK SPACE
    if session_dir.exists():
        try:
            shutil.rmtree(session_dir)
            logger.info(f"Successfully deleted temporary screenshots folder: {session_dir}")
        except Exception as cleanup_err:
            logger.error(f"Failed deleting temporary screenshots {session_dir}: {cleanup_err}")

    # Update SQLite database
    database.update_extracted_text(file_id, str(text_file_path))
    return str(text_file_path)

@celery_app.task(name="extract_text_from_screenshots")
def process_document_screenshots_task(file_id: str, dom_text: str = ""):
    logger.info(f"Celery worker processing extraction for {file_id}")
    return perform_ocr_extraction(file_id, dom_text)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        fid = sys.argv[1]
        print(f"Running manual extraction for {fid}...")
        res = perform_ocr_extraction(fid)
        print("Done ->", res)
