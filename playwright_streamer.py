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
        self.accumulated_text_blocks = []
        self.seen_text_hashes = set()


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
        
        await self.websocket.send_json({
            "type": "status",
            "message": "Warming up: Pre-scrolling to bottom (3 passes) to force Google Drive to lazy-load all pages...",
            "auto_scrolling": True
        })

        # 3-Pass Pre-Scroll Warmup to trigger lazy-loading of all PDF pages in Google Drive
        if self.page:
            try:
                for pass_num in range(1, 4):
                    await self.page.evaluate("""() => {
                        window.scrollTo(0, document.body ? document.body.scrollHeight : 100000);
                        let scrollables = document.querySelectorAll('div, iframe, body, [role="main"], [tabindex="0"], .ndfHFb-c4Qvld');
                        scrollables.forEach(el => {
                            try { el.scrollTop = el.scrollHeight; } catch(e) {}
                        });
                        let iframes = document.querySelectorAll('iframe');
                        iframes.forEach(iframe => {
                            try {
                                let iWin = iframe.contentWindow;
                                let iDoc = iframe.contentDocument || iWin.document;
                                if (iWin) iWin.scrollTo(0, 100000);
                                if (iDoc) {
                                    if (iDoc.body) iDoc.body.scrollTop = iDoc.body.scrollHeight;
                                    let iScrolls = iDoc.querySelectorAll('div, [role="main"]');
                                    iScrolls.forEach(el => el.scrollTop = el.scrollHeight);
                                }
                            } catch(e) {}
                        });
                    }""")
                    await asyncio.sleep(0.4)
            except Exception as warmup_err:
                logger.warning(f"Warmup scroll warning: {warmup_err}")

        self.auto_scroll_task = asyncio.create_task(self._auto_scroll_loop())

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
        logger.info(f"Starting page-targeted verified scroll loop for {self.file_id}")
        
        page_map: Dict[int, List[str]] = {}
        total_pages = 67  # Default, updated dynamically from DOM

        # 1. Detect total pages from document
        if self.page:
            try:
                detected_total = await self.page.evaluate("""() => {
                    let match = document.body ? document.body.innerText.match(/Page\\s+\\d+\\s+of\\s+(\\d+)/i) : null;
                    if (match) return parseInt(match[1]);
                    let iframes = document.querySelectorAll('iframe');
                    for (let iframe of iframes) {
                        try {
                            let iDoc = iframe.contentDocument || iframe.contentWindow.document;
                            if (iDoc && iDoc.body) {
                                let iMatch = iDoc.body.innerText.match(/Page\\s+\\d+\\s+of\\s+(\\d+)/i);
                                if (iMatch) return parseInt(iMatch[1]);
                            }
                        } catch(e) {}
                    }
                    return 67;
                }""")

                if detected_total and detected_total > 0:
                    total_pages = detected_total
                    logger.info(f"Detected document total page count: {total_pages}")
            except Exception as det_err:
                logger.warning(f"Page count detection warning: {det_err}")

        step_delta = 750
        max_steps = max(total_pages * 2, 80)

        for step in range(1, max_steps + 1):
            if not self.is_auto_scrolling or not self.is_running:
                break

            self.screenshot_count += 1
            current_target_page = min(total_pages, (step // 2) + 1)
            scroll_amount = step_delta

            self.last_scroll_pos += scroll_amount
            self.last_page = current_target_page

            # Scroll step
            try:
                await self.page.evaluate("""(delta) => {
                    window.scrollBy(0, delta);
                    if (document.body) document.body.scrollTop += delta;
                    if (document.documentElement) document.documentElement.scrollTop += delta;

                    let scrollables = document.querySelectorAll('div, iframe, body, [role="main"], [tabindex="0"], .ndfHFb-c4Qvld');
                    scrollables.forEach(el => {
                        try {
                            if (el.scrollHeight > el.clientHeight) el.scrollTop += delta;
                        } catch(e) {}
                    });

                    let iframes = document.querySelectorAll('iframe');
                    iframes.forEach(iframe => {
                        try {
                            let iWin = iframe.contentWindow;
                            let iDoc = iframe.contentDocument || iWin.document;
                            if (iWin) iWin.scrollBy(0, delta);
                            if (iDoc) {
                                if (iDoc.body) iDoc.body.scrollTop += delta;
                                if (iDoc.documentElement) iDoc.documentElement.scrollTop += delta;
                                let iScrolls = iDoc.querySelectorAll('div, [role="main"]');
                                iScrolls.forEach(el => {
                                    if (el.scrollHeight > el.clientHeight) el.scrollTop += delta;
                                });
                            }
                        } catch(e) {}
                    });
                }""", scroll_amount)
            except Exception as scroll_err:
                logger.warning(f"Scroll step warning: {scroll_err}")

            # Extract raw DOM text
            try:
                step_text = await self.page.evaluate("""() => {
                    let parts = [];
                    if (document.body && document.body.innerText) parts.push(document.body.innerText);
                    let iframes = document.querySelectorAll('iframe');
                    iframes.forEach((iframe) => {
                        try {
                            let iDoc = iframe.contentDocument || iframe.contentWindow.document;
                            if (iDoc && iDoc.body && iDoc.body.innerText) parts.push(iDoc.body.innerText);
                        } catch(e) {}
                    });
                    return parts.join('\\n\\n');
                }""")

                if step_text and len(step_text.strip()) > 10:
                    import re
                    # Parse page headers from text (e.g. Page X of 67)
                    raw_lines = [l.strip() for l in step_text.split('\n') if l.strip()]
                    current_p = current_target_page

                    for line in raw_lines:
                        p_match = re.search(r'Page\s+(\d+)\s+of\s+(\d+)', line, re.IGNORECASE)
                        if p_match:
                            current_p = int(p_match.group(1))

                        if current_p not in page_map:
                            page_map[current_p] = []

                        # Avoid exact line duplicates on the same page
                        if line not in page_map[current_p] and not line.startswith("==="):
                            page_map[current_p].append(line)

                    # Build clean, structured multi-page document text
                    formatted_pages = []
                    sorted_pages = sorted(page_map.keys())
                    for p in sorted_pages:
                        p_content = "\n".join(page_map[p])
                        if p_content.strip():
                            formatted_pages.append(f"================================================================================\n📄 PAGE {p} OF {total_pages}\n================================================================================\n\n{p_content}")

                    if formatted_pages:
                        accumulated_full_text = "\n\n".join(formatted_pages)
                        text_file_path = Path("extracted_text") / f"{self.file_id}_extracted.txt"
                        text_file_path.write_text(accumulated_full_text, encoding="utf-8")
                        database.update_extracted_text(self.file_id, str(text_file_path))
                        database.update_file_progress(self.file_id, self.last_page, self.last_scroll_pos)

                        # Notify WebSocket client safely
                        try:
                            if self.websocket:
                                await self.websocket.send_json({
                                    "type": "live_text",
                                    "file_id": self.file_id,
                                    "text": accumulated_full_text,
                                    "step": self.screenshot_count,
                                    "pages_captured": len(page_map),
                                    "total_pages": total_pages,
                                    "last_page": self.last_page,
                                    "last_scroll_pos": self.last_scroll_pos
                                })
                        except Exception:
                            pass

            except Exception as text_err:
                logger.warning(f"Live text extraction step warning: {text_err}")

            # Paced lapse: 1.0 second per step for clean DOM rendering
            await asyncio.sleep(1.0)


    async def finish_and_extract_text(self):
        await self.stop_auto_scroll()
        await self.websocket.send_json({
            "type": "status",
            "message": "Finalizing clean multi-page document structure...",
            "extracting": True
        })

        # Load existing clean text file or fallback to DOM
        text_file_path = Path("extracted_text") / f"{self.file_id}_extracted.txt"
        dom_text = text_file_path.read_text(encoding="utf-8") if text_file_path.exists() else ""

        if not dom_text and self.page:
            try:
                dom_text = await self.page.evaluate("""() => document.body ? document.body.innerText : ''""")
            except Exception:
                pass


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
        if file_rec:
            self.last_scroll_pos = file_rec.get("last_scroll_pos", 0) or 0
            self.last_page = file_rec.get("last_page", 1) or 1

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

            # Restore previous scroll position if continuing session
            if self.last_scroll_pos > 0:
                await asyncio.sleep(1.0)
                await self.page.evaluate(f"""() => {{
                    window.scrollTo(0, {self.last_scroll_pos});
                    let scrollables = document.querySelectorAll('div, iframe, body, [role="main"], .ndfHFb-c4Qvld');
                    scrollables.forEach(el => {{
                        try {{
                            if (el.scrollHeight > el.clientHeight) el.scrollTop = {self.last_scroll_pos};
                        }} catch(e) {{}}
                    }});
                }}""")
                await self.websocket.send_json({
                    "type": "status",
                    "message": f"Resumed document at last saved position (Page ~{self.last_page}, {self.last_scroll_pos}px)",
                    "last_page": self.last_page,
                    "last_scroll_pos": self.last_scroll_pos
                })

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

