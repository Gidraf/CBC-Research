/**
 * 🚀 Google Drive Mobile JS Extractor & Langfuse Direct Ingestion Script
 * =========================================================================
 * Run this script directly in your mobile browser console, Tampermonkey,
 * Scriptable, or as a Bookmarklet on any Google Drive PDF document view page.
 *
 * Features:
 *  - Compatible with Google Drive TrustedTypes CSP restrictions.
 *  - Stores Langfuse credentials (Public Key, Secret Key, Host, Dataset) in localStorage.
 *  - Prompts for missing credentials with an interactive mobile modal dialog.
 *  - Extracts DOM text, detects page numbers, and structures per-page blocks.
 *  - Pushes extracted text directly to Langfuse Dataset API.
 */

(function () {
    'use strict';

    const STORAGE_KEY_HOST = 'lf_host';
    const STORAGE_KEY_PK = 'lf_pk';
    const STORAGE_KEY_SK = 'lf_sk';
    const STORAGE_KEY_DATASET = 'lf_dataset';

    const DEFAULT_HOST = 'https://cloud.langfuse.com';
    const DEFAULT_DATASET = 'CBC_Research_Curriculum_Designs';

    // ── 0. TRUSTED TYPES CSP BYPASS HELPER ────────────────────────────────────
    function setTrustedHTML(element, htmlString) {
        if (!element) return;

        // Try TrustedTypes policy first if supported
        if (window.trustedTypes && window.trustedTypes.createPolicy) {
            try {
                let policy = window.trustedTypes.defaultPolicy;
                if (!policy) {
                    try {
                        policy = window.trustedTypes.createPolicy('gdriveLangfusePolicy', {
                            createHTML: (s) => s
                        });
                    } catch (e) {
                        // Policy might exist under default or another name
                    }
                }
                if (policy && policy.createHTML) {
                    element.innerHTML = policy.createHTML(htmlString);
                    return;
                }
            } catch (err) {
                // Fallback to DOM parsing
            }
        }

        // Fallback DOM node building via DOMParser (bypasses direct innerHTML assignment)
        try {
            const parser = new DOMParser();
            const doc = parser.parseFromString(htmlString, 'text/html');
            element.replaceChildren(...doc.body.childNodes);
            return;
        } catch (e) {}

        // Secondary fallback via Range
        try {
            const range = document.createRange();
            const fragment = range.createContextualFragment(htmlString);
            element.replaceChildren(fragment);
        } catch (e) {
            element.textContent = htmlString.replace(/<[^>]*>?/gm, '');
        }
    }

    // ── 1. HELPERS & CREDENTIALS MANAGEMENT ──────────────────────────────────
    function getStoredCredentials() {
        return {
            host: (localStorage.getItem(STORAGE_KEY_HOST) || DEFAULT_HOST).replace(/\/+$/, ''),
            pk: (localStorage.getItem(STORAGE_KEY_PK) || '').trim(),
            sk: (localStorage.getItem(STORAGE_KEY_SK) || '').trim(),
            dataset: (localStorage.getItem(STORAGE_KEY_DATASET) || DEFAULT_DATASET).trim()
        };
    }

    function saveCredentials(host, pk, sk, dataset) {
        localStorage.setItem(STORAGE_KEY_HOST, (host || DEFAULT_HOST).trim());
        localStorage.setItem(STORAGE_KEY_PK, (pk || '').trim());
        localStorage.setItem(STORAGE_KEY_SK, (sk || '').trim());
        localStorage.setItem(STORAGE_KEY_DATASET, (dataset || DEFAULT_DATASET).trim());
    }

    function openCredentialsModal(onSavedCallback) {
        let modal = document.getElementById('lf-credentials-modal');
        if (!modal) {
            modal = document.createElement('div');
            modal.id = 'lf-credentials-modal';
            modal.style.cssText = `
                position: fixed;
                top: 0; left: 0; right: 0; bottom: 0;
                z-index: 999999999;
                background: rgba(2, 6, 23, 0.88);
                display: flex;
                align-items: center;
                justify-content: center;
                padding: 16px;
                box-sizing: border-box;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            `;
            document.body.appendChild(modal);
        }

        const creds = getStoredCredentials();

        setTrustedHTML(modal, `
            <div style="background:#0f172a; border:1px solid #38bdf8; border-radius:12px; padding:20px; width:100%; max-width:420px; box-shadow:0 20px 50px rgba(0,0,0,0.9); color:white; box-sizing:border-box;">
                <div style="display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid #334155; padding-bottom:10px; margin-bottom:14px;">
                    <h3 style="margin:0; font-size:16px; font-weight:700; color:#38bdf8;">⚙️ Langfuse API Credentials</h3>
                    <button id="lf-modal-close" style="background:none; border:none; color:#94a3b8; font-size:20px; cursor:pointer;">&times;</button>
                </div>
                <p style="font-size:12px; color:#94a3b8; margin-top:0; margin-bottom:14px; line-height:1.4;">
                    Enter your Langfuse keys below. They are saved securely in your phone's browser local storage and accessed automatically for every text sync.
                </p>
                <div style="margin-bottom:12px;">
                    <label style="display:block; font-size:12px; font-weight:600; color:#e2e8f0; margin-bottom:4px;">🌐 Langfuse Host URL:</label>
                    <input type="text" id="lf-input-host" value="${creds.host}" placeholder="https://cloud.langfuse.com" style="width:100%; padding:10px; border-radius:6px; background:#1e293b; border:1px solid #334155; color:white; font-size:13px; box-sizing:border-box;">
                </div>
                <div style="margin-bottom:12px;">
                    <label style="display:block; font-size:12px; font-weight:600; color:#e2e8f0; margin-bottom:4px;">🔑 Public Key (pk-lf-...):</label>
                    <input type="text" id="lf-input-pk" value="${creds.pk}" placeholder="pk-lf-..." style="width:100%; padding:10px; border-radius:6px; background:#1e293b; border:1px solid #334155; color:white; font-size:13px; box-sizing:border-box;">
                </div>
                <div style="margin-bottom:12px;">
                    <label style="display:block; font-size:12px; font-weight:600; color:#e2e8f0; margin-bottom:4px;">🔒 Secret Key (sk-lf-...):</label>
                    <input type="password" id="lf-input-sk" value="${creds.sk}" placeholder="sk-lf-..." style="width:100%; padding:10px; border-radius:6px; background:#1e293b; border:1px solid #334155; color:white; font-size:13px; box-sizing:border-box;">
                </div>
                <div style="margin-bottom:16px;">
                    <label style="display:block; font-size:12px; font-weight:600; color:#e2e8f0; margin-bottom:4px;">📦 Dataset Name:</label>
                    <input type="text" id="lf-input-dataset" value="${creds.dataset}" placeholder="CBC_Research_Curriculum_Designs" style="width:100%; padding:10px; border-radius:6px; background:#1e293b; border:1px solid #334155; color:white; font-size:13px; box-sizing:border-box;">
                </div>
                <div style="display:flex; justify-content:flex-end; gap:8px;">
                    <button id="lf-modal-cancel" style="padding:10px 16px; border-radius:6px; background:#334155; color:white; border:none; font-weight:600; cursor:pointer;">Cancel</button>
                    <button id="lf-modal-save" style="padding:10px 20px; border-radius:6px; background:linear-gradient(135deg, #22c55e, #16a34a); color:white; border:none; font-weight:700; cursor:pointer;">💾 Save Keys</button>
                </div>
            </div>
        `);

        modal.style.display = 'flex';

        document.getElementById('lf-modal-close').onclick = () => { modal.style.display = 'none'; };
        document.getElementById('lf-modal-cancel').onclick = () => { modal.style.display = 'none'; };

        document.getElementById('lf-modal-save').onclick = () => {
            const host = document.getElementById('lf-input-host').value.trim();
            const pk = document.getElementById('lf-input-pk').value.trim();
            const sk = document.getElementById('lf-input-sk').value.trim();
            const dataset = document.getElementById('lf-input-dataset').value.trim();

            if (!pk || !sk) {
                alert('⚠️ Public Key and Secret Key are required!');
                return;
            }

            saveCredentials(host, pk, sk, dataset);
            modal.style.display = 'none';
            if (onSavedCallback) onSavedCallback(getStoredCredentials());
        };
    }

    // ── 2. FILE ID & DOCUMENT METADATA EXTRACTION ─────────────────────────────
    function extractFileId() {
        const url = window.location.href;
        const match = url.match(/\/d\/([a-zA-Z0-9_-]{25,})/) || url.match(/[?&]id=([a-zA-Z0-9_-]{25,})/);
        if (match) return match[1];

        const domMatch = document.body.innerHTML.match(/1[a-zA-Z0-9_-]{25,}/);
        return domMatch ? domMatch[0] : 'gdrive_doc_' + Date.now();
    }

    function extractDocTitle() {
        let title = document.title || '';
        title = title.replace(/\s*-\s*Google\s*Drive\s*$/i, '').trim();

        const headerEl = document.querySelector('.ndfHFb-c4Qvld-title, [role="heading"], .drive-viewer-title');
        if (headerEl && headerEl.innerText && headerEl.innerText.length > 2) {
            return headerEl.innerText.trim();
        }

        return title || 'Untitled Curriculum Document';
    }

    // ── 3. DOM TEXT & PAGE EXTRACTION ────────────────────────────────────────
    function extractTextFromPage() {
        let textParts = [];

        if (document.body && document.body.innerText) {
            textParts.push(document.body.innerText);
        }

        const iframes = document.querySelectorAll('iframe');
        iframes.forEach(iframe => {
            try {
                const iDoc = iframe.contentDocument || iframe.contentWindow.document;
                if (iDoc && iDoc.body && iDoc.body.innerText) {
                    textParts.push(iDoc.body.innerText);
                }
            } catch (e) {}
        });

        const rawText = textParts.join('\n\n');
        return processAndStructureText(rawText);
    }

    function processAndStructureText(rawText) {
        const lines = rawText.split('\n').map(l => l.trim()).filter(l => l.length > 0);
        let totalPages = 67;

        for (const l of lines) {
            const m = l.match(/(?:Page\s+(\d+)|\b(\d+))\s*(?:of|\/)\s*(\d+)/i);
            if (m) {
                totalPages = parseInt(m[3]);
                break;
            }
        }

        let pageMap = {};
        let currentP = 1;

        for (const line of lines) {
            const pm = line.match(/(?:Page\s+(\d+)|\b(\d+))\s*(?:of|\/)\s*(\d+)/i);
            if (pm) {
                const pVal = parseInt(pm[1] || pm[2]);
                if (pVal > 0 && pVal <= 2000) currentP = pVal;
            }

            if (!pageMap[currentP]) pageMap[currentP] = [];
            if (!pageMap[currentP].includes(line) && !line.startsWith('===')) {
                pageMap[currentP].push(line);
            }
        }

        let formattedBlocks = [];
        let pagesCount = 0;

        const sortedPages = Object.keys(pageMap).map(Number).sort((a, b) => a - b);
        for (const pNum of sortedPages) {
            const pLines = pageMap[pNum];
            const realLen = pLines.filter(l => l !== 'Page' && l !== '/' && !l.match(/^Page\s+\d+\s*(?:of|\/)\s*\d+$/i)).join('').length;
            if (realLen >= 15) {
                pagesCount++;
                formattedBlocks.push(`================================================================================\n📄 PAGE ${pNum} OF ${totalPages}\n================================================================================\n\n` + pLines.join('\n'));
            }
        }

        const fullText = formattedBlocks.length > 0 ? formattedBlocks.join('\n\n') : rawText;

        return {
            fullText: fullText,
            totalPages: totalPages,
            capturedPagesCount: pagesCount,
            charCount: fullText.length
        };
    }

    // ── 4. LANGFUSE INGESTION API CALL ───────────────────────────────────────
    async function syncToLangfuse(creds, extractionData) {
        const fileId = extractFileId();
        const title = extractDocTitle();

        const endpoint = `${creds.host}/api/public/dataset-items`;
        const authHeader = 'Basic ' + btoa(`${creds.pk}:${creds.sk}`);

        const payload = {
            datasetName: creds.dataset,
            input: {
                file_id: fileId,
                title: title,
                url: window.location.href,
                source: 'Mobile_JS_Browser_Injector',
                user_agent: navigator.userAgent,
                captured_at: new Date().toISOString()
            },
            expectedOutput: extractionData.fullText,
            metadata: {
                total_pages: extractionData.totalPages,
                captured_pages_count: extractionData.capturedPagesCount,
                char_count: extractionData.charCount,
                device: 'Mobile Browser'
            }
        };

        const resp = await fetch(endpoint, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': authHeader
            },
            body: JSON.stringify(payload)
        });

        if (!resp.ok) {
            const errText = await resp.text();
            throw new Error(`Langfuse HTTP ${resp.status}: ${errText}`);
        }

        return await resp.json();
    }

    // ── 5. FLOATING HUD UI CONTROLLER ─────────────────────────────────────────
    function createHUD() {
        let hud = document.getElementById('gdrive-lf-hud');
        if (hud) return hud;

        hud = document.createElement('div');
        hud.id = 'gdrive-lf-hud';
        hud.style.cssText = `
            position: fixed;
            top: 10px;
            right: 10px;
            z-index: 99999999;
            background: #0f172a;
            border: 1px solid #38bdf8;
            border-radius: 12px;
            padding: 12px 14px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.85);
            color: #ffffff;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            font-size: 12px;
            width: 280px;
            box-sizing: border-box;
        `;

        setTrustedHTML(hud, `
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; border-bottom:1px solid #334155; padding-bottom:6px;">
                <strong style="color:#38bdf8; font-size:13px;">🚀 Langfuse Mobile Sync</strong>
                <button id="lf-btn-close" style="background:none; border:none; color:#94a3b8; font-size:16px; cursor:pointer;">&times;</button>
            </div>
            <div id="lf-status" style="color:#e2e8f0; margin-bottom:10px; line-height:1.4;">📄 Initializing...</div>
            <div style="display:flex; gap:6px; flex-wrap:wrap;">
                <button id="lf-btn-sync" style="flex:1; background:linear-gradient(135deg, #22c55e, #16a34a); color:white; border:none; padding:8px; border-radius:6px; font-weight:700; cursor:pointer;">🚀 Sync Now</button>
                <button id="lf-btn-scroll" style="flex:1; background:#0ea5e9; color:white; border:none; padding:8px; border-radius:6px; font-weight:600; cursor:pointer;">📜 Auto Scroll</button>
            </div>
            <div style="display:flex; gap:6px; margin-top:6px;">
                <button id="lf-btn-keys" style="flex:1; background:#334155; color:white; border:none; padding:6px; border-radius:6px; font-size:11px; cursor:pointer;">⚙️ Keys</button>
                <button id="lf-btn-copy" style="flex:1; background:#334155; color:white; border:none; padding:6px; border-radius:6px; font-size:11px; cursor:pointer;">📋 Copy Text</button>
            </div>
        `);

        document.body.appendChild(hud);

        document.getElementById('lf-btn-close').onclick = () => hud.remove();

        document.getElementById('lf-btn-keys').onclick = () => {
            openCredentialsModal(() => {
                updateHUDStatus();
            });
        };

        document.getElementById('lf-btn-sync').onclick = async () => {
            const btn = document.getElementById('lf-btn-sync');
            let creds = getStoredCredentials();

            if (!creds.pk || !creds.sk) {
                openCredentialsModal(async (newCreds) => {
                    await doSyncProcess(btn, newCreds);
                });
                return;
            }

            await doSyncProcess(btn, creds);
        };

        document.getElementById('lf-btn-scroll').onclick = () => {
            let scrolled = 0;
            const scrollInterval = setInterval(() => {
                window.scrollBy(0, 900);
                scrolled += 900;
                updateHUDStatus();
                if (scrolled >= 40000 || (window.innerHeight + window.scrollY) >= document.body.offsetHeight) {
                    clearInterval(scrollInterval);
                    alert('📜 Auto-scroll completed! Click "🚀 Sync Now" to push text to Langfuse.');
                }
            }, 600);
        };

        document.getElementById('lf-btn-copy').onclick = () => {
            const res = extractTextFromPage();
            navigator.clipboard.writeText(res.fullText);
            alert(`📋 Copied ${res.charCount} characters of extracted text to clipboard!`);
        };

        return hud;
    }

    async function doSyncProcess(btn, creds) {
        btn.disabled = true;
        btn.innerText = '⏳ Syncing...';
        try {
            const result = extractTextFromPage();
            await syncToLangfuse(creds, result);
            alert(`✅ Sync Successful!\n\nExtracted ${result.capturedPagesCount} pages (${result.charCount} chars) and pushed directly to Langfuse dataset '${creds.dataset}'!`);
        } catch (err) {
            alert(`❌ Sync Failed:\n${err.message}`);
        } finally {
            btn.disabled = false;
            btn.innerText = '🚀 Sync Now';
            updateHUDStatus();
        }
    }

    function updateHUDStatus() {
        const hud = createHUD();
        const statusEl = document.getElementById('lf-status');
        const res = extractTextFromPage();
        const creds = getStoredCredentials();

        const keyStatus = (creds.pk && creds.sk) ? '<span style="color:#22c55e;">🔑 Keys Configured</span>' : '<span style="color:#f97316;">⚠️ Missing Keys</span>';
        setTrustedHTML(statusEl, `
            <strong>Doc:</strong> ${extractDocTitle().substring(0, 24)}...<br>
            <strong>Pages:</strong> ${res.capturedPagesCount} / ${res.totalPages} (${res.charCount} chars)<br>
            <strong>Status:</strong> ${keyStatus}
        `);
    }

    // ── 6. INITIALIZATION ───────────────────────────────────────────────────
    function init() {
        const creds = getStoredCredentials();
        updateHUDStatus();

        if (!creds.pk || !creds.sk) {
            openCredentialsModal(() => {
                updateHUDStatus();
            });
        }
    }

    init();

})();
