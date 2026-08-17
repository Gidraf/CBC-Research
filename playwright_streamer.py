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
        self.captured_pages_set: set = set()

        text_path = Path("extracted_text") / f"{file_id}_extracted.txt"
        if text_path.exists():
            try:
                import re
                txt = text_path.read_text(encoding="utf-8")
                for pm in re.finditer(r'📄 PAGE (\d+) OF', txt):
                    self.captured_pages_set.add(int(pm.group(1)))
            except Exception:
                pass


    async def _send_ws_json(self, data: dict):
        if self.websocket:
            try:
                await self.websocket.send_json(data)
            except Exception:
                pass

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
            await self._send_ws_json({"type": "error", "message": str(e)})

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

            await self._send_ws_json({
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
        
        await self._send_ws_json({
            "type": "status",
            "message": "Warming up: Pre-scrolling to bottom to force Google Drive to lazy-load pages...",
            "auto_scrolling": True
        })

        # Dynamic pre-flight sweep
        if self.page:
            try:
                await self.page.evaluate("""() => window.scrollTo(0, document.body ? document.body.scrollHeight : 100000);""")
                await asyncio.sleep(0.4)
            except Exception as warmup_err:
                logger.warning(f"Warmup scroll warning: {warmup_err}")

        self.auto_scroll_task = asyncio.create_task(self._auto_scroll_loop())

    async def stop_auto_scroll(self):
        self.is_auto_scrolling = False
        if self.auto_scroll_task:
            self.auto_scroll_task.cancel()
            self.auto_scroll_task = None
        await self._send_ws_json({
            "type": "status",
            "message": "Auto-scrolling paused.",
            "auto_scrolling": False
        })


    async def _auto_scroll_loop(self):
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Starting explicit Page-Checklist State Machine for {self.file_id}")
        
        page_map: Dict[int, List[str]] = {}
        total_pages = 0

        # -------------------------------------------------------------------------
        # STEP 1: Pre-flight Sweep to Bottom & Dynamic Page Count Discovery
        # -------------------------------------------------------------------------
        if self.page:
            try:
                logger.info("Executing pre-flight sweep to bottom to read dynamic total page count...")
                await self.page.evaluate("""() => {
                    window.scrollTo(0, document.body ? document.body.scrollHeight : 100000);
                    let scrollables = document.querySelectorAll('div, iframe, body, [role="main"], [tabindex="0"], .ndfHFb-c4Qvld');
                    scrollables.forEach(el => { try { el.scrollTop = el.scrollHeight; } catch(e) {} });
                }""")
                await asyncio.sleep(1.0)

                detected_total = await self.page.evaluate("""() => {
                    function extractMaxPage(str) {
                        if (!str) return 0;
                        let matches = str.matchAll(/(?:Page\\s+\\d+|\\b\\d+)\\s*(?:of|\\/)\\s*(\\d+)/gi);
                        let maxP = 0;
                        for (let m of matches) {
                            let val = parseInt(m[1]);
                            if (val > maxP && val < 2000) maxP = val;
                        }
                        return maxP;
                    }

                    let p = extractMaxPage(document.body ? document.body.innerText : '');
                    let iframes = document.querySelectorAll('iframe');
                    for (let iframe of iframes) {
                        try {
                            let iDoc = iframe.contentDocument || iframe.contentWindow.document;
                            if (iDoc && iDoc.body) {
                                let ip = extractMaxPage(iDoc.body.innerText);
                                if (ip > p) p = ip;
                            }
                        } catch(e) {}
                    }
                    return p;
                }""")

                if detected_total and detected_total > 0:
                    total_pages = detected_total
                    logger.info(f"DYNAMICALLY DETECTED DOCUMENT TOTAL PAGES: {total_pages}")
            except Exception as det_err:
                logger.warning(f"Pre-flight page detection warning: {det_err}")

        # Fallback to dynamic loop page detection if initial detection was 0
        if total_pages <= 0:
            total_pages = 50  # Dynamic starting estimate, updated continuously


        # Reset scroll back to top to begin systematic page checklist sweep
        if self.page:
            try:
                await self.page.evaluate("""() => window.scrollTo(0, 0);""")
                await asyncio.sleep(0.5)
            except Exception:
                pass

        required_pages = set(range(1, total_pages + 1))
        step_delta = 750

        # -------------------------------------------------------------------------
        # STEP 2: Systematic Primary Sweep from Top to Bottom
        # -------------------------------------------------------------------------
        max_steps = total_pages + 10
        for step in range(1, max_steps + 1):
            if not self.is_auto_scrolling or not self.is_running:
                break

            self.screenshot_count += 1
            scroll_pos = (step - 1) * step_delta
            self.last_scroll_pos = scroll_pos
            self.last_page = min(total_pages, step)

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
                }""", step_delta)
            except Exception as scroll_err:
                logger.warning(f"Scroll step warning: {scroll_err}")

            # Extract raw DOM text & update page checklist map
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
                    raw_lines = [l.strip() for l in step_text.split('\n') if l.strip()]
                    current_p = self.last_page

                    for line in raw_lines:
                        p_match = re.search(r'(?:Page\s+(\d+)|\b(\d+))\s*(?:of|\/)\s*(\d+)', line, re.IGNORECASE)
                        if p_match:
                            cur_page_val = int(p_match.group(1) or p_match.group(2))
                            tot_page_val = int(p_match.group(3))
                            if 0 < cur_page_val <= 2000 and 0 < tot_page_val <= 2000:
                                current_p = cur_page_val
                                if tot_page_val > total_pages:
                                    total_pages = tot_page_val
                                    required_pages = set(range(1, total_pages + 1))
                                    logger.info(f"Dynamically updated total page count to {total_pages}")

                        if current_p not in page_map:
                            page_map[current_p] = []

                        if line not in page_map[current_p] and not line.startswith("==="):
                            page_map[current_p].append(line)

                    def has_real_page_content(lines: List[str]) -> bool:
                        real_text_len = 0
                        for l_str in lines:
                            l = l_str.strip()
                            if not l or l.startswith("==="):
                                continue
                            if l in ["Page", "/", "\\"] or re.match(r'^Page\s+\d+\s*(?:of|\/)\s*\d+$', l, re.IGNORECASE) or re.match(r'^\d+$', l):
                                continue
                            real_text_len += len(l)
                        return real_text_len >= 15

                    # Only count pages that contain TRULY FETCHED body content
                    captured_pages = set(p for p in page_map.keys() if has_real_page_content(page_map[p]))
                    self.captured_pages_set.update(captured_pages)
                    missing_pages = sorted(list(required_pages - captured_pages))


                    # Inject translucent 0.7 green highlight on fetched page containers in Playwright viewer
                    if captured_pages:
                        try:
                            await self.page.evaluate("""(fetchedList) => {
                                let setObj = new Set(fetchedList);
                                let containers = document.querySelectorAll('.ndfHFb-c4Qvld, [role="region"]');
                                containers.forEach((el, idx) => {
                                    if (setObj.has(idx + 1)) {
                                        el.style.border = '4px solid rgba(34, 197, 94, 0.7)';
                                        el.style.boxShadow = '0 0 12px rgba(34, 197, 94, 0.7)';
                                    }
                                });
                            }""", list(captured_pages))
                        except Exception:
                            pass

                    # Save continuous progress for pages with real content
                    formatted_pages = []
                    for p in sorted(captured_pages):
                        p_content = "\n".join(page_map[p])
                        formatted_pages.append(f"================================================================================\n📄 PAGE {p} OF {total_pages}\n================================================================================\n\n{p_content}")


                    if formatted_pages:
                        accumulated_full_text = "\n\n".join(formatted_pages)
                        text_file_path = Path("extracted_text") / f"{self.file_id}_extracted.txt"
                        text_file_path.write_text(accumulated_full_text, encoding="utf-8")
                        database.update_extracted_text(self.file_id, str(text_file_path), accumulated_full_text)
                        database.update_file_progress(self.file_id, self.last_page, self.last_scroll_pos)

                        database.update_file_page_status(self.file_id, total_pages, list(captured_pages))



                        try:
                            if self.websocket:
                                await self.websocket.send_json({
                                    "type": "live_text",
                                    "file_id": self.file_id,
                                    "text": accumulated_full_text,
                                    "step": self.screenshot_count,
                                    "pages_captured": len(captured_pages),
                                    "total_pages": total_pages,
                                    "missing_pages": missing_pages,
                                    "last_page": self.last_page,
                                    "last_scroll_pos": self.last_scroll_pos
                                })
                        except Exception:
                            pass

            except Exception as text_err:
                logger.warning(f"Live text extraction step warning: {text_err}")

            await asyncio.sleep(1.0)

        # -------------------------------------------------------------------------
        # STEP 3: Targeted Missing Page Rescue Sweeps
        # -------------------------------------------------------------------------
        captured_pages = set(page_map.keys())
        missing_pages = sorted(list(required_pages - captured_pages))

        if missing_pages and self.is_auto_scrolling and self.is_running:
            logger.info(f"Targeting missing pages rescue sweep: {missing_pages}")
            for target_missing_p in missing_pages:
                if not self.is_auto_scrolling or not self.is_running:
                    break

                target_y = (target_missing_p - 1) * 800
                logger.info(f"Targeted jump to missing Page {target_missing_p} (y={target_y})")

                try:
                    await self.page.evaluate(f"""(yPos) => {{
                        window.scrollTo(0, yPos);
                        if (document.body) document.body.scrollTop = yPos;
                        let scrollables = document.querySelectorAll('div, iframe, body, [role="main"], .ndfHFb-c4Qvld');
                        scrollables.forEach(el => {{ try {{ el.scrollTop = yPos; }} catch(e) {{}} }});
                    }}""", target_y)
                    await asyncio.sleep(1.2)

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

                    if step_text:
                        import re
                        raw_lines = [l.strip() for l in step_text.split('\n') if l.strip()]
                        cur_p = target_missing_p
                        for line in raw_lines:
                            p_match = re.search(r'Page\s+(\d+)\s+of\s+(\d+)', line, re.IGNORECASE)
                            if p_match:
                                cur_p = int(p_match.group(1))

                            if cur_p not in page_map:
                                page_map[cur_p] = []

                            if line not in page_map[cur_p] and not line.startswith("==="):
                                page_map[cur_p].append(line)

                        formatted_pages = []
                        for p in sorted(page_map.keys()):
                            p_content = "\n".join(page_map[p])
                            if p_content.strip():
                                formatted_pages.append(f"================================================================================\n📄 PAGE {p} OF {total_pages}\n================================================================================\n\n{p_content}")

                        if formatted_pages:
                            accumulated_full_text = "\n\n".join(formatted_pages)
                            text_file_path = Path("extracted_text") / f"{self.file_id}_extracted.txt"
                            text_file_path.write_text(accumulated_full_text, encoding="utf-8")
                            database.update_extracted_text(self.file_id, str(text_file_path))

                except Exception as rescue_err:
                    logger.warning(f"Rescue sweep warning for page {target_missing_p}: {rescue_err}")



    async def finish_and_extract_text(self):
        await self.stop_auto_scroll()
        await self._send_ws_json({
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
            
            await self._send_ws_json({
                "type": "extraction_complete",
                "file_id": self.file_id,
                "text_path": text_file_path,
                "downloaded": True
            })
        except Exception as e:
            logger.error(f"Text extraction failed: {e}")
            await self._send_ws_json({
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
            await self._send_ws_json({
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
                await self._send_ws_json({
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

            elif act_type in ["manual_prev", "manual_next", "manual_jump", "manual_capture"]:
                await self._handle_manual_navigation_and_capture(action)

            elif act_type == "mark_done":
                rec = database.mark_as_downloaded(self.file_id)
                await self._send_ws_json({
                    "type": "status",
                    "message": f"File {self.file_id} marked as Downloaded/Done!",
                    "downloaded": True
                })

            elif act_type == "reload":
                await self.page.reload()

        except Exception as e:
            logger.error(f"Error handling action {act_type}: {e}")

    async def _handle_manual_navigation_and_capture(self, action: Dict[str, Any]):
        act = action.get("action")
        if not self.page:
            return

        if act == "manual_prev":
            await self.page.evaluate("""() => {
                window.scrollBy(0, -750);
                let scrollables = document.querySelectorAll('div, iframe, body, [role="main"], .ndfHFb-c4Qvld');
                scrollables.forEach(el => { try { el.scrollTop -= 750; } catch(e) {} });
            }""")
            await asyncio.sleep(0.5)

        elif act == "manual_next":
            await self.page.evaluate("""() => {
                window.scrollBy(0, 750);
                let scrollables = document.querySelectorAll('div, iframe, body, [role="main"], .ndfHFb-c4Qvld');
                scrollables.forEach(el => { try { el.scrollTop += 750; } catch(e) {} });
            }""")
            await asyncio.sleep(0.5)

        elif act == "manual_jump":
            target_p = action.get("page", 1)
            target_y = (target_p - 1) * 750
            await self.page.evaluate(f"""() => {{
                window.scrollTo(0, {target_y});
                let scrollables = document.querySelectorAll('div, iframe, body, [role="main"], .ndfHFb-c4Qvld');
                scrollables.forEach(el => {{ try {{ el.scrollTop = {target_y}; }} catch(e) {{}} }});
            }}""")
            await asyncio.sleep(0.8)

        # Execute text capture from current visible DOM
        captured_text = await self.page.evaluate("""() => {
            let parts = [];
            if (document.body && document.body.innerText) parts.push(document.body.innerText);
            let iframes = document.querySelectorAll('iframe');
            iframes.forEach(iframe => {
                try {
                    let iDoc = iframe.contentDocument || iframe.contentWindow.document;
                    if (iDoc && iDoc.body && iDoc.body.innerText) parts.push(iDoc.body.innerText);
                } catch(e) {}
            });
            return parts.join('\\n\\n');
        }""")

        if captured_text and len(captured_text.strip()) > 10:
            import re
            lines = [l.strip() for l in captured_text.split('\n') if l.strip()]
            tot_p = 67
            
            # Detect total pages first
            for l in lines:
                m = re.search(r'(?:Page\s+(\d+)|\b(\d+))\s*(?:of|\/)\s*(\d+)', l, re.IGNORECASE)
                if m:
                    tot_p = int(m.group(3))
                    break

            # Parse DOM text into page-specific map
            page_text_map = {}
            current_detected_p = action.get("page") or self.last_page or 1

            for line in lines:
                pm = re.search(r'(?:Page\s+(\d+)|\b(\d+))\s*(?:of|\/)\s*(\d+)', line, re.IGNORECASE)
                if pm:
                    p_val = int(pm.group(1) or pm.group(2))
                    if 0 < p_val <= 2000:
                        current_detected_p = p_val
                
                if current_detected_p not in page_text_map:
                    page_text_map[current_detected_p] = []
                
                if line not in page_text_map[current_detected_p] and not line.startswith("==="):
                    page_text_map[current_detected_p].append(line)

            # Update extracted_text file cleanly for each captured page
            text_file_path = Path("extracted_text") / f"{self.file_id}_extracted.txt"
            existing_content = text_file_path.read_text(encoding="utf-8") if text_file_path.exists() else ""
            
            for p_num, p_lines in page_text_map.items():
                real_len = sum(len(l) for l in p_lines if l not in ["Page", "/", "\\"] and not re.match(r'^Page\s+\d+\s*(?:of|\/)\s*\d+$', l, re.IGNORECASE))
                if real_len < 15:
                    continue

                self.captured_pages_set.add(p_num)
                page_body = "\n".join(p_lines)
                new_block = f"================================================================================\n📄 PAGE {p_num} OF {tot_p}\n================================================================================\n\n{page_body.strip()}"
                
                page_pattern = rf"================================================================================\n📄 PAGE {p_num} OF \d+\n================================================================================\n\n[\s\S]*?(?================================================================================|$)"
                if re.search(page_pattern, existing_content):
                    existing_content = re.sub(page_pattern, new_block, existing_content)
                else:
                    existing_content = (existing_content + f"\n\n{new_block}").strip()

            text_file_path.write_text(existing_content, encoding="utf-8")
            database.update_extracted_text(self.file_id, str(text_file_path), existing_content)
            database.update_file_page_status(self.file_id, tot_p, list(self.captured_pages_set))



            await self._send_ws_json({
                "type": "live_text",
                "file_id": self.file_id,
                "text": existing_content,
                "total_pages": tot_p,
                "status_message": f"Captured Page(s) {sorted(list(page_text_map.keys()))} Text via Manual Control!"
            })




    async def _frame_broadcaster(self):
        """Captures screenshots continuously and sends binary/JPEG frames over WebSocket."""
        while self.is_running and self.page:
            try:
                # Apply permanent sticky non-flashing CSS styles to captured pages in DOM
                if self.captured_pages_set:
                    try:
                        await self.page.evaluate("""(fetchedList) => {
                            let styleTag = document.getElementById('py-permanent-style');
                            if (!styleTag && document.head) {
                                styleTag = document.createElement('style');
                                styleTag.id = 'py-permanent-style';
                                styleTag.innerHTML = `
                                    .captured-page-permanent {
                                        border: 6px solid #22c55e !important;
                                        box-shadow: 0 0 30px rgba(34, 197, 94, 0.95), inset 0 0 40px rgba(34, 197, 94, 0.35) !important;
                                        position: relative !important;
                                    }
                                    .vivid-capture-badge {
                                        position: absolute !important;
                                        top: 15px !important;
                                        right: 15px !important;
                                        background-color: rgba(34, 197, 94, 0.95) !important;
                                        color: #ffffff !important;
                                        padding: 8px 18px !important;
                                        border-radius: 20px !important;
                                        font-weight: 800 !important;
                                        font-size: 14px !important;
                                        box-shadow: 0 4px 15px rgba(0, 0, 0, 0.6) !important;
                                        z-index: 999999 !important;
                                    }
                                `;
                                document.head.appendChild(styleTag);
                            }

                            let setObj = new Set(fetchedList);

                            function getIndividualPageContainers() {
                                let pageEls = Array.from(document.querySelectorAll('.drive-viewer-page, .ndfHFb-c4Qvld-page, [role="region"][aria-label*="Page"], [data-page-number]'));
                                if (pageEls.length > 0) return pageEls;

                                let parent = document.querySelector('.ndfHFb-c4Qvld') || document.body;
                                if (parent) {
                                    let children = Array.from(parent.querySelectorAll('div, img, canvas, section'));
                                    let pageCards = children.filter(el => {
                                        let rect = el.getBoundingClientRect();
                                        return rect.width > 250 && rect.height > 350 && rect.width < window.innerWidth * 0.92 && rect.height < window.innerHeight * 0.92;
                                    });
                                    if (pageCards.length > 0) return pageCards;
                                }
                                return [];
                            }

                            let containers = getIndividualPageContainers();
                            containers.forEach((el, idx) => {
                                let pNum = idx + 1;
                                if (setObj.has(pNum)) {
                                    if (!el.classList.contains('captured-page-permanent')) {
                                        el.classList.add('captured-page-permanent');
                                    }
                                    let badge = el.querySelector('.vivid-capture-badge');
                                    if (!badge) {
                                        badge = document.createElement('div');
                                        badge.className = 'vivid-capture-badge';
                                        badge.innerHTML = '📄 PAGE ' + pNum + ' CAPTURED ✓';
                                        el.appendChild(badge);
                                    }
                                } else {
                                    if (el.classList.contains('captured-page-permanent')) {
                                        el.classList.remove('captured-page-permanent');
                                    }
                                    let badge = el.querySelector('.vivid-capture-badge');
                                    if (badge) badge.remove();
                                }
                            });
                        }""", list(self.captured_pages_set))
                    except Exception:
                        pass



                screenshot_bytes = await self.page.screenshot(type="jpeg", quality=60)
                page_title = await self.page.title()
                page_url = self.page.url

                import base64
                b64_frame = base64.b64encode(screenshot_bytes).decode("utf-8")

                await self._send_ws_json({
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
            except Exception:
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

