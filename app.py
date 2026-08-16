import asyncio
import json
import logging
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
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
    if not file_rec or not file_rec.get("text_path"):
        raise HTTPException(status_code=404, detail="Extracted text not found for this file.")
    text_file = Path(file_rec["text_path"])
    if not text_file.exists():
        raise HTTPException(status_code=404, detail="Extracted text file does not exist.")
    return {"file_id": file_id, "content": text_file.read_text(encoding="utf-8")}


@app.get("/api/stats")
def get_system_stats():
    return database.get_stats()

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
        await session.stop()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=5550, reload=True)
