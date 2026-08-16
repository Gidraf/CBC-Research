let currentFileId = null;
let currentFileObj = null;
let ws = null;
let jsEnabled = false;
let allFiles = [];

// DOM Elements
const searchInput = document.getElementById('search-input');
const gradeFilter = document.getElementById('grade-filter');
const statusFilter = document.getElementById('status-filter');
const filesList = document.getElementById('files-list');

const activeGrade = document.getElementById('active-grade');
const activeSubject = document.getElementById('active-subject');
const activeId = document.getElementById('active-id');

const btnToggleJs = document.getElementById('btn-toggle-js');
const jsStatusPill = document.getElementById('js-status-pill');
const streamFrame = document.getElementById('stream-frame');
const streamOverlay = document.getElementById('stream-overlay');
const overlayStatusText = document.getElementById('overlay-status-text');
const streamUrlDisplay = document.getElementById('stream-url-display');
const contextMenu = document.getElementById('context-menu');

let lastClickCoords = { x: 0, y: 0 };

document.addEventListener('DOMContentLoaded', () => {
    fetchStats();
    fetchFiles();
    setupCanvasEvents();
    setupGlobalContextMenuDismiss();
});

// Fetch Stats
async function fetchStats() {
    try {
        const resp = await fetch('/api/stats');
        const data = await resp.json();
        document.getElementById('stat-total').textContent = data.total || 0;
        document.getElementById('stat-downloaded').textContent = data.downloaded || 0;
        document.getElementById('stat-pending').textContent = data.pending || 0;
    } catch (e) {
        console.error('Error fetching stats:', e);
    }
}

// Fetch Files List
async function fetchFiles() {
    try {
        const search = searchInput.value;
        const grade = gradeFilter.value;
        const status = statusFilter.value;

        const params = new URLSearchParams();
        if (search) params.append('search', search);
        if (grade) params.append('grade', grade);
        if (status) params.append('status', status);

        const resp = await fetch(`/api/files?${params.toString()}`);
        allFiles = await resp.json();

        updateGradeFilterOptions(allFiles);
        renderFilesList(allFiles);
        document.getElementById('file-count-badge').textContent = `${allFiles.length} items`;
    } catch (e) {
        filesList.innerHTML = `<div class="empty-state">Failed loading files: ${e.message}</div>`;
    }
}

function updateGradeFilterOptions(files) {
    const currentSelected = gradeFilter.value;
    const grades = [...new Set(files.map(f => f.grade))].sort();
    
    // Keep 'All Grades' as first option
    gradeFilter.innerHTML = '<option value="">All Grades</option>';
    grades.forEach(g => {
        const opt = document.createElement('option');
        opt.value = g;
        opt.textContent = g;
        if (g === currentSelected) opt.selected = true;
        gradeFilter.appendChild(opt);
    });
}

function renderFilesList(files) {
    if (files.length === 0) {
        filesList.innerHTML = '<div class="empty-state">No matching files found. Click "Extract KICD Links" above.</div>';
        return;
    }

    filesList.innerHTML = files.map(f => {
        const isDownloaded = f.downloaded === 1;
        const isActive = f.file_id === currentFileId;
        return `
            <div class="file-card ${isActive ? 'active' : ''}" onclick="selectFile('${f.file_id}')">
                <div class="file-card-header">
                    <span class="file-grade">${escapeHtml(f.grade)}</span>
                    <span class="status-tag ${isDownloaded ? 'downloaded' : 'pending'}">
                        ${isDownloaded ? 'Downloaded' : 'Pending'}
                    </span>
                </div>
                <div class="file-subject">${escapeHtml(f.subject)}</div>
                <div class="file-id-text">ID: ${f.file_id}</div>
                <div class="card-actions">
                    <button class="btn btn-sm btn-secondary" onclick="event.stopPropagation(); selectFile('${f.file_id}')">
                        👁️ Stream
                    </button>
                    <button class="btn btn-sm btn-primary" onclick="event.stopPropagation(); downloadFileNoJS('${f.file_id}')">
                        📥 Download PDF
                    </button>
                    <button class="btn btn-sm ${isDownloaded ? 'btn-secondary' : 'btn-success'}" onclick="event.stopPropagation(); toggleMarkDone('${f.file_id}')">
                        ${isDownloaded ? 'Done' : '✅ Mark'}
                    </button>
                </div>
            </div>
        `;
    }).join('');
}

function selectFile(fileId) {
    const fileObj = allFiles.find(f => f.file_id === fileId);
    if (!fileObj) return;

    currentFileId = fileId;
    currentFileObj = fileObj;

    activeGrade.textContent = fileObj.grade;
    activeSubject.textContent = fileObj.subject;
    activeId.textContent = `ID: ${fileId}`;

    renderFilesList(allFiles);
    connectWebSocket(fileId);
}

// Direct No-JS Downloader Function
async function downloadFileNoJS(fileId) {
    streamOverlay.classList.remove('hidden');
    overlayStatusText.textContent = `Downloading No-JS PDF for ${fileId}...`;
    try {
        const resp = await fetch(`/api/files/${fileId}/download-nojs`, { method: 'POST' });
        if (resp.ok) {
            const data = await resp.json();
            alert(`✅ Download Complete!\nFile saved to: ${data.local_path || 'downloads/'}`);
            await fetchStats();
            await fetchFiles();
        } else {
            alert('⚠️ Download failed or restricted by Google Drive permissions.');
        }
    } catch (e) {
        alert('Error initiating download: ' + e.message);
    } finally {
        streamOverlay.classList.add('hidden');
    }
}

async function downloadActiveFileNoJS() {
    if (!currentFileId) {
        alert('Please select a file first.');
        return;
    }
    await downloadFileNoJS(currentFileId);
}

// Toggle Mark Done
async function toggleMarkDone(fileId) {
    try {
        await fetch(`/api/files/${fileId}/toggle-downloaded`, { method: 'POST' });
        await fetchStats();
        await fetchFiles();
    } catch (e) {
        console.error('Error toggling status:', e);
    }
}

async function markActiveFileDone() {
    if (!currentFileId) return;
    await toggleMarkDone(currentFileId);
}


// WebSocket Connection & Playwright Live Stream
function connectWebSocket(fileId) {
    if (ws) {
        ws.close();
    }

    streamOverlay.classList.remove('hidden');
    overlayStatusText.textContent = `Connecting to Playwright stream for ${fileId}...`;

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${protocol}//${window.location.host}/ws/stream/${fileId}`);

    ws.onopen = () => {
        overlayStatusText.textContent = `Initializing browser session (JS: ${jsEnabled ? 'Enabled' : 'Disabled'})...`;
    };

    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);

        if (data.type === 'frame') {
            streamOverlay.classList.add('hidden');
            streamFrame.src = data.frame;
            streamUrlDisplay.textContent = `URL: ${data.url || 'loading...'}`;
            updateJSButtonUI(data.js_enabled);
        } else if (data.type === 'status') {
            overlayStatusText.textContent = data.message;
            if (data.downloaded) {
                fetchStats();
                fetchFiles();
            }
        } else if (data.type === 'download_complete') {
            alert(`✅ File Downloaded Successfully to: ${data.local_path}`);
            fetchStats();
            fetchFiles();
        }
    };

    ws.onclose = () => {
        streamOverlay.classList.remove('hidden');
        overlayStatusText.textContent = 'Stream disconnected. Click a file to reconnect.';
    };

    ws.onerror = (err) => {
        console.error('WebSocket Error:', err);
    };
}

// BIG BUTTON: Toggle JavaScript
function toggleJS() {
    jsEnabled = !jsEnabled;
    updateJSButtonUI(jsEnabled);

    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({
            action: 'toggle_js',
            js_enabled: jsEnabled
        }));
        streamOverlay.classList.remove('hidden');
        overlayStatusText.textContent = `Switching Playwright to ${jsEnabled ? 'JavaScript Enabled' : 'No-JS (Disabled)'} Mode...`;
    }
}

function updateJSButtonUI(isJsOn) {
    jsEnabled = isJsOn;
    if (isJsOn) {
        btnToggleJs.className = 'btn btn-js-toggle js-enabled';
        btnToggleJs.querySelector('.toggle-icon').textContent = '⚡';
        btnToggleJs.querySelector('.toggle-text').textContent = 'ENABLE JAVASCRIPT';
        jsStatusPill.textContent = 'JS ENABLED';
    } else {
        btnToggleJs.className = 'btn btn-js-toggle active-nojs';
        btnToggleJs.querySelector('.toggle-icon').textContent = '🚫';
        btnToggleJs.querySelector('.toggle-text').textContent = 'DISABLE JAVASCRIPT';
        jsStatusPill.textContent = 'No-JS ACTIVE';
    }
}

function reloadStream() {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ action: 'reload' }));
    }
}

// Canvas Mouse & Keyboard Events Setup
function setupCanvasEvents() {
    streamFrame.addEventListener('click', (e) => {
        if (!ws || ws.readyState !== WebSocket.OPEN) return;
        const coords = getCanvasCoords(e);
        ws.send(JSON.stringify({
            action: 'click',
            x: coords.x,
            y: coords.y,
            button: 'left'
        }));
    });

    streamFrame.addEventListener('wheel', (e) => {
        if (!ws || ws.readyState !== WebSocket.OPEN) return;
        e.preventDefault();
        ws.send(JSON.stringify({
            action: 'scroll',
            delta_x: e.deltaX,
            delta_y: e.deltaY
        }));
    }, { passive: false });

    streamFrame.addEventListener('keydown', (e) => {
        if (!ws || ws.readyState !== WebSocket.OPEN) return;
        ws.send(JSON.stringify({
            action: 'keypress',
            key: e.key
        }));
    });

    // Custom Right Click Handler on Stream Canvas
    streamFrame.addEventListener('contextmenu', (e) => {
        e.preventDefault();
        lastClickCoords = getCanvasCoords(e);
        
        // Show Custom Right-Click Overlay Context Menu
        contextMenu.style.display = 'block';
        contextMenu.style.left = `${e.clientX}px`;
        contextMenu.style.top = `${e.clientY}px`;
    });
}

function getCanvasCoords(e) {
    const rect = streamFrame.getBoundingClientRect();
    const scaleX = 1280 / rect.width;
    const scaleY = 800 / rect.height;
    return {
        x: Math.round((e.clientX - rect.left) * scaleX),
        y: Math.round((e.clientY - rect.top) * scaleY)
    };
}

function setupGlobalContextMenuDismiss() {
    document.addEventListener('click', (e) => {
        if (!contextMenu.contains(e.target)) {
            contextMenu.style.display = 'none';
        }
    });
}

// Context Menu Action Handlers
function contextMarkDone() {
    contextMenu.style.display = 'none';
    markActiveFileDone();
}

function contextDownloadNoJS() {
    contextMenu.style.display = 'none';
    downloadActiveFileNoJS();
}

function contextToggleJS() {

    contextMenu.style.display = 'none';
    toggleJS();
}

function contextReload() {
    contextMenu.style.display = 'none';
    reloadStream();
}

function contextPassRightClick() {
    contextMenu.style.display = 'none';
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({
            action: 'right_click',
            x: lastClickCoords.x,
            y: lastClickCoords.y
        }));
    }
}

function escapeHtml(text) {
    return text ? text.replace(/[&<>"']/g, function(m) {
        return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' }[m];
    }) : '';
}
