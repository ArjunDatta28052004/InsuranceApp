/* ══════════════════════════════════════════════════════════════════════════
   InsuranceIQ — Frontend Logic
   ══════════════════════════════════════════════════════════════════════════ */

let uploadPollInterval = null;
let allHistory = [];

// ── Init ──────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    loadDocuments();
    loadStats();
    setupDragDrop();
});

// ── Drag & Drop ───────────────────────────────────────────────────────────────
function setupDragDrop() {
    const zone = document.getElementById('uploadZone');
    const label = document.getElementById('uploadLabel');

    zone.addEventListener('dragover', e => {
        e.preventDefault();
        label.classList.add('drag-over');
    });
    zone.addEventListener('dragleave', () => label.classList.remove('drag-over'));
    zone.addEventListener('drop', e => {
        e.preventDefault();
        label.classList.remove('drag-over');
        const file = e.dataTransfer.files[0];
        if (file && file.name.toLowerCase().endsWith('.pdf')) {
            uploadFile(file);
        } else {
            showToast('error', 'Only PDF files are accepted.');
        }
    });
}

// ── PDF Upload ────────────────────────────────────────────────────────────────
function uploadPDF() {
    const input = document.getElementById('pdfInput');
    if (input.files[0]) uploadFile(input.files[0]);
}

async function uploadFile(file) {
    if (!file.name.toLowerCase().endsWith('.pdf')) {
        return showToast('error', 'Only PDF files are supported.');
    }

    const formData = new FormData();
    formData.append('file', file);

    showProgress(0, `Uploading ${file.name}…`);

    try {
        const res = await fetch('/upload', { method: 'POST', body: formData });
        const data = await res.json();

        if (!data.success) {
            hideProgress();
            return showToast('error', data.message || 'Upload failed.');
        }

        showProgress(5, 'Processing policy document…');
        pollUploadStatus(data.task_id);
    } catch (e) {
        hideProgress();
        showToast('error', 'Upload error: ' + e.message);
    }
}

function pollUploadStatus(taskId) {
    if (uploadPollInterval) clearInterval(uploadPollInterval);

    uploadPollInterval = setInterval(async () => {
        try {
            const res = await fetch(`/upload_status/${taskId}`);
            const data = await res.json();

            if (data.status === 'processing') {
                showProgress(data.progress || 0, `Embedding policy… ${data.progress || 0}%`);
            } else if (data.status === 'completed') {
                clearInterval(uploadPollInterval);
                hideProgress();
                showToast('success', 'Policy document processed! You can now ask questions.');
                loadDocuments();
                loadStats();
                document.getElementById('pdfInput').value = '';
            } else if (data.status === 'error') {
                clearInterval(uploadPollInterval);
                hideProgress();
                showToast('error', data.message || 'Processing failed.');
            }
        } catch (e) {
            clearInterval(uploadPollInterval);
            hideProgress();
        }
    }, 1000);
}

function showProgress(pct, label) {
    document.getElementById('progressWrap').style.display = 'block';
    document.getElementById('uploadLabel').style.display = 'none';
    document.getElementById('progressFill').style.width = pct + '%';
    document.getElementById('progressLabel').textContent = label;
}

function hideProgress() {
    document.getElementById('progressWrap').style.display = 'none';
    document.getElementById('uploadLabel').style.display = '';
}

// ── Documents List ────────────────────────────────────────────────────────────
async function loadDocuments() {
    try {
        const res = await fetch('/documents');
        const data = await res.json();
        if (!data.success) return;

        const docs = data.documents;
        const badge = document.getElementById('docBadge');
        const list = document.getElementById('docList');

        badge.textContent = docs.length;

        if (!docs.length) {
            list.innerHTML = '<li class="doc-empty">No documents yet. Upload your policy PDF.</li>';
            return;
        }

        list.innerHTML = docs.map(d => `
            <li class="doc-item">
                <div class="doc-icon">📄</div>
                <div class="doc-info">
                    <div class="doc-name" title="${escHtml(d.original_filename)}">${escHtml(d.original_filename || d.filename)}</div>
                    <div class="doc-date">${formatDate(d.upload_date)}</div>
                </div>
                <button class="doc-del" onclick="deleteDoc(${d.doc_id})" title="Remove document">🗑</button>
            </li>
        `).join('');
    } catch (e) {
        console.error('loadDocuments error:', e);
    }
}

async function deleteDoc(docId) {
    if (!confirm('Remove this document? Your questions referencing it will remain.')) return;
    try {
        const res = await fetch(`/documents/${docId}`, { method: 'DELETE' });
        const data = await res.json();
        if (data.success) {
            showToast('success', 'Document removed.');
            loadDocuments();
            loadStats();
        } else {
            showToast('error', data.message || 'Deletion failed.');
        }
    } catch (e) {
        showToast('error', 'Error deleting document.');
    }
}

// ── Stats ─────────────────────────────────────────────────────────────────────
async function loadStats() {
    try {
        const res = await fetch('/stats');
        const data = await res.json();
        if (!data.success) return;
        document.getElementById('statDocs').textContent = data.stats.total_docs;
        document.getElementById('statQs').textContent = data.stats.total_questions;
        document.getElementById('statAs').textContent = data.stats.total_answers;
    } catch (e) {}
}

// ── Ask Question ──────────────────────────────────────────────────────────────
function handleKey(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        askQuestion();
    }
}

function fillQuestion(el) {
    document.getElementById('questionInput').value = el.textContent.trim();
    document.getElementById('questionInput').focus();
}

async function askQuestion() {
    const input = document.getElementById('questionInput');
    const question = input.value.trim();
    if (!question) return;

    // Hide welcome card
    const wc = document.getElementById('welcomeCard');
    if (wc) wc.style.display = 'none';

    // Render user bubble
    appendUserMessage(question);
    input.value = '';

    // Show typing indicator
    const typingId = appendTyping();

    // Disable button
    const btn = document.getElementById('askBtn');
    btn.disabled = true;

    try {
        const res = await fetch('/ask', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ question })
        });
        const data = await res.json();
        removeTyping(typingId);

        if (!data.success) {
            appendBotError(data.message || 'Something went wrong.');
        } else {
            appendVerdictCard(data);
            loadStats();
        }
    } catch (e) {
        removeTyping(typingId);
        appendBotError('Network error: ' + e.message);
    } finally {
        btn.disabled = false;
    }
}

// ── Message Rendering ─────────────────────────────────────────────────────────
function appendUserMessage(text) {
    const container = document.getElementById('messages');
    const row = document.createElement('div');
    row.className = 'msg-row user';
    row.innerHTML = `<div class="msg-bubble">${escHtml(text)}</div>`;
    container.appendChild(row);
    scrollToBottom();
}

function appendTyping() {
    const container = document.getElementById('messages');
    const id = 'typing-' + Date.now();
    const row = document.createElement('div');
    row.className = 'msg-row bot';
    row.id = id;
    row.innerHTML = `
        <div class="typing-indicator">
            <span></span><span></span><span></span>
        </div>`;
    container.appendChild(row);
    scrollToBottom();
    return id;
}

function removeTyping(id) {
    const el = document.getElementById(id);
    if (el) el.remove();
}

function appendBotError(msg) {
    const container = document.getElementById('messages');
    const row = document.createElement('div');
    row.className = 'msg-row bot';
    row.innerHTML = `
        <div class="msg-bubble" style="border-left:4px solid var(--red);color:var(--red);">
            ⚠️ ${escHtml(msg)}
        </div>`;
    container.appendChild(row);
    scrollToBottom();
}

function appendVerdictCard(data) {
    const container = document.getElementById('messages');
    const row = document.createElement('div');
    row.className = 'msg-row bot';

    const verdict = data.verdict || 'UNCLEAR';
    const verdictIcon = { APPROVED: '✅', DENIED: '❌', PARTIAL: '⚠️', UNCLEAR: '❓' }[verdict] || '❓';
    const confPct = Math.round((data.confidence || 0) * 100);

    const clauseHtml = data.clause_reference
        ? `<div>
               <div class="clause-label">📜 Policy Clause / Reference</div>
               <div class="clause-box">${escHtml(data.clause_reference)}</div>
           </div>`
        : '';

    const sourceHtml = (data.source_doc && data.source_doc !== 'N/A')
        ? `<span class="source-badge">📄 ${escHtml(data.source_doc)}${data.source_page ? ' · Page ' + data.source_page : ''}</span>`
        : '';

    const cacheHtml = data.from_cache
        ? `<span class="cache-badge">⚡ From your query cache</span>`
        : '';

    row.innerHTML = `
        <div class="verdict-card">
            <div class="verdict-header ${verdict}">
                <span class="verdict-icon">${verdictIcon}</span>
                <span class="verdict-label">${verdict}</span>
                <span class="verdict-conf">Confidence: ${confPct}%</span>
            </div>
            <div class="verdict-body">
                ${clauseHtml}
                <div class="explanation-text">${escHtml(data.answer || '')}</div>
                <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:4px">
                    ${sourceHtml}
                    ${cacheHtml}
                </div>
            </div>
        </div>`;

    container.appendChild(row);
    scrollToBottom();
}

// ── History Modal ─────────────────────────────────────────────────────────────
async function toggleHistory() {
    const modal = document.getElementById('historyModal');
    modal.classList.add('open');
    await loadHistory();
}

function closeHistory(e) {
    if (e && e.target !== document.getElementById('historyModal')) return;
    document.getElementById('historyModal').classList.remove('open');
}

document.addEventListener('keydown', e => {
    if (e.key === 'Escape') document.getElementById('historyModal').classList.remove('open');
});

async function loadHistory() {
    const list = document.getElementById('historyList');
    list.innerHTML = '<p class="empty-msg">Loading…</p>';
    try {
        const res = await fetch('/history');
        const data = await res.json();
        allHistory = data.history || [];
        renderHistory(allHistory);
    } catch (e) {
        list.innerHTML = '<p class="empty-msg">Failed to load history.</p>';
    }
}

function filterHistory() {
    const q = document.getElementById('histSearch').value.toLowerCase();
    const filtered = allHistory.filter(h =>
        h.question.toLowerCase().includes(q) ||
        (h.answer || '').toLowerCase().includes(q) ||
        (h.verdict || '').toLowerCase().includes(q)
    );
    renderHistory(filtered);
}

function renderHistory(items) {
    const list = document.getElementById('historyList');
    if (!items.length) {
        list.innerHTML = '<p class="empty-msg">No queries yet.</p>';
        return;
    }
    list.innerHTML = items.map(h => {
        const verdict = h.verdict || 'UNCLEAR';
        const confPct = Math.round((h.confidence || 0) * 100);
        const clauseHtml = h.clause_reference
            ? `<div class="clause-box" style="margin-top:8px;font-size:12px;">${escHtml(h.clause_reference)}</div>` : '';
        return `
            <div class="hist-item">
                <div class="hist-question">💬 ${escHtml(h.question)}</div>
                <div class="hist-answer">
                    <span class="hist-verdict ${verdict}">${verdict} (${confPct}%)</span>
                    <div>${escHtml(h.answer || '')}</div>
                    ${clauseHtml}
                    ${h.source_doc && h.source_doc !== 'N/A'
                        ? `<div style="margin-top:8px"><span class="source-badge">📄 ${escHtml(h.source_doc)}${h.source_page ? ' · P' + h.source_page : ''}</span></div>`
                        : ''}
                </div>
            </div>`;
    }).join('');
}

// ── Export ────────────────────────────────────────────────────────────────────
function exportToExcel() {
    window.open('/export', '_blank');
}

// ── Logout ────────────────────────────────────────────────────────────────────
function logout() {
    window.location.href = '/logout';
}

// ── Utilities ─────────────────────────────────────────────────────────────────
function scrollToBottom() {
    const m = document.getElementById('messages');
    m.scrollTop = m.scrollHeight;
}

function escHtml(str) {
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/\n/g, '<br>');
}

function formatDate(dateStr) {
    if (!dateStr) return '';
    const d = new Date(dateStr);
    if (isNaN(d)) return dateStr;
    return d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
}

function showToast(type, msg) {
    // Remove any existing toast
    document.querySelectorAll('.toast').forEach(t => t.remove());

    const toast = document.createElement('div');
    const bg = type === 'success' ? '#166534' : type === 'error' ? '#991b1b' : '#1a3c5e';
    const icon = type === 'success' ? '✅' : type === 'error' ? '❌' : 'ℹ️';

    toast.className = 'toast';
    toast.style.cssText = `
        position:fixed; bottom:28px; right:28px; z-index:9999;
        background:${bg}; color:#fff; padding:14px 20px; border-radius:12px;
        font-size:14px; display:flex; align-items:center; gap:10px;
        box-shadow:0 8px 32px rgba(0,0,0,.25); max-width:360px;
        animation:slideIn .3s ease;
    `;
    toast.innerHTML = `<span>${icon}</span><span>${escHtml(msg)}</span>`;

    // Inject animation if not already present
    if (!document.getElementById('toast-style')) {
        const s = document.createElement('style');
        s.id = 'toast-style';
        s.textContent = '@keyframes slideIn{from{transform:translateX(120%);opacity:0}to{transform:translateX(0);opacity:1}}';
        document.head.appendChild(s);
    }

    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 4000);
}