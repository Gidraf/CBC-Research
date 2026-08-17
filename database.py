import sqlite3
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional

DB_PATH = Path("gdrive_files.db")

def get_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
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
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        # Safe migration for existing DB
        try:
            cursor.execute("ALTER TABLE files ADD COLUMN text_path TEXT;")
        except Exception:
            pass
        try:
            cursor.execute("ALTER TABLE files ADD COLUMN text_extracted INTEGER DEFAULT 0;")
        except Exception:
            pass
        try:
            cursor.execute("ALTER TABLE files ADD COLUMN last_scroll_pos INTEGER DEFAULT 0;")
        except Exception:
            pass
        try:
            cursor.execute("ALTER TABLE files ADD COLUMN last_page INTEGER DEFAULT 1;")
        except Exception:
            pass
        try:
            cursor.execute("ALTER TABLE files ADD COLUMN total_pages INTEGER DEFAULT 67;")
        except Exception:
            pass
        try:
            cursor.execute("ALTER TABLE files ADD COLUMN fetched_pages_json TEXT DEFAULT '[]';")
        except Exception:
            pass
        try:
            cursor.execute("ALTER TABLE files ADD COLUMN extracted_content TEXT;")
        except Exception:
            pass


        cursor.execute("CREATE INDEX IF NOT EXISTS idx_file_id ON files(file_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_grade ON files(grade);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_downloaded ON files(downloaded);")
        conn.commit()
    sync_disk_files_status()





def upsert_file(grade: str, subject: str, file_id: str, google_drive_url: str, download_url: str, local_path: Optional[str] = None, downloaded: bool = False) -> Dict[str, Any]:
    now = datetime.now().isoformat()
    with get_connection() as conn:
        cursor = conn.cursor()
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
    return get_file_by_id(file_id)

def sync_disk_files_status():
    """Scans disk directories and updates SQLite status for backward compatibility."""
    downloads_dir = Path("downloads")
    extracted_dir = Path("extracted_text")
    
    with get_connection() as conn:
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
                    for r in cursor.execute("SELECT file_id FROM files").fetchall():
                        fid = r["file_id"]
                        if fid in pdf_file.name:
                            cursor.execute("UPDATE files SET downloaded = 1, local_path = ? WHERE file_id = ?", (str(pdf_file), fid))

        conn.commit()

def get_all_files(grade_filter: Optional[str] = None, status_filter: Optional[str] = None, search: Optional[str] = None) -> List[Dict[str, Any]]:
    query = "SELECT * FROM files WHERE 1=1"
    params = []
    
    if grade_filter:
        query += " AND grade = ?"
        params.append(grade_filter)
        
    if status_filter in ["completed", "downloaded"]:
        query += " AND (downloaded = 1 OR text_extracted = 1 OR (local_path IS NOT NULL AND local_path != '') OR (text_path IS NOT NULL AND text_path != ''))"
    elif status_filter == "in_progress":
        query += " AND (downloaded = 0 AND text_extracted = 0 AND (local_path IS NULL OR local_path = '') AND (text_path IS NULL OR text_path = '')) AND (last_page > 1 OR (fetched_pages_json IS NOT NULL AND fetched_pages_json != '[]'))"
    elif status_filter in ["todo", "pending"]:
        query += " AND (downloaded = 0 AND text_extracted = 0 AND (local_path IS NULL OR local_path = '') AND (text_path IS NULL OR text_path = '')) AND (last_page <= 1 AND (fetched_pages_json IS NULL OR fetched_pages_json = '[]'))"
    # If status_filter is "all" or None or "", no status WHERE clause is added -> returns all files!
        
    if search:
        query += " AND (subject LIKE ? OR grade LIKE ? OR file_id LIKE ?)"
        pattern = f"%{search}%"
        params.extend([pattern, pattern, pattern])
        
    query += " ORDER BY grade ASC, subject ASC"


    
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()
        return [dict(r) for r in rows]

def get_file_by_id(file_id: str) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM files WHERE file_id = ?", (file_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

def get_file_by_db_id(db_id: int) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM files WHERE id = ?", (db_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

def mark_as_downloaded(file_id: str, local_path: Optional[str] = None, file_size: int = 0) -> Optional[Dict[str, Any]]:
    now = datetime.now().isoformat()
    with get_connection() as conn:
        cursor = conn.cursor()
        if local_path:
            cursor.execute("""
                UPDATE files 
                SET downloaded = 1, local_path = ?, file_size = ?, updated_at = ? 
                WHERE file_id = ?
            """, (local_path, file_size, now, file_id))
        else:
            cursor.execute("""
                UPDATE files 
                SET downloaded = 1, updated_at = ? 
                WHERE file_id = ?
            """, (now, file_id))
        conn.commit()
    return get_file_by_id(file_id)

def toggle_downloaded(file_id: str) -> Optional[Dict[str, Any]]:
    file_record = get_file_by_id(file_id)
    if not file_record:
        return None
    new_status = 0 if file_record["downloaded"] == 1 else 1
    now = datetime.now().isoformat()
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE files SET downloaded = ?, updated_at = ? WHERE file_id = ?", (new_status, now, file_id))
        conn.commit()
    return get_file_by_id(file_id)

def update_extracted_text(file_id: str, text_path: str, content: Optional[str] = None) -> Optional[Dict[str, Any]]:
    now = datetime.now().isoformat()
    with get_connection() as conn:
        cursor = conn.cursor()
        if content is not None:
            cursor.execute("""
                UPDATE files 
                SET downloaded = 1, text_extracted = 1, text_path = ?, extracted_content = ?, updated_at = ? 
                WHERE file_id = ?
            """, (text_path, content, now, file_id))
        else:
            cursor.execute("""
                UPDATE files 
                SET downloaded = 1, text_extracted = 1, text_path = ?, updated_at = ? 
                WHERE file_id = ?
            """, (text_path, now, file_id))
        conn.commit()
    return get_file_by_id(file_id)

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
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE files 
            SET text_extracted = 0,
                extracted_content = NULL,
                last_page = 1,
                last_scroll_pos = 0,
                fetched_pages_json = '[]',
                updated_at = ?
            WHERE file_id = ?
        """, (now, file_id))
        conn.commit()

    # Delete local text file if exists
    text_path = Path("extracted_text") / f"{file_id}_extracted.txt"
    if text_path.exists():
        try:
            text_path.unlink()
        except Exception:
            pass

    return get_file_by_id(file_id)

def update_file_progress(file_id: str, last_page: int = 1, last_scroll_pos: int = 0) -> Optional[Dict[str, Any]]:

    now = datetime.now().isoformat()
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE files 
            SET last_page = ?, last_scroll_pos = ?, updated_at = ? 
            WHERE file_id = ?
        """, (last_page, last_scroll_pos, now, file_id))
        conn.commit()
    return get_file_by_id(file_id)

def update_file_page_status(file_id: str, total_pages: int, fetched_pages: list) -> Optional[Dict[str, Any]]:
    import json
    now = datetime.now().isoformat()
    fetched_json = json.dumps(sorted(list(set(fetched_pages))))
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE files 
            SET total_pages = ?, fetched_pages_json = ?, updated_at = ? 
            WHERE file_id = ?
        """, (total_pages, fetched_json, now, file_id))
        conn.commit()
    return get_file_by_id(file_id)



def get_stats() -> Dict[str, Any]:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as total FROM files")
        total = cursor.fetchone()["total"]
        cursor.execute("SELECT COUNT(*) as completed FROM files WHERE downloaded = 1 OR text_extracted = 1")
        completed = cursor.fetchone()["completed"]
        cursor.execute("SELECT COUNT(*) as in_progress FROM files WHERE (downloaded = 0 AND text_extracted = 0) AND (last_page > 1 OR (fetched_pages_json IS NOT NULL AND fetched_pages_json != '[]'))")
        in_progress = cursor.fetchone()["in_progress"]
        cursor.execute("SELECT COUNT(*) as todo FROM files WHERE (downloaded = 0 AND text_extracted = 0) AND (last_page <= 1 AND (fetched_pages_json IS NULL OR fetched_pages_json = '[]'))")
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


