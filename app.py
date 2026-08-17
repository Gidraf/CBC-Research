import asyncio
import json
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, Response

from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

import database
import gdrive_extractor
import gdrive_nojs_downloader
from playwright_streamer import PlaywrightStreamSession

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("fastapi_app")

app = FastAPI(title="Google Drive Playwright Extractor & Viewer", version="1.0.0")

# Setup Static Files & Templates
BASE_DIR = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

def render_template(request: Request, name: str, context: Optional[dict] = None):
    ctx = context or {}
    ctx["request"] = request
    try:
        # Starlette >= 0.28.0 (request is required first or keyword)
        return templates.TemplateResponse(request=request, name=name, context=ctx)
    except (TypeError, ValueError):
        try:
            # Fallback for Starlette < 0.28.0 (name, context)
            return templates.TemplateResponse(name, ctx)
        except Exception:
            # Fallback positional for Starlette >= 0.28.0 (request, name, context)
            return templates.TemplateResponse(request, name, ctx)

# Startup Event: Initialize SQLite Database
@app.on_event("startup")
def startup_event():
    database.init_db()
    logger.info("Database initialized successfully.")
    # Seed initial records if empty
    stats = database.get_stats()
    if stats["total"] == 0:
        logger.info("Database empty, populating initial records...")
        gdrive_extractor.extract_and_store_all_kicd_files()

# Page Route
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return render_template(request, "index.html")

@app.get("/favicon.ico")
async def favicon():
    return HTMLResponse(status_code=204)

# REST APIs
@app.get("/api/files")
def list_files(grade: Optional[str] = None, status: Optional[str] = None, search: Optional[str] = None):
    files = database.get_all_files(grade_filter=grade, status_filter=status, search=search)
    return files

@app.get("/api/files/{file_id}")
def get_file(file_id: str):
    file_rec = database.get_file_by_id(file_id)
    if not file_rec:
        raise HTTPException(status_code=404, detail="File not found")
    return file_rec

@app.post("/api/files/{file_id}/toggle-downloaded")
def toggle_file_status(file_id: str):
    res = database.toggle_downloaded(file_id)
    if not res:
        raise HTTPException(status_code=404, detail="File not found")
    return res

@app.post("/api/files/{file_id}/mark-done")
def mark_file_done(file_id: str):
    res = database.mark_as_downloaded(file_id)
    if not res:
        raise HTTPException(status_code=404, detail="File not found")
    return res

class CustomUrlRequest(BaseModel):
    url_or_id: str
    grade: Optional[str] = "Custom Grade"
    subject: Optional[str] = "Custom Subject"

@app.post("/api/files/add-custom")
def add_custom_file(req: CustomUrlRequest):
    res = gdrive_extractor.add_custom_gdrive_url(req.url_or_id, req.grade, req.subject)
    if not res:
        raise HTTPException(status_code=400, detail="Invalid Google Drive URL or File ID")
    return res

@app.post("/api/extract")
def trigger_extraction():
    count = gdrive_extractor.extract_and_store_all_kicd_files()
    return {"status": "success", "count": count}

@app.post("/api/files/{file_id}/download-nojs")
def download_nojs_direct(file_id: str):
    success = gdrive_nojs_downloader.download_gdrive_no_js_python(file_id)
    if not success:
        raise HTTPException(status_code=500, detail="No-JS direct download failed")
    return database.get_file_by_id(file_id)

@app.post("/api/files/{file_id}/finish-extract")
def finish_and_extract_file(file_id: str):
    from celery_worker import perform_ocr_extraction
    text_path = perform_ocr_extraction(file_id)
    return {"status": "success", "file_id": file_id, "text_path": text_path}

@app.get("/api/files/{file_id}/text")
def get_extracted_text(file_id: str):
    file_rec = database.get_file_by_id(file_id)
    text_file = Path(f"extracted_text/{file_id}_extracted.txt")
    if file_rec and file_rec.get("text_path") and Path(file_rec["text_path"]).exists():
        text_file = Path(file_rec["text_path"])
    
    if not text_file.exists():
        raise HTTPException(status_code=404, detail="Extracted text file does not exist.")
    return {"file_id": file_id, "content": text_file.read_text(encoding="utf-8")}

class SaveTextRequest(BaseModel):
    content: str

@app.post("/api/files/{file_id}/save-text")
def save_extracted_text(file_id: str, req: SaveTextRequest):
    out_dir = Path("extracted_text")
    out_dir.mkdir(parents=True, exist_ok=True)
    text_file = out_dir / f"{file_id}_extracted.txt"
    text_file.write_text(req.content, encoding="utf-8")
    updated = database.update_extracted_text(file_id, str(text_file))
    return {"status": "success", "file_id": file_id, "text_path": str(text_file), "record": updated}

@app.get("/api/files/{file_id}/download-text")
def download_extracted_text_file(file_id: str):
    from fastapi.responses import FileResponse
    text_file = Path(f"extracted_text/{file_id}_extracted.txt")
    file_rec = database.get_file_by_id(file_id)
    if file_rec and file_rec.get("text_path") and Path(file_rec["text_path"]).exists():
        text_file = Path(file_rec["text_path"])
    
    if not text_file.exists():
        raise HTTPException(status_code=404, detail="Text file not found.")
    
    filename = f"{file_rec['grade']}_{file_rec['subject']}_extracted.txt".replace(" ", "_") if file_rec else f"{file_id}_extracted.txt"
    return FileResponse(path=str(text_file), filename=filename, media_type="text/plain")

@app.get("/api/files/{file_id}/page-status")
def get_file_page_status(file_id: str):
    file_rec = database.get_file_by_id(file_id)
    if not file_rec:
        raise HTTPException(status_code=404, detail="File not found")

    total_pages = file_rec.get("total_pages") or 0
    fetched_json = file_rec.get("fetched_pages_json") or "[]"
    try:
        fetched_pages = json.loads(fetched_json)
    except Exception:
        fetched_pages = []

    # Check if extracted text file exists and parse all page markers from it
    text_file_path = Path("extracted_text") / f"{file_id}_extracted.txt"
    if text_file_path.exists():
        try:
            import re
            content = text_file_path.read_text(encoding="utf-8")
            def has_real_body_content(block_str: str) -> bool:
                real_len = 0
                for line in block_str.split('\n'):
                    l = line.strip()
                    if not l or l.startswith("==="):
                        continue
                    if l in ["Page", "/", "\\"] or re.match(r'^Page\s+\d+\s*(?:of|\/)\s*\d+$', l, re.IGNORECASE) or re.match(r'^\d+$', l):
                        continue
                    real_len += len(l)
                return real_len >= 15

            found_pages = set()
            blocks = re.split(r'================================================================================\n📄 PAGE (\d+) OF (\d+)\n================================================================================', content)
            
            # Pattern matched blocks: [preamble, page_1, total_1, body_1, page_2, total_2, body_2, ...]
            if len(blocks) > 1:
                i = 1
                while i < len(blocks) - 2:
                    p_num = int(blocks[i])
                    t_num = int(blocks[i+1])
                    body_text = blocks[i+2]
                    if 0 < p_num <= 2000 and 0 < t_num <= 2000:
                        if t_num > total_pages:
                            total_pages = t_num
                        if has_real_body_content(body_text):
                            found_pages.add(p_num)
                    i += 3
            else:
                # Fallback scan for other text formats
                for m in re.finditer(r'(?:📄\s*PAGE|Page)\s+(\d+)\s*(?:of|\/)\s*(\d+)', content, re.IGNORECASE):
                    p_num = int(m.group(1))
                    t_num = int(m.group(2))
                    if 0 < p_num <= 2000 and 0 < t_num <= 2000:
                        if t_num > total_pages:
                            total_pages = t_num
                        # Find surrounding snippet
                        start_idx = max(0, m.start() - 50)
                        end_idx = min(len(content), m.end() + 300)
                        snippet = content[start_idx:end_idx]
                        if has_real_body_content(snippet):
                            found_pages.add(p_num)

            if found_pages and total_pages <= 0:
                total_pages = max(found_pages)

            fetched_pages = sorted(list(found_pages))
            if total_pages > 0:
                database.update_file_page_status(file_id, total_pages, fetched_pages)
        except Exception as scan_err:
            logger.warning(f"Error parsing page status from text file: {scan_err}")


    if total_pages <= 0:
        total_pages = 67  # Fallback default for display if undiscovered

    missing_pages = sorted(list(set(range(1, total_pages + 1)) - set(fetched_pages)))


    return {
        "file_id": file_id,
        "total_pages": total_pages,
        "fetched_pages": fetched_pages,
        "missing_pages": missing_pages,
        "fetched_count": len(fetched_pages),
        "missing_count": len(missing_pages),
        "is_complete": len(missing_pages) == 0
    }

class FetchPagesRequest(BaseModel):
    pages: Optional[List[int]] = None

@app.post("/api/files/{file_id}/fetch-pages")
async def trigger_fetch_targeted_pages(file_id: str, req: FetchPagesRequest):
    file_rec = database.get_file_by_id(file_id)
    if not file_rec:
        raise HTTPException(status_code=404, detail="File not found")

    # Save target pages request for background runner
    return {
        "status": "success",
        "file_id": file_id,
        "target_pages": req.pages,
        "message": f"Targeted fetch queued for pages: {req.pages or 'unfetched missing pages'}"
    }

@app.get("/api/stats")
def get_system_stats():
    return database.get_stats()

class LangfuseSyncRequest(BaseModel):
    public_key: str
    secret_key: str
    host_url: Optional[str] = "https://cloud.langfuse.com"

@app.get("/api/files/{file_id}/extracted-text")
def get_extracted_text_api(file_id: str):
    file_rec = database.get_file_by_id(file_id)
    if not file_rec:
        raise HTTPException(status_code=404, detail="File not found")
    
    text_content = database.get_extracted_text(file_id) or ""
    status_info = get_file_page_status(file_id)
    
    return {
        "success": True,
        "file_id": file_id,
        "grade": file_rec.get("grade"),
        "subject": file_rec.get("subject"),
        "text_content": text_content,
        "total_pages": status_info.get("total_pages", 67),
        "fetched_pages": status_info.get("fetched_pages", []),
        "char_count": len(text_content)
    }

@app.get("/api/files/{file_id}/download-text")
def download_extracted_text_api(file_id: str):
    file_rec = database.get_file_by_id(file_id)
    if not file_rec:
        raise HTTPException(status_code=404, detail="File not found")
    
    text_content = database.get_extracted_text(file_id)
    if not text_content:
        raise HTTPException(status_code=404, detail="No extracted text available for this file yet")
    
    grade_clean = (file_rec.get("grade") or "file").replace(" ", "_")
    subject_clean = (file_rec.get("subject") or file_id).replace(" ", "_")
    filename = f"{grade_clean}_{subject_clean}_{file_id}_extracted.txt"
    
    return Response(
        content=text_content,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )

@app.post("/api/files/{file_id}/sync-langfuse")
async def sync_to_langfuse_api(file_id: str, req: LangfuseSyncRequest):
    file_rec = database.get_file_by_id(file_id)
    if not file_rec:
        raise HTTPException(status_code=404, detail="File not found")
    
    text_content = database.get_extracted_text(file_id)
    if not text_content:
        raise HTTPException(status_code=400, detail="No extracted text available to sync. Please capture or scroll pages first.")
    
    if not req.public_key or not req.secret_key:
        raise HTTPException(status_code=400, detail="Langfuse Public Key and Secret Key are required.")
    
    host_url = (req.host_url or "https://cloud.langfuse.com").rstrip("/")
    status_info = get_file_page_status(file_id)
    
    import urllib.request
    import urllib.error
    import base64
    import json
    
    endpoint = f"{host_url}/api/public/dataset-items"
    
    payload = {
        "datasetName": "CBC_Research_Curriculum_Designs",
        "input": {
            "file_id": file_id,
            "grade": file_rec.get("grade"),
            "subject": file_rec.get("subject"),
            "google_drive_url": file_rec.get("google_drive_url")
        },
        "expectedOutput": text_content,
        "metadata": {
            "total_pages": status_info.get("total_pages", 67),
            "fetched_pages": status_info.get("fetched_pages", []),
            "char_count": len(text_content),
            "source": "CBC_Research_Playwright_Scraper"
        }
    }
    
    auth_str = f"{req.public_key}:{req.secret_key}"
    b64_auth = base64.b64encode(auth_str.encode("utf-8")).decode("utf-8")
    
    req_obj = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Basic {b64_auth}"
        },
        method="POST"
    )
    
    try:
        with urllib.request.urlopen(req_obj, timeout=15) as resp:
            resp_body = resp.read().decode("utf-8")
            return {
                "success": True,
                "file_id": file_id,
                "message": "Successfully synced extracted text into Langfuse dataset 'CBC_Research_Curriculum_Designs'!",
                "langfuse_response": json.loads(resp_body) if resp_body else {}
            }
    except urllib.error.HTTPError as http_err:
        err_msg = http_err.read().decode("utf-8") if http_err.fp else str(http_err)
        raise HTTPException(status_code=http_err.code, detail=f"Langfuse API Error: {err_msg}")
    except Exception as err:
        raise HTTPException(status_code=500, detail=f"Failed connecting to Langfuse host {host_url}: {err}")



# WebSocket Stream Route
@app.websocket("/ws/stream/{file_id}")
async def stream_browser(websocket: WebSocket, file_id: str):
    await websocket.accept()
    session = PlaywrightStreamSession(file_id, websocket)

    try:
        await session.start()
        
        while True:
            data_text = await websocket.receive_text()
            try:
                action_data = json.loads(data_text)
                await session.handle_action(action_data)
            except Exception as e:
                logger.error(f"Error parsing client action: {e}")

    except WebSocketDisconnect:
        logger.info(f"WebSocket client disconnected for file {file_id}")
    except Exception as e:
        logger.error(f"WebSocket session error: {e}")
    finally:
        try:
            await session.stop()
        except Exception:
            pass


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=5550, reload=True)
