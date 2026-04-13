// ============================================
// Constitutional Convention Simulator — Frontend
// ============================================

const state = {
    delegates: [],
    selectedIds: new Set(),
    delegateCount: 5,
    debateRounds: 3,
    prompt: "",
    referenceDocument: "",  // uploaded document text
    history: [],        // {delegate_id, name, text}
    conventionRunning: false,
    stopRequested: false,
    finalDocument: "",
    activeCategory: "all",
    models: { anthropic: {}, openai: {}, google: {} },
    delegateSourceText: "",  // source text for custom delegate creation
};

// Delegate color assignment
const DELEGATE_COLORS = [
    "#2c4a6e", "#6b3a5c", "#3a6b4a", "#8b5e2f",
    "#5c3a6b", "#2e6b6b", "#6b2e2e", "#4a5c2e",
    "#2e4a6b", "#6b4a2e", "#3a5c6b", "#5c6b3a",
];

function getDelegateColor(delegateId) {
    const ids = Array.from(state.selectedIds);
    const idx = ids.indexOf(delegateId);
    return DELEGATE_COLORS[idx % DELEGATE_COLORS.length];
}

// ---- DOM refs ----

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

const els = {
    screenSetup: $("#screen-setup"),
    screenConvention: $("#screen-convention"),
    screenResults: $("#screen-results"),
    providerSelect: $("#provider-select"),
    modelSelect: $("#model-select"),
    apiKey: $("#api-key"),
    prompt: $("#convention-prompt"),
    delegateCount: $("#delegate-count"),
    debateRounds: $("#debate-rounds"),
    selectionCount: $("#selection-count"),
    selectionMax: $("#selection-max"),
    delegateGrid: $("#delegate-grid"),
    categoryFilters: $("#category-filters"),
    btnAutoSelect: $("#btn-auto-select"),
    btnClearSelection: $("#btn-clear-selection"),
    btnStart: $("#btn-start"),
    btnStop: $("#btn-stop"),
    conventionTopic: $("#convention-topic"),
    turnIndicator: $("#turn-indicator"),
    transcript: $("#debate-transcript"),
    typingIndicator: $("#typing-indicator"),
    typingName: $("#typing-name"),
    emergingDocument: $("#emerging-document"),
    finalDocument: $("#final-document"),
    resultsTranscript: $("#results-transcript"),
    btnViewDocument: $("#btn-view-document"),
    btnViewTranscript: $("#btn-view-transcript"),
    resultsTopic: $("#results-topic"),
    btnDownloadTranscript: $("#btn-download-transcript"),
    btnDownloadDocument: $("#btn-download-document"),
    btnNewConvention: $("#btn-new-convention"),
    loadingOverlay: $("#loading-overlay"),
    loadingMessage: $("#loading-message"),
    // Document upload
    docUploadArea: $("#doc-upload-area"),
    docFileInput: $("#doc-file-input"),
    docUploadPrompt: $("#doc-upload-prompt"),
    docUploadStatus: $("#doc-upload-status"),
    docFilename: $("#doc-filename"),
    docCharCount: $("#doc-char-count"),
    btnRemoveDoc: $("#btn-remove-doc"),
    // Custom delegate
    customDelegateName: $("#custom-delegate-name"),
    btnSourceFile: $("#btn-source-file"),
    btnSourcePaste: $("#btn-source-paste"),
    sourceFileArea: $("#source-file-area"),
    sourcePasteArea: $("#source-paste-area"),
    delegateSourceFile: $("#delegate-source-file"),
    delegateSourcePrompt: $("#delegate-source-prompt"),
    delegateSourceStatus: $("#delegate-source-status"),
    delegateSourceFilename: $("#delegate-source-filename"),
    btnRemoveDelegateSource: $("#btn-remove-delegate-source"),
    delegateSourceText: $("#delegate-source-text"),
    btnCreateDelegate: $("#btn-create-delegate"),
    customDelegatesList: $("#custom-delegates-list"),
};

// ---- Provider / Model management ----

function getProviderConfig() {
    return {
        provider: els.providerSelect.value,
        model: els.modelSelect.value,
        api_key: els.apiKey.value.trim(),
    };
}

function populateModelSelect() {
    const provider = els.providerSelect.value;
    const models = state.models[provider] || {};
    els.modelSelect.innerHTML = Object.entries(models)
        .map(([id, label]) => `<option value="${id}">${label}</option>`)
        .join("");

    // Update placeholder hint for api key
    const placeholders = { anthropic: "sk-ant-...", openai: "sk-...", google: "AIza..." };
    els.apiKey.placeholder = placeholders[provider] || "API key...";
}

els.providerSelect.addEventListener("change", populateModelSelect);

async function loadModels() {
    try {
        state.models = await api("/models");
        populateModelSelect();
    } catch (e) {
        // Fallback defaults
        state.models = {
            anthropic: { "claude-sonnet-4-20250514": "Claude Sonnet 4" },
            openai: { "gpt-4o": "GPT-4o" },
            google: { "gemini-2.5-flash": "Gemini 2.5 Flash" },
        };
        populateModelSelect();
    }
}

// ---- Screen management ----

function showScreen(name) {
    $$(".screen").forEach((s) => s.classList.remove("active"));
    $(`#screen-${name}`).classList.add("active");
}

function showLoading(msg) {
    els.loadingMessage.textContent = msg;
    els.loadingOverlay.classList.remove("hidden");
}

function hideLoading() {
    els.loadingOverlay.classList.add("hidden");
}

// ---- API helpers ----

async function api(endpoint, data, retries = 2) {
    for (let attempt = 0; attempt <= retries; attempt++) {
        try {
            const res = await fetch(`/api${endpoint}`, {
                method: data ? "POST" : "GET",
                headers: data ? { "Content-Type": "application/json" } : {},
                body: data ? JSON.stringify(data) : undefined,
            });
            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                const msg = err.error || `HTTP ${res.status}: ${res.statusText}`;
                if (attempt < retries && (res.status >= 500 || res.status === 429)) {
                    await new Promise(r => setTimeout(r, 1000 * (attempt + 1)));
                    continue;
                }
                throw new Error(msg);
            }
            return res.json();
        } catch (e) {
            if (attempt < retries && !e.message.startsWith("HTTP")) {
                await new Promise(r => setTimeout(r, 1000 * (attempt + 1)));
                continue;
            }
            throw e;
        }
    }
}

// Helper: inject provider config into POST data
function withProvider(data) {
    return { ...data, ...getProviderConfig() };
}

// ---- Delegate list ----

async function loadDelegates() {
    showLoading("Loading delegates...");
    try {
        state.delegates = await api("/delegates");
        renderCategoryFilters();
        renderDelegateGrid();
    } catch (e) {
        alert("Failed to load delegates: " + e.message);
    } finally {
        hideLoading();
    }
}

function renderCategoryFilters() {
    const categories = [...new Set(state.delegates.map((d) => d.category))].sort();
    els.categoryFilters.innerHTML =
        `<button class="category-btn active" data-cat="all">All</button>` +
        categories
            .map((c) => `<button class="category-btn" data-cat="${c}">${c}</button>`)
            .join("");

    els.categoryFilters.addEventListener("click", (e) => {
        const btn = e.target.closest(".category-btn");
        if (!btn) return;
        state.activeCategory = btn.dataset.cat;
        $$(".category-btn").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        renderDelegateGrid();
    });
}

function renderDelegateGrid() {
    const filtered =
        state.activeCategory === "all"
            ? state.delegates
            : state.delegates.filter((d) => d.category === state.activeCategory);

    els.delegateGrid.innerHTML = filtered
        .map((d) => {
            const selected = state.selectedIds.has(d.id);
            const atMax = state.selectedIds.size >= state.delegateCount && !selected;
            return `
                <div class="delegate-card ${selected ? "selected" : ""} ${atMax ? "disabled" : ""} ${d.custom ? "custom-card" : ""}"
                     data-id="${d.id}">
                    <div class="check-mark">&#10003;</div>
                    ${d.custom ? '<div class="custom-badge">Custom</div>' : ""}
                    <div class="card-name">${d.name}</div>
                    <div class="card-category">${d.category}</div>
                    <div class="card-bio">${d.bio}</div>
                    ${d.leanings ? `<div class="card-leanings">${d.leanings}</div>` : ""}
                </div>
            `;
        })
        .join("");
}

function updateSelectionUI() {
    els.selectionCount.textContent = state.selectedIds.size;
    els.selectionMax.textContent = state.delegateCount;
    updateStartButton();
    renderDelegateGrid();
}

function updateStartButton() {
    els.btnStart.disabled =
        state.selectedIds.size === 0 ||
        !els.prompt.value.trim() ||
        !els.apiKey.value.trim();
}

// ---- Delegate count controls ----

$("#count-minus").addEventListener("click", () => {
    if (state.delegateCount > 2) {
        state.delegateCount--;
        els.delegateCount.textContent = state.delegateCount;
        while (state.selectedIds.size > state.delegateCount) {
            const last = Array.from(state.selectedIds).pop();
            state.selectedIds.delete(last);
        }
        updateSelectionUI();
    }
});

$("#count-plus").addEventListener("click", () => {
    state.delegateCount++;
    els.delegateCount.textContent = state.delegateCount;
    updateSelectionUI();
});

// ---- Debate rounds controls ----

$("#rounds-minus").addEventListener("click", () => {
    if (state.debateRounds > 1) {
        state.debateRounds--;
        els.debateRounds.textContent = state.debateRounds;
    }
});

$("#rounds-plus").addEventListener("click", () => {
    if (state.debateRounds < 10) {
        state.debateRounds++;
        els.debateRounds.textContent = state.debateRounds;
    }
});

// ---- Delegate grid click ----

els.delegateGrid.addEventListener("click", (e) => {
    const card = e.target.closest(".delegate-card");
    if (!card) return;
    const id = card.dataset.id;

    if (state.selectedIds.has(id)) {
        state.selectedIds.delete(id);
    } else if (state.selectedIds.size < state.delegateCount) {
        state.selectedIds.add(id);
    }
    updateSelectionUI();
});

// ---- Auto-select ----

els.btnAutoSelect.addEventListener("click", async () => {
    const prompt = els.prompt.value.trim();
    if (!prompt) {
        alert("Please enter a topic first so delegates can be selected based on it.");
        return;
    }
    if (!els.apiKey.value.trim()) {
        alert("Please enter your API key first.");
        return;
    }

    showLoading("Analyzing topic and selecting delegates...");
    try {
        const result = await api("/auto-select", withProvider({
            prompt,
            count: state.delegateCount,
        }));
        state.selectedIds = new Set(result.selected);
        updateSelectionUI();
    } catch (e) {
        alert("Auto-select failed: " + e.message);
    } finally {
        hideLoading();
    }
});

// ---- Clear selection ----

els.btnClearSelection.addEventListener("click", () => {
    state.selectedIds.clear();
    updateSelectionUI();
});

// ---- Input change handlers ----

els.prompt.addEventListener("input", updateStartButton);
els.apiKey.addEventListener("input", updateStartButton);

// ---- Simple markdown to HTML ----

function mdToHtml(text) {
    let html = text
        // Escape HTML
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        // Bold
        .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
        // Italic
        .replace(/\*(.+?)\*/g, "<em>$1</em>")
        // Headers
        .replace(/^### (.+)$/gm, "<h3>$1</h3>")
        .replace(/^## (.+)$/gm, "<h2>$1</h2>")
        .replace(/^# (.+)$/gm, "<h1>$1</h1>")
        // Blockquotes
        .replace(/^> (.+)$/gm, "<blockquote>$1</blockquote>")
        // Horizontal rules
        .replace(/^---$/gm, "<hr>");

    // Process lists (simple approach)
    html = html.replace(/^(\d+)\. (.+)$/gm, "<li>$2</li>");
    html = html.replace(/^[-*] (.+)$/gm, "<li>$1</li>");

    // Wrap consecutive <li> in <ul>
    html = html.replace(/((?:<li>.*<\/li>\n?)+)/g, "<ul>$1</ul>");

    // Paragraphs: split on double newlines
    html = html
        .split(/\n\n+/)
        .map((block) => {
            block = block.trim();
            if (!block) return "";
            if (
                block.startsWith("<h") ||
                block.startsWith("<ul") ||
                block.startsWith("<ol") ||
                block.startsWith("<blockquote") ||
                block.startsWith("<hr")
            )
                return block;
            return `<p>${block.replace(/\n/g, "<br>")}</p>`;
        })
        .join("\n");

    return html;
}

// ---- Convention flow ----

els.btnStart.addEventListener("click", startConvention);

async function startConvention() {
    state.prompt = els.prompt.value.trim();
    state.history = [];
    state.conventionRunning = true;
    state.stopRequested = false;
    state.finalDocument = "";

    const delegateIds = Array.from(state.selectedIds);
    const totalTurns = state.debateRounds * delegateIds.length;

    // Switch to convention screen
    showScreen("convention");
    els.conventionTopic.textContent = state.prompt;
    els.transcript.innerHTML = "";
    els.emergingDocument.innerHTML =
        '<p class="placeholder-text">The working document will appear here as consensus emerges during the debate...</p>';

    let turnNumber = 0;

    // Run debate rounds
    for (let round = 0; round < state.debateRounds; round++) {
        // Shuffle delegate order per round for variety (Fisher-Yates)
        const roundOrder = [...delegateIds];
        for (let i = roundOrder.length - 1; i > 0; i--) {
            const j = Math.floor(Math.random() * (i + 1));
            [roundOrder[i], roundOrder[j]] = [roundOrder[j], roundOrder[i]];
        }

        for (const delegateId of roundOrder) {
            if (state.stopRequested) break;

            els.turnIndicator.textContent = `Turn ${turnNumber + 1} of ${totalTurns}`;

            // Show typing indicator
            const delegateName =
                state.delegates.find((d) => d.id === delegateId)?.name || delegateId;
            els.typingName.textContent = delegateName;
            els.typingIndicator.classList.remove("hidden");

            try {
                const result = await api("/debate/turn", withProvider({
                    prompt: state.prompt,
                    delegate_id: delegateId,
                    history: state.history,
                    all_delegate_ids: delegateIds,
                    turn_number: turnNumber,
                    total_turns: totalTurns,
                    reference_document: state.referenceDocument,
                }));

                state.history.push({
                    delegate_id: delegateId,
                    name: result.name,
                    text: result.text,
                });

                // Add to transcript
                appendDebateEntry(delegateId, result.name, result.text);
                turnNumber++;
            } catch (e) {
                appendSystemMessage(`Error: ${e.message}. Continuing...`);
                turnNumber++;
                continue;
            } finally {
                els.typingIndicator.classList.add("hidden");
            }

            // Update emerging document every few turns
            if (
                state.history.length >= 3 &&
                (state.history.length % Math.max(2, delegateIds.length) === 0 ||
                    turnNumber === totalTurns)
            ) {
                updateEmergingDocument();
            }
        }
        if (state.stopRequested) break;
    }

    // Convention over — generate final document
    state.conventionRunning = false;
    els.turnIndicator.textContent = "Generating final document...";
    els.typingName.textContent = "The Clerk";
    els.typingIndicator.classList.remove("hidden");

    try {
        const result = await api("/debate/document", withProvider({
            prompt: state.prompt,
            history: state.history,
            all_delegate_ids: delegateIds,
            reference_document: state.referenceDocument,
        }));
        state.finalDocument = result.document;
    } catch (e) {
        state.finalDocument = "Error generating final document: " + e.message;
    }

    els.typingIndicator.classList.add("hidden");

    // Show results
    showScreen("results");
    els.resultsTopic.textContent = state.prompt;
    els.finalDocument.innerHTML = mdToHtml(state.finalDocument);

    // Build results transcript
    let transcriptHtml = "";
    for (const entry of state.history) {
        const color = getDelegateColor(entry.delegate_id);
        transcriptHtml += `
            <div class="debate-entry">
                <div class="speaker-name" style="color: ${color}">${entry.name}</div>
                <div class="bubble" style="border-left-color: ${color}">${mdToHtml(entry.text)}</div>
            </div>`;
    }
    els.resultsTranscript.innerHTML = transcriptHtml;

    // Reset toggle to show document
    els.finalDocument.classList.remove("hidden");
    els.resultsTranscript.classList.add("hidden");
    els.btnViewDocument.classList.add("active");
    els.btnViewTranscript.classList.remove("active");
}

function appendDebateEntry(delegateId, name, text) {
    const color = getDelegateColor(delegateId);
    const entry = document.createElement("div");
    entry.className = "debate-entry";
    entry.innerHTML = `
        <div class="speaker-name" style="color: ${color}">${name}</div>
        <div class="bubble" style="border-left-color: ${color}">${mdToHtml(text)}</div>
    `;
    // Only auto-scroll if user is near the bottom (not reading earlier entries)
    const el = els.transcript;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 150;
    el.appendChild(entry);
    if (nearBottom) el.scrollTop = el.scrollHeight;
}

function appendSystemMessage(text) {
    const entry = document.createElement("div");
    entry.className = "debate-entry";
    entry.innerHTML = `
        <div class="speaker-name" style="color: var(--text-muted)">System</div>
        <div class="bubble" style="border-left-color: var(--border); font-style: italic;">${text}</div>
    `;
    els.transcript.appendChild(entry);
}

async function updateEmergingDocument() {
    try {
        const result = await api("/debate/progress-document", withProvider({
            prompt: state.prompt,
            history: state.history,
            all_delegate_ids: Array.from(state.selectedIds),
            reference_document: state.referenceDocument,
        }));
        els.emergingDocument.innerHTML = mdToHtml(result.document);
    } catch (e) {
        // Silently fail — emerging doc is a nice-to-have
    }
}

// ---- Stop early ----

els.btnStop.addEventListener("click", () => {
    if (confirm("End the debate early and proceed to generating the final document?")) {
        state.stopRequested = true;
    }
});

// ---- Results toggle ----

els.btnViewDocument.addEventListener("click", () => {
    els.finalDocument.classList.remove("hidden");
    els.resultsTranscript.classList.add("hidden");
    els.btnViewDocument.classList.add("active");
    els.btnViewTranscript.classList.remove("active");
});

els.btnViewTranscript.addEventListener("click", () => {
    els.resultsTranscript.classList.remove("hidden");
    els.finalDocument.classList.add("hidden");
    els.btnViewTranscript.classList.add("active");
    els.btnViewDocument.classList.remove("active");
});

// ---- Downloads ----

els.btnDownloadTranscript.addEventListener("click", () => {
    let text = `CONSTITUTIONAL CONVENTION TRANSCRIPT\n`;
    text += `Topic: ${state.prompt}\n`;
    text += `${"=".repeat(60)}\n\n`;

    for (const entry of state.history) {
        text += `[${entry.name}]\n${entry.text}\n\n---\n\n`;
    }

    downloadFile("convention-transcript.txt", text);
});

els.btnDownloadDocument.addEventListener("click", () => {
    let text = `CONSTITUTIONAL CONVENTION — FINAL DOCUMENT\n`;
    text += `Topic: ${state.prompt}\n`;
    text += `${"=".repeat(60)}\n\n`;
    text += state.finalDocument;

    downloadFile("convention-document.txt", text);
});

function downloadFile(filename, content) {
    const blob = new Blob([content], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}

// ---- New Convention ----

els.btnNewConvention.addEventListener("click", () => {
    showScreen("setup");
});

// ---- Reference Document Upload ----

async function uploadFile(fileInput) {
    const file = fileInput.files[0];
    if (!file) return null;

    const formData = new FormData();
    formData.append("file", file);

    const res = await fetch("/api/upload-document", {
        method: "POST",
        body: formData,
    });
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.error || "Upload failed");
    }
    return res.json();
}

els.docUploadArea.addEventListener("click", (e) => {
    if (e.target.closest("#btn-remove-doc")) return;
    if (!state.referenceDocument) els.docFileInput.click();
});

els.docUploadArea.addEventListener("dragover", (e) => {
    e.preventDefault();
    els.docUploadArea.classList.add("has-file");
});

els.docUploadArea.addEventListener("dragleave", () => {
    if (!state.referenceDocument) els.docUploadArea.classList.remove("has-file");
});

els.docUploadArea.addEventListener("drop", (e) => {
    e.preventDefault();
    if (e.dataTransfer.files.length) {
        els.docFileInput.files = e.dataTransfer.files;
        handleDocUpload();
    }
});

els.docFileInput.addEventListener("change", handleDocUpload);

async function handleDocUpload() {
    const file = els.docFileInput.files[0];
    if (!file) return;

    showLoading("Extracting text from document...");
    try {
        const result = await uploadFile(els.docFileInput);
        state.referenceDocument = result.text;
        els.docFilename.textContent = result.filename;
        els.docCharCount.textContent = `(${result.text.length.toLocaleString()} characters)`;
        els.docUploadPrompt.classList.add("hidden");
        els.docUploadStatus.classList.remove("hidden");
        els.docUploadArea.classList.add("has-file");
    } catch (e) {
        alert("Failed to upload document: " + e.message);
        els.docUploadArea.classList.remove("has-file");
    } finally {
        hideLoading();
    }
}

els.btnRemoveDoc.addEventListener("click", (e) => {
    e.stopPropagation();
    state.referenceDocument = "";
    els.docFileInput.value = "";
    els.docUploadPrompt.classList.remove("hidden");
    els.docUploadStatus.classList.add("hidden");
    els.docUploadArea.classList.remove("has-file");
});

// ---- Custom Delegate Creation ----

// Source input tabs
els.btnSourceFile.addEventListener("click", () => {
    els.btnSourceFile.classList.add("active");
    els.btnSourcePaste.classList.remove("active");
    els.sourceFileArea.classList.remove("hidden");
    els.sourcePasteArea.classList.add("hidden");
});

els.btnSourcePaste.addEventListener("click", () => {
    els.btnSourcePaste.classList.add("active");
    els.btnSourceFile.classList.remove("active");
    els.sourcePasteArea.classList.remove("hidden");
    els.sourceFileArea.classList.add("hidden");
});

// Delegate source file upload
els.delegateSourcePrompt.addEventListener("click", () => els.delegateSourceFile.click());

els.delegateSourceFile.addEventListener("change", async () => {
    const file = els.delegateSourceFile.files[0];
    if (!file) return;

    showLoading("Reading source file...");
    try {
        const result = await uploadFile(els.delegateSourceFile);
        state.delegateSourceText = result.text;
        els.delegateSourceFilename.textContent = result.filename;
        els.delegateSourcePrompt.classList.add("hidden");
        els.delegateSourceStatus.classList.remove("hidden");
    } catch (e) {
        alert("Failed to read file: " + e.message);
    } finally {
        hideLoading();
    }
});

els.btnRemoveDelegateSource.addEventListener("click", () => {
    state.delegateSourceText = "";
    els.delegateSourceFile.value = "";
    els.delegateSourcePrompt.classList.remove("hidden");
    els.delegateSourceStatus.classList.add("hidden");
});

// Create delegate button
els.btnCreateDelegate.addEventListener("click", async () => {
    const name = els.customDelegateName.value.trim();
    const isPasteMode = els.btnSourcePaste.classList.contains("active");
    const sourceText = isPasteMode
        ? els.delegateSourceText.value.trim()
        : state.delegateSourceText;

    if (!name) {
        alert("Please enter a name for the delegate.");
        return;
    }
    if (!sourceText) {
        alert("Please provide source material (upload a file or paste text).");
        return;
    }
    if (!els.apiKey.value.trim()) {
        alert("Please enter your API key first (Step 1) — it's needed to generate the delegate persona.");
        return;
    }

    showLoading(`Creating delegate persona for ${name}...`);
    try {
        const result = await api("/create-delegate", withProvider({
            name,
            source_text: sourceText,
        }));

        // Add to local delegates list and refresh grid
        state.delegates.push(result);

        // Show in custom delegates list
        const chip = document.createElement("div");
        chip.className = "custom-delegate-chip";
        chip.innerHTML = `
            <span class="chip-name">${result.name}</span>
            <span class="chip-bio">${result.bio}</span>
        `;
        els.customDelegatesList.appendChild(chip);

        // Reset the form
        els.customDelegateName.value = "";
        els.delegateSourceText.value = "";
        state.delegateSourceText = "";
        els.delegateSourceFile.value = "";
        els.delegateSourcePrompt.classList.remove("hidden");
        els.delegateSourceStatus.classList.add("hidden");

        // Refresh the delegate grid to include the new delegate
        renderCategoryFilters();
        renderDelegateGrid();

        alert(`Delegate "${name}" created successfully! You can now select them in the delegate grid.`);
    } catch (e) {
        alert("Failed to create delegate: " + e.message);
    } finally {
        hideLoading();
    }
});

// ---- Init ----

loadModels();
loadDelegates();
