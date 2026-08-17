import os
import json
import sqlite3
import logging
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional

logger = logging.getLogger("database")

DB_PATH = Path("gdrive_files.db")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@postgres:5432/cbc_research")

_use_pg = False

def get_connection():
    global _use_pg
    if DATABASE_URL and DATABASE_URL.startswith("postgresql"):
        try:
            import psycopg2
            from psycopg2.extras import RealDictCursor
            conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
            _use_pg = True
            return conn
        except Exception as e:
            logger.warning(f"PostgreSQL connection to {DATABASE_URL} failed ({e}), falling back to SQLite ({DB_PATH})")

    _use_pg = False
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def is_pg() -> bool:
    return _use_pg

def init_db():
    conn = get_connection()
    try:
        cursor = conn.cursor()
        if is_pg():
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS files (
                    id SERIAL PRIMARY KEY,
                    grade VARCHAR(255) NOT NULL,
                    subject VARCHAR(255) NOT NULL,
                    file_id VARCHAR(255) UNIQUE NOT NULL,
                    google_drive_url TEXT NOT NULL,
                    download_url TEXT NOT NULL,
                    downloaded INTEGER DEFAULT 0,
                    local_path TEXT,
                    file_size BIGINT DEFAULT 0,
                    notes TEXT,
                    text_path TEXT,
                    text_extracted INTEGER DEFAULT 0,
                    total_pages INTEGER DEFAULT 67,
                    fetched_pages_json TEXT DEFAULT '[]',
                    last_page INTEGER DEFAULT 1,
                    last_scroll_pos INTEGER DEFAULT 0,
                    extracted_content TEXT,
                    minio_object_key TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS page_extractions (
                    id SERIAL PRIMARY KEY,
                    file_id VARCHAR(255) NOT NULL REFERENCES files(file_id) ON DELETE CASCADE,
                    page_number INTEGER NOT NULL,
                    total_pages INTEGER NOT NULL DEFAULT 67,
                    page_text TEXT,
                    char_count INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(file_id, page_number)
                );

                CREATE INDEX IF NOT EXISTS idx_files_file_id ON files(file_id);
                CREATE INDEX IF NOT EXISTS idx_files_grade ON files(grade);
                CREATE INDEX IF NOT EXISTS idx_files_downloaded ON files(downloaded);
                CREATE INDEX IF NOT EXISTS idx_page_ext_fid ON page_extractions(file_id);
            """)
            conn.commit()
            logger.info("Initialized PostgreSQL database tables successfully.")
        else:
            cursor.executescript("""
                CREATE TABLE IF NOT EXISTS files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    grade TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    file_id TEXT UNIQUE NOT NULL,
                    google_drive_url TEXT NOT NULL,
                    download_url TEXT NOT NULL,
                    downloaded INTEGER DEFAULT 0,
                    local_path TEXT,
                    file_size INTEGER DEFAULT 0,
                    notes TEXT,
                    text_path TEXT,
                    text_extracted INTEGER DEFAULT 0,
                    total_pages INTEGER DEFAULT 67,
                    fetched_pages_json TEXT DEFAULT '[]',
                    last_page INTEGER DEFAULT 1,
                    last_scroll_pos INTEGER DEFAULT 0,
                    extracted_content TEXT,
                    minio_object_key TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS page_extractions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_id TEXT NOT NULL,
                    page_number INTEGER NOT NULL,
                    total_pages INTEGER NOT NULL DEFAULT 67,
                    page_text TEXT,
                    char_count INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(file_id, page_number)
                );
            """)

            try: cursor.execute("ALTER TABLE files ADD COLUMN text_path TEXT;")
            except Exception: pass
            try: cursor.execute("ALTER TABLE files ADD COLUMN text_extracted INTEGER DEFAULT 0;")
            except Exception: pass
            try: cursor.execute("ALTER TABLE files ADD COLUMN last_scroll_pos INTEGER DEFAULT 0;")
            except Exception: pass
            try: cursor.execute("ALTER TABLE files ADD COLUMN last_page INTEGER DEFAULT 1;")
            except Exception: pass
            try: cursor.execute("ALTER TABLE files ADD COLUMN total_pages INTEGER DEFAULT 67;")
            except Exception: pass
            try: cursor.execute("ALTER TABLE files ADD COLUMN fetched_pages_json TEXT DEFAULT '[]';")
            except Exception: pass
            try: cursor.execute("ALTER TABLE files ADD COLUMN extracted_content TEXT;")
            except Exception: pass
            try: cursor.execute("ALTER TABLE files ADD COLUMN minio_object_key TEXT;")
            except Exception: pass

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_file_id ON files(file_id);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_grade ON files(grade);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_downloaded ON files(downloaded);")
            conn.commit()

        sync_disk_files_status()
    finally:
        conn.close()

def upsert_file(grade: str, subject: str, file_id: str, google_drive_url: str, download_url: str, local_path: Optional[str] = None, downloaded: bool = False) -> Dict[str, Any]:
    now = datetime.now().isoformat()
    conn = get_connection()
    try:
        cursor = conn.cursor()
        if is_pg():
            cursor.execute("""
                INSERT INTO files (grade, subject, file_id, google_drive_url, download_url, downloaded, local_path, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT(file_id) DO UPDATE SET
                    grade = EXCLUDED.grade,
                    subject = EXCLUDED.subject,
                    google_drive_url = EXCLUDED.google_drive_url,
                    download_url = EXCLUDED.download_url,
                    updated_at = NOW()
            """, (grade, subject, file_id, google_drive_url, download_url, 1 if downloaded else 0, local_path))
        else:
            cursor.execute("""
                INSERT INTO files (grade, subject, file_id, google_drive_url, download_url, downloaded, local_path, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(file_id) DO UPDATE SET
                    grade = excluded.grade,
                    subject = excluded.subject,
                    google_drive_url = excluded.google_drive_url,
                    download_url = excluded.download_url,
                    updated_at = excluded.updated_at
            """, (grade, subject, file_id, google_drive_url, download_url, 1 if downloaded else 0, local_path, now))
        conn.commit()
    finally:
        conn.close()
    return get_file_by_id(file_id)

def sync_disk_files_status():
    """Scans disk directories and updates database status for backward compatibility."""
    downloads_dir = Path("downloads")
    extracted_dir = Path("extracted_text")
    
    conn = get_connection()
    try:
        cursor = conn.cursor()
        
        # 1. Sync files where local_path or text_path or extracted_content is populated
        cursor.execute("""
            UPDATE files 
            SET downloaded = 1 
            WHERE (local_path IS NOT NULL AND local_path != '') 
               OR (text_path IS NOT NULL AND text_path != '')
               OR (extracted_content IS NOT NULL AND extracted_content != '');
        """)
        
        # 2. Check extracted_text directory for text files
        if extracted_dir.exists():
            for txt_file in extracted_dir.glob("*_extracted.txt"):
                file_id = txt_file.name.replace("_extracted.txt", "")
                content = txt_file.read_text(encoding="utf-8") if txt_file.stat().st_size > 0 else None
                if is_pg():
                    if content:
                        cursor.execute("""
                            UPDATE files 
                            SET downloaded = 1, text_extracted = 1, text_path = %s, extracted_content = COALESCE(extracted_content, %s)
                            WHERE file_id = %s;
                        """, (str(txt_file), content, file_id))
                    else:
                        cursor.execute("""
                            UPDATE files 
                            SET downloaded = 1, text_extracted = 1, text_path = %s
                            WHERE file_id = %s;
                        """, (str(txt_file), file_id))
                else:
                    if content:
                        cursor.execute("""
                            UPDATE files 
                            SET downloaded = 1, text_extracted = 1, text_path = ?, extracted_content = COALESCE(extracted_content, ?)
                            WHERE file_id = ?;
                        """, (str(txt_file), content, file_id))
                    else:
                        cursor.execute("""
                            UPDATE files 
                            SET downloaded = 1, text_extracted = 1, text_path = ?
                            WHERE file_id = ?;
                        """, (str(txt_file), file_id))
                    
        # 3. Check downloads directory for PDF files
        if downloads_dir.exists():
            for pdf_file in downloads_dir.glob("*"):
                if pdf_file.is_file() and pdf_file.stat().st_size > 0:
                    ph = "%s" if is_pg() else "?"
                    cursor.execute(f"SELECT file_id FROM files")
                    rows = cursor.fetchall()
                    for r in rows:
                        fid = r["file_id"]
                        if fid in pdf_file.name:
                            cursor.execute(f"UPDATE files SET downloaded = 1, local_path = {ph} WHERE file_id = {ph}", (str(pdf_file), fid))

        conn.commit()
    finally:
        conn.close()

def get_all_files(grade_filter: Optional[str] = None, status_filter: Optional[str] = None, search: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        ph = "%s" if is_pg() else "?"
        query = "SELECT * FROM files WHERE 1=1"
        params = []
        
        if grade_filter:
            query += f" AND grade = {ph}"
            params.append(grade_filter)
            
        if status_filter in ["completed", "downloaded"]:
            query += " AND (downloaded = 1 OR text_extracted = 1 OR (local_path IS NOT NULL AND local_path != '') OR (text_path IS NOT NULL AND text_path != ''))"
        elif status_filter == "in_progress":
            query += " AND (downloaded = 0 AND text_extracted = 0 AND (local_path IS NULL OR local_path = '') AND (text_path IS NULL OR text_path = '')) AND (last_page > 1 OR (fetched_pages_json IS NOT NULL AND fetched_pages_json != '[]'))"
        elif status_filter in ["todo", "pending"]:
            query += " AND (downloaded = 0 AND text_extracted = 0 AND (local_path IS NULL OR local_path = '') AND (text_path IS NULL OR text_path = '')) AND (last_page <= 1 AND (fetched_pages_json IS NULL OR fetched_pages_json = '[]'))"
            
        if search:
            query += f" AND (subject LIKE {ph} OR grade LIKE {ph} OR file_id LIKE {ph})"
            pattern = f"%{search}%"
            params.extend([pattern, pattern, pattern])
            
        query += " ORDER BY grade ASC, subject ASC"
        cursor.execute(query, params)
        rows = cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

def get_file_by_id(file_id: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        ph = "%s" if is_pg() else "?"
        cursor.execute(f"SELECT * FROM files WHERE file_id = {ph}", (file_id,))
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def get_file_by_db_id(db_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        ph = "%s" if is_pg() else "?"
        cursor.execute(f"SELECT * FROM files WHERE id = {ph}", (db_id,))
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def mark_as_downloaded(file_id: str, local_path: Optional[str] = None, file_size: int = 0, minio_object_key: Optional[str] = None) -> Optional[Dict[str, Any]]:
    now = datetime.now().isoformat()
    conn = get_connection()
    try:
        cursor = conn.cursor()
        ph = "%s" if is_pg() else "?"
        time_exp = "NOW()" if is_pg() else "?"

        if local_path and minio_object_key:
            sql = f"UPDATE files SET downloaded = 1, local_path = {ph}, file_size = {ph}, minio_object_key = {ph}, updated_at = {time_exp} WHERE file_id = {ph}"
            args = (local_path, file_size, minio_object_key, file_id) if is_pg() else (local_path, file_size, minio_object_key, now, file_id)
        elif local_path:
            sql = f"UPDATE files SET downloaded = 1, local_path = {ph}, file_size = {ph}, updated_at = {time_exp} WHERE file_id = {ph}"
            args = (local_path, file_size, file_id) if is_pg() else (local_path, file_size, now, file_id)
        else:
            sql = f"UPDATE files SET downloaded = 1, updated_at = {time_exp} WHERE file_id = {ph}"
            args = (file_id,) if is_pg() else (now, file_id)

        cursor.execute(sql, args)
        conn.commit()
    finally:
        conn.close()
    return get_file_by_id(file_id)

def toggle_downloaded(file_id: str) -> Optional[Dict[str, Any]]:
    file_record = get_file_by_id(file_id)
    if not file_record:
        return None
    new_status = 0 if file_record["downloaded"] == 1 else 1
    now = datetime.now().isoformat()
    conn = get_connection()
    try:
        cursor = conn.cursor()
        ph = "%s" if is_pg() else "?"
        time_exp = "NOW()" if is_pg() else "?"
        args = (new_status, file_id) if is_pg() else (new_status, now, file_id)
        cursor.execute(f"UPDATE files SET downloaded = {ph}, updated_at = {time_exp} WHERE file_id = {ph}", args)
        conn.commit()
    finally:
        conn.close()
    return get_file_by_id(file_id)

def update_extracted_text(file_id: str, text_path: str, content: Optional[str] = None) -> Optional[Dict[str, Any]]:
    now = datetime.now().isoformat()
    conn = get_connection()
    try:
        cursor = conn.cursor()
        ph = "%s" if is_pg() else "?"
        time_exp = "NOW()" if is_pg() else "?"

        if content is not None:
            sql = f"UPDATE files SET downloaded = 1, text_extracted = 1, text_path = {ph}, extracted_content = {ph}, updated_at = {time_exp} WHERE file_id = {ph}"
            args = (text_path, content, file_id) if is_pg() else (text_path, content, now, file_id)
        else:
            sql = f"UPDATE files SET downloaded = 1, text_extracted = 1, text_path = {ph}, updated_at = {time_exp} WHERE file_id = {ph}"
            args = (text_path, file_id) if is_pg() else (text_path, now, file_id)

        cursor.execute(sql, args)
        conn.commit()
    finally:
        conn.close()
    return get_file_by_id(file_id)

def save_page_extraction(file_id: str, page_number: int, total_pages: int, page_text: str):
    """Saves/upserts individual per-page text extractions directly into PostgreSQL/DB."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        char_count = len(page_text) if page_text else 0
        if is_pg():
            cursor.execute("""
                INSERT INTO page_extractions (file_id, page_number, total_pages, page_text, char_count, created_at)
                VALUES (%s, %s, %s, %s, %s, NOW())
                ON CONFLICT(file_id, page_number) DO UPDATE SET
                    total_pages = EXCLUDED.total_pages,
                    page_text = EXCLUDED.page_text,
                    char_count = EXCLUDED.char_count,
                    created_at = NOW();
            """, (file_id, page_number, total_pages, page_text, char_count))
        else:
            cursor.execute("""
                INSERT INTO page_extractions (file_id, page_number, total_pages, page_text, char_count, created_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(file_id, page_number) DO UPDATE SET
                    total_pages = excluded.total_pages,
                    page_text = excluded.page_text,
                    char_count = excluded.char_count,
                    created_at = CURRENT_TIMESTAMP;
            """, (file_id, page_number, total_pages, page_text, char_count))
        conn.commit()
    finally:
        conn.close()

def get_page_extractions(file_id: str) -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        ph = "%s" if is_pg() else "?"
        cursor.execute(f"SELECT * FROM page_extractions WHERE file_id = {ph} ORDER BY page_number ASC", (file_id,))
        rows = cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

def get_extracted_text(file_id: str) -> Optional[str]:
    rec = get_file_by_id(file_id)
    if not rec:
        return None
    if rec.get("extracted_content"):
        return rec["extracted_content"]
    text_path_str = rec.get("text_path")
    if text_path_str:
        p = Path(text_path_str)
        if p.exists():
            return p.read_text(encoding="utf-8")
    return None

def reset_extracted_text(file_id: str) -> Optional[Dict[str, Any]]:
    """Clears extracted text, page progress, and resets file status for restarting extraction."""
    now = datetime.now().isoformat()
    conn = get_connection()
    try:
        cursor = conn.cursor()
        ph = "%s" if is_pg() else "?"
        time_exp = "NOW()" if is_pg() else "?"

        cursor.execute(f"DELETE FROM page_extractions WHERE file_id = {ph}", (file_id,))

        sql = f"""
            UPDATE files 
            SET text_extracted = 0,
                extracted_content = NULL,
                last_page = 1,
                last_scroll_pos = 0,
                fetched_pages_json = '[]',
                updated_at = {time_exp}
            WHERE file_id = {ph}
        """
        args = (file_id,) if is_pg() else (now, file_id)
        cursor.execute(sql, args)
        conn.commit()

        # Delete local text file if exists
        text_path = Path("extracted_text") / f"{file_id}_extracted.txt"
        if text_path.exists():
            try: text_path.unlink()
            except Exception: pass

    finally:
        conn.close()

    return get_file_by_id(file_id)

def update_file_progress(file_id: str, last_page: int = 1, last_scroll_pos: int = 0) -> Optional[Dict[str, Any]]:
    now = datetime.now().isoformat()
    conn = get_connection()
    try:
        cursor = conn.cursor()
        ph = "%s" if is_pg() else "?"
        time_exp = "NOW()" if is_pg() else "?"
        sql = f"UPDATE files SET last_page = {ph}, last_scroll_pos = {ph}, updated_at = {time_exp} WHERE file_id = {ph}"
        args = (last_page, last_scroll_pos, file_id) if is_pg() else (last_page, last_scroll_pos, now, file_id)
        cursor.execute(sql, args)
        conn.commit()
    finally:
        conn.close()
    return get_file_by_id(file_id)

def update_file_page_status(file_id: str, total_pages: int, fetched_pages: list) -> Optional[Dict[str, Any]]:
    now = datetime.now().isoformat()
    fetched_json = json.dumps(sorted(list(set(fetched_pages))))
    conn = get_connection()
    try:
        cursor = conn.cursor()
        ph = "%s" if is_pg() else "?"
        time_exp = "NOW()" if is_pg() else "?"
        sql = f"UPDATE files SET total_pages = {ph}, fetched_pages_json = {ph}, updated_at = {time_exp} WHERE file_id = {ph}"
        args = (total_pages, fetched_json, file_id) if is_pg() else (total_pages, fetched_json, now, file_id)
        cursor.execute(sql, args)
        conn.commit()
    finally:
        conn.close()
    return get_file_by_id(file_id)

def get_stats() -> Dict[str, Any]:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as total FROM files")
        total = cursor.fetchone()["total"]
        cursor.execute("SELECT COUNT(*) as completed FROM files WHERE downloaded = 1 OR text_extracted = 1 OR (local_path IS NOT NULL AND local_path != '') OR (text_path IS NOT NULL AND text_path != '')")
        completed = cursor.fetchone()["completed"]
        cursor.execute("SELECT COUNT(*) as in_progress FROM files WHERE (downloaded = 0 AND text_extracted = 0 AND (local_path IS NULL OR local_path = '') AND (text_path IS NULL OR text_path = '')) AND (last_page > 1 OR (fetched_pages_json IS NOT NULL AND fetched_pages_json != '[]'))")
        in_progress = cursor.fetchone()["in_progress"]
        cursor.execute("SELECT COUNT(*) as todo FROM files WHERE (downloaded = 0 AND text_extracted = 0 AND (local_path IS NULL OR local_path = '') AND (text_path IS NULL OR text_path = '')) AND (last_page <= 1 AND (fetched_pages_json IS NULL OR fetched_pages_json = '[]'))")
        todo = cursor.fetchone()["todo"]
        cursor.execute("SELECT COUNT(DISTINCT grade) as grades FROM files")
        grades = cursor.fetchone()["grades"]
        return {
            "total": total,
            "completed": completed,
            "in_progress": in_progress,
            "todo": todo,
            "downloaded": completed,
            "pending": todo,
            "grades_count": grades
        }
    finally:
        conn.close()
