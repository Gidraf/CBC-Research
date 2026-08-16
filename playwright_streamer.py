import asyncio
import io
import os
import sys
import logging
from pathlib import Path
from typing import Optional, Dict, Any
from fastapi import WebSocket
from playwright.async_api import async_playwright, Browser, BrowserContext, Page
import database

logger = logging.getLogger("playwright_streamer")
logger.setLevel(logging.INFO)

DOWNLOADS_DIR = Path("downloads")
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)

class PlaywrightStreamSession:
    def __init__(self, file_id: str, websocket: WebSocket):
        self.file_id = file_id
        self.websocket = websocket
        self.playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.js_enabled: bool = False  # Default to No-JS mode for GDrive bypass!
        self.current_url: str = f"https://drive.google.com/uc?export=download&id={file_id}"
        self.viewport_width = 1280
        self.viewport_height = 800
        self.is_running = False
        self.stream_task = None
        self.is_auto_scrolling = False
        self.auto_scroll_task = None
        self.screenshot_count = 0
        self.temp_dir = Path("temp_screenshots") / file_id

    async def start(self):
        self.is_running = True
        try:
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-accelerated-2d-canvas",
                    "--disable-gpu",
                ]
            )
            await self._create_context()
            self.stream_task = asyncio.create_task(self._frame_broadcaster())
            await self.navigate_to_file(self.file_id)

        except Exception as e:
            logger.error(f"Error starting Playwright session: {e}")
            await self.websocket.send_json({"type": "error", "message": str(e)})

    async def _create_context(self):
        if self.context:
            await self.context.close()

        self.context = await self.browser.new_context(
            java_script_enabled=self.js_enabled,
            accept_downloads=True,
            viewport={"width": self.viewport_width, "height": self.viewport_height},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        self.page = await self.context.new_page()
        self.page.on("download", self._handle_download)

    async def _handle_download(self, download):
        try:
            suggested_filename = download.suggested_filename or f"{self.file_id}.pdf"
            save_path = DOWNLOADS_DIR / f"{self.file_id}_{suggested_filename}"
            await download.save_as(save_path)
            file_size = save_path.stat().st_size if save_path.exists() else 0
            
            database.mark_as_downloaded(self.file_id, str(save_path), file_size)
            logger.info(f"File downloaded via Playwright -> {save_path}")

            await self.websocket.send_json({
                "type": "download_complete",
                "file_id": self.file_id,
                "local_path": str(save_path),
                "file_size": file_size
            })
        except Exception as e:
            logger.error(f"Failed saving download: {e}")

    async def start_auto_scroll(self):
        if self.is_auto_scrolling:
            return
        self.is_auto_scrolling = True
        self.auto_scroll_task = asyncio.create_task(self._auto_scroll_loop())
        await self.websocket.send_json({
            "type": "status",
            "message": "Auto-scrolling started (~350px / 1.5s). Screenshots recording...",
            "auto_scrolling": True
        })

    async def stop_auto_scroll(self):
        self.is_auto_scrolling = False
        if self.auto_scroll_task:
            self.auto_scroll_task.cancel()
            self.auto_scroll_task = None
        await self.websocket.send_json({
            "type": "status",
            "message": "Auto-scrolling paused.",
            "auto_scrolling": False
        })

    async def _auto_scroll_loop(self):
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        while self.is_auto_scrolling and self.page and self.is_running:
            try:
                # Capture frame for OCR before scroll step
                self.screenshot_count += 1
                img_path = self.temp_dir / f"step_{self.screenshot_count:04d}.png"
                try:
                    await self.page.screenshot(path=str(img_path))
                except Exception as shot_err:
                    logger.warning(f"Screenshot step error: {shot_err}")

                # 1. Dispatch PageDown keypress to advance Google Drive viewer pages
                try:
                    await self.page.keyboard.press("PageDown")
                except Exception as key_err:
                    pass

                # 2. Scroll outer window, inner body, and any iframe or GDrive PDF viewer container
                await self.page.evaluate("""() => {
                    window.scrollBy(0, 500);
                    if (document.body) document.body.scrollTop += 500;
                    if (document.documentElement) document.documentElement.scrollTop += 500;

                    // Target Google Drive Drive Viewer / Drive PDF scroll containers
                    let scrollables = document.querySelectorAll('div, iframe, body, [role="main"], [tabindex="0"], .ndfHFb-c4Qvld');
                    scrollables.forEach(el => {
                        try {
                            if (el.scrollHeight > el.clientHeight) {
                                el.scrollTop += 500;
                            }
                        } catch(e) {}
                    });

                    // Scroll inside inner iframes if accessible
                    let iframes = document.querySelectorAll('iframe');
                    iframes.forEach(iframe => {
                        try {
                            let iWin = iframe.contentWindow;
                            let iDoc = iframe.contentDocument || iWin.document;
                            if (iWin) iWin.scrollBy(0, 500);
                            if (iDoc) {
                                if (iDoc.body) iDoc.body.scrollTop += 500;
                                if (iDoc.documentElement) iDoc.documentElement.scrollTop += 500;
                                let iScrolls = iDoc.querySelectorAll('div, [role="main"]');
                                iScrolls.forEach(el => {
                                    if (el.scrollHeight > el.clientHeight) el.scrollTop += 500;
                                });
                            }
                        } catch(e) {}
                    });
                }""")

                # 3. Extract live DOM innerText and stream real-time over WebSocket
                try:
                    live_text = await self.page.evaluate("""() => {
                        let parts = [];
                        if (document.body && document.body.innerText) parts.push(document.body.innerText);

                        let iframes = document.querySelectorAll('iframe');
                        iframes.forEach(iframe => {
                            try {
                                let iDoc = iframe.contentDocument || iframe.contentWindow.document;
                                if (iDoc && iDoc.body && iDoc.body.innerText) {
                                    parts.push(iDoc.body.innerText);
                                }
                            } catch(e) {}
                        });
                        return parts.join('\\n\\n=== SECTION ===\\n\\n');
                    }""")

                    if live_text and len(live_text.strip()) > 10:
                        await self.websocket.send_json({
                            "type": "live_text",
                            "file_id": self.file_id,
                            "text": live_text,
                            "step": self.screenshot_count
                        })
                except Exception as text_err:
                    logger.warning(f"Live text extraction step warning: {text_err}")
                
                # Sleep 1.5s for human-readable scrolling
                await asyncio.sleep(1.5)


            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Auto-scroll loop error: {e}")

                await asyncio.sleep(1.0)


    async def finish_and_extract_text(self):
        await self.stop_auto_scroll()
        await self.websocket.send_json({
            "type": "status",
            "message": "Extracting document text from DOM & screenshots...",
            "extracting": True
        })

        dom_text = ""
        if self.page:
            try:
                # Extract DOM innerText from document.body and any inner iframe documents
                dom_text = await self.page.evaluate("""() => {
                    let text = document.body ? document.body.innerText : '';
                    let iframes = document.querySelectorAll('iframe');
                    iframes.forEach(iframe => {
                        try {
                            let iDoc = iframe.contentDocument || iframe.contentWindow.document;
                            if (iDoc && iDoc.body && iDoc.body.innerText) {
                                text += '\\n\\n' + iDoc.body.innerText;
                            }
                        } catch(e) {}
                    });
                    return text;
                }""")
            except Exception as dom_err:
                logger.warning(f"DOM text extraction warning: {dom_err}")

        # Process text extraction via Celery / background worker
        try:
            from celery_worker import perform_ocr_extraction
            text_file_path = await asyncio.to_thread(perform_ocr_extraction, self.file_id, dom_text)
            
            await self.websocket.send_json({
                "type": "extraction_complete",
                "file_id": self.file_id,
                "text_path": text_file_path,
                "downloaded": True
            })
        except Exception as e:
            logger.error(f"Text extraction failed: {e}")
            await self.websocket.send_json({
                "type": "error",
                "message": f"Text extraction error: {e}"
            })


    async def navigate_to_file(self, file_id: str, mode: str = "auto"):
        self.file_id = file_id
        file_rec = database.get_file_by_id(file_id)
        
        if self.js_enabled:
            self.current_url = f"https://drive.google.com/file/d/{file_id}/preview"
        else:
            self.current_url = f"https://drive.google.com/uc?export=download&id={file_id}"

        try:
            await self.websocket.send_json({
                "type": "status",
                "message": f"Navigating to {self.current_url} (JS: {'Enabled' if self.js_enabled else 'Disabled'})...",
                "js_enabled": self.js_enabled
            })

            await self.page.goto(self.current_url, wait_until="domcontentloaded", timeout=30000)

            if not self.js_enabled:
                if await self.page.locator("#uc-download-link").is_visible():
                    await self.page.locator("#uc-download-link").click()
                elif await self.page.locator("input[type='submit']").is_visible():
                    await self.page.locator("input[type='submit']").click()

        except Exception as e:
            logger.warning(f"Navigation warning for {self.current_url}: {e}")

    async def toggle_javascript(self, enable: Optional[bool] = None):
        if enable is not None:
            self.js_enabled = enable
        else:
            self.js_enabled = not self.js_enabled

        await self._create_context()
        await self.navigate_to_file(self.file_id)

    async def handle_action(self, action: Dict[str, Any]):
        act_type = action.get("action")
        
        if not self.page:
            return

        try:
            if act_type == "click":
                x = action.get("x", 0)
                y = action.get("y", 0)
                button = action.get("button", "left")
                await self.page.mouse.click(x, y, button=button)

            elif act_type == "right_click":
                x = action.get("x", 0)
                y = action.get("y", 0)
                await self.page.mouse.click(x, y, button="right")

            elif act_type == "mousemove":
                x = action.get("x", 0)
                y = action.get("y", 0)
                await self.page.mouse.move(x, y)

            elif act_type == "scroll":
                delta_x = action.get("delta_x", 0)
                delta_y = action.get("delta_y", 0)
                await self.page.mouse.wheel(delta_x, delta_y)

            elif act_type == "keypress":
                key = action.get("key")
                if key:
                    await self.page.keyboard.press(key)

            elif act_type == "type":
                text = action.get("text")
                if text:
                    await self.page.keyboard.type(text)

            elif act_type == "toggle_js":
                desired_js = action.get("js_enabled")
                await self.toggle_javascript(desired_js)

            elif act_type == "start_auto_scroll":
                await self.start_auto_scroll()

            elif act_type == "stop_auto_scroll":
                await self.stop_auto_scroll()

            elif act_type == "finish_and_extract":
                await self.finish_and_extract_text()

            elif act_type == "navigate":
                url = action.get("url")
                if url:
                    self.current_url = url
                    await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)

            elif act_type == "mark_done":
                rec = database.mark_as_downloaded(self.file_id)
                await self.websocket.send_json({
                    "type": "status",
                    "message": f"File {self.file_id} marked as Downloaded/Done!",
                    "downloaded": True
                })

            elif act_type == "reload":
                await self.page.reload()

        except Exception as e:
            logger.error(f"Error handling action {act_type}: {e}")

    async def _frame_broadcaster(self):
        """Captures screenshots continuously and sends binary/JPEG frames over WebSocket."""
        while self.is_running and self.page:
            try:
                screenshot_bytes = await self.page.screenshot(type="jpeg", quality=60)
                page_title = await self.page.title()
                page_url = self.page.url

                import base64
                b64_frame = base64.b64encode(screenshot_bytes).decode("utf-8")

                await self.websocket.send_json({
                    "type": "frame",
                    "frame": f"data:image/jpeg;base64,{b64_frame}",
                    "url": page_url,
                    "title": page_title,
                    "js_enabled": self.js_enabled,
                    "auto_scrolling": self.is_auto_scrolling,
                    "file_id": self.file_id
                })

                await asyncio.sleep(0.1)
            except asyncio.CancelledError:
                break
            except Exception as e:
                await asyncio.sleep(0.5)

    async def stop(self):
        await self.stop_auto_scroll()
        self.is_running = False
        if self.stream_task:
            self.stream_task.cancel()
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()

