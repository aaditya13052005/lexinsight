// static/case.js
document.addEventListener("DOMContentLoaded", async () => {
    try {
        const caseId = document.getElementById("case-id").value;
        const fileList = document.getElementById("file-list");
        const hitsList = document.getElementById("hits-list");
        const semanticResults = document.getElementById("semantic-results");
        const scoutResults = document.getElementById("scout-results");
        const pdfContainer = document.getElementById("pdf-container");
        const searchInput = document.getElementById("search-input");
        const searchBtn = document.getElementById("search-btn");
        const semanticInput = document.getElementById("semantic-input");
        const semanticBtn = document.getElementById("semantic-btn");
        const uploadForm = document.getElementById("upload-form");
        const scoutInput = document.getElementById("scout-input");
        const scoutBtn = document.getElementById("scout-btn");

        let fileUrlMap = {};

        // -------------------
        // Load all files
        // -------------------
        async function loadFiles() {
            try {
                const res = await fetch(`/files_by_case/${caseId}`);
                const data = await res.json();
                if (data.files) {
                    fileList.innerHTML = "";
                    data.files.forEach(f => {
                        const li = document.createElement("li");
                        li.textContent = f.file_name;
                        li.dataset.url = f.file_url;
                        li.dataset.id = f.id || "";
                        fileList.appendChild(li);
                        fileUrlMap[f.file_name] = f.file_url;
                        if (f.id) fileUrlMap[f.id] = f.file_url;
                    });
                }
            } catch (err) {
                console.error("Error loading files:", err);
            }
        }
        await loadFiles();

        // -------------------
        // TAB SWITCHING
        // -------------------
        const tabButtons = document.querySelectorAll('.tab-btn');
        const panes = document.querySelectorAll('.tab-content .pane');

        tabButtons.forEach(btn => {
            btn.addEventListener('click', () => {
                tabButtons.forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                const target = btn.dataset.tab;
                panes.forEach(p => {
                    p.classList.toggle('active', p.id === target);
                });
            });
        });

        // -------------------
        // SUMMARIZATION (keeps existing behavior)
        // -------------------
        const summarizeForm = document.getElementById("summarize-form");
        let summaryBox = document.getElementById("summary-result");
        if (!summaryBox) summaryBox = document.getElementById("summary-output");

        if (summarizeForm && summaryBox) {
            summarizeForm.addEventListener("submit", async (e) => {
                e.preventDefault();
                summaryBox.textContent = "Summarizing... ⏳";

                const fileInput = summarizeForm.querySelector('input[type="file"]');
                const file = fileInput.files[0];
                if (!file) {
                    summaryBox.textContent = "Please upload a PDF first.";
                    return;
                }

                const formData = new FormData();
                formData.append("pdf", file);

                try {
                    const res = await fetch(`/summarize_pdf/${caseId}`, {
                        method: "POST",
                        body: formData
                    });
                    const data = await res.json();
                    if (data.summary) {
                        summaryBox.textContent = data.summary;
                    } else {
                        summaryBox.textContent = data.error || "Failed to summarize.";
                    }
                } catch (err) {
                    console.error(err);
                    summaryBox.textContent = "Error generating summary.";
                }
            });
        }

        // -------------------
        // UPLOAD PDF
        // -------------------
        if (uploadForm) {
            uploadForm.addEventListener("submit", async (e) => {
                e.preventDefault();
                const formData = new FormData(uploadForm);

                try {
                    const response = await fetch(`/upload_file/${caseId}`, {
                        method: "POST",
                        body: formData
                    });
                    const data = await response.json();
                    if (response.ok) {
                        const li = document.createElement("li");
                        li.textContent = data.filename;
                        li.dataset.url = data.file_url;
                        fileList.appendChild(li);
                        fileUrlMap[data.filename] = data.file_url;
                        openPdf(data.file_url);
                    } else {
                        alert("Upload failed: " + (data.error || "Unknown error"));
                    }
                } catch (err) {
                    console.error("Error uploading:", err);
                    alert("Upload error: " + err.message);
                }
            });
        }

        // -------------------
        // OPEN PDF
        // -------------------
        function openPdf(url, pageNumber = 1, searchTerm = "") {
            if (!url) return;
            const encodedUrl = encodeURIComponent(url);
            const timestamp = Date.now();
            let viewerUrl = `https://mozilla.github.io/pdf.js/web/viewer.html?file=${encodedUrl}#page=${pageNumber}`;
            if (searchTerm) viewerUrl += `&search=${encodeURIComponent(searchTerm)}&highlightAll=true`;
            pdfContainer.innerHTML = `<iframe key="${timestamp}" src="${viewerUrl}" width="100%" height="800px" style="border:none;"></iframe>`;
        }

        fileList.addEventListener("click", (e) => {
            if (e.target && e.target.tagName === "LI") openPdf(e.target.dataset.url);
        });
        hitsList.addEventListener("click", (e) => {
            if (e.target && e.target.tagName === "LI") openPdf(e.target.dataset.url);
        });

        // -------------------
        // KEYWORD SEARCH
        // -------------------
        async function performSearch(query, endpoint, resultList) {
            if (!query) return;
            resultList.innerHTML = "<li>Loading results...</li>";

            try {
                const res = await fetch(endpoint, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ case_id: caseId, query, match_count: 5 })
                });
                const data = await res.json();
                resultList.innerHTML = "";

                if (data.hits && data.hits.length > 0) {
                    data.hits.forEach(hit => {
                        const li = document.createElement("li");
                        li.dataset.url = hit.file_url;
                        li.innerHTML = `<strong>${hit.filename}</strong><br><small>${(hit.text_snippet||"").slice(0,200)}...</small>`;
                        li.style.cursor = "pointer";
                        li.addEventListener("click", () => {
                            openPdf(hit.file_url, hit.page_number || 1, query);
                        });
                        resultList.appendChild(li);
                    });
                    // open first result automatically
                    openPdf(data.hits[0].file_url, data.hits[0].page_number || 1, query);
                } else {
                    resultList.innerHTML = "<li>No matches found</li>";
                    pdfContainer.innerHTML = "<p>No matches found</p>";
                }
            } catch (err) {
                console.error("Search error:", err);
                resultList.innerHTML = "<li>Error performing search</li>";
            }
        }

        if (searchBtn) searchBtn.addEventListener("click", () => {
            performSearch(searchInput.value.trim(), "/search_pdf", hitsList);
        });

        // -------------------
        // SEMANTIC SEARCH (FIXED)
        // -------------------
        if (semanticBtn) semanticBtn.addEventListener("click", async () => {
            const query = semanticInput.value.trim();
            if (!query) return;
            semanticResults.innerHTML = "<li>Loading results...</li>";

            try {
                // endpoint corrected to match backend
                const res = await fetch("/api/semantic_search", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ case_id: caseId, query, top_k: 5 })
                });
                const data = await res.json();
                semanticResults.innerHTML = "";

                if (data.results && data.results.length > 0) {
                    data.results.forEach(result => {
                        const li = document.createElement("li");
                        li.dataset.url = result.file_url;
                        li.dataset.page = result.page_number || 1;
                        li.dataset.snippet = result.text_snippet || "";
                        li.innerHTML = `
                            <b>${result.filename}</b> — Page ${result.page_number || 1}<br>
                            <small>${(result.text_snippet || "").slice(0, 300)}...</small>
                        `;
                        li.style.cursor = "pointer";
                        li.addEventListener("click", () => {
                            openPdf(result.file_url, result.page_number || 1, result.text_snippet || "");
                        });
                        semanticResults.appendChild(li);
                    });
                } else {
                    semanticResults.innerHTML = "<li>No semantic matches found</li>";
                }
            } catch (err) {
                console.error("Semantic search error:", err);
                semanticResults.innerHTML = "<li>Error performing semantic search</li>";
            }
        });

        // -------------------
        // SCOUTING SEARCH
        // -------------------
        if (scoutBtn) scoutBtn.addEventListener("click", async () => {
            const topic = scoutInput.value.trim();
            if (!topic) return;

            scoutResults.innerHTML = "<li>Loading articles...</li>";

            try {
                const res = await fetch("/api/scout", {
                    method: "POST",
                    headers: {"Content-Type":"application/json"},
                    body: JSON.stringify({ topic })
                });
                const data = await res.json();
                scoutResults.innerHTML = "";

                if (data.scouting_results && data.scouting_results.length > 0) {
                    data.scouting_results.forEach(item => {
                        const li = document.createElement("li");
                        li.classList.add("article-card");
                        li.innerHTML = `<h4><a href="${item.link}" target="_blank">${item.title}</a></h4>
                                        <p><b>Authors:</b> ${item.authors}</p>
                                        <p><b>Published:</b> ${item.published}</p>
                                        <p>${item.summary}</p>`;
                        scoutResults.appendChild(li);
                    });
                } else {
                    scoutResults.innerHTML = "<li>No articles found</li>";
                }
            } catch (err) {
                console.error("Scouting error:", err);
                scoutResults.innerHTML = "<li>Error fetching articles</li>";
            }
        });

        // -------------------
        // FLOATING AI CHAT ICON — FIXED & DEBUGGED
        // -------------------
        // Ensure only one toggle method used: 'visible'
        const chatIcon = document.getElementById("ai-chat-icon");
        const chatBox = document.getElementById("ai-chat-box");
        const aiInput = document.getElementById("ai-input");
        const aiSendBtn = document.getElementById("ai-send-btn");
        const aiResponse = document.getElementById("ai-response");

        if (!chatIcon || !chatBox || !aiSendBtn) {
            console.warn("AI chat elements missing - chat may not work. chatIcon, chatBox or aiSendBtn not found.");
        } else {
            // toggle visibility
            chatIcon.addEventListener("click", () => {
                try {
                    chatBox.classList.toggle("visible");
                    // optional: focus input when opened
                    if (chatBox.classList.contains("visible")) {
                        setTimeout(() => aiInput?.focus(), 100);
                    }
                } catch (e) {
                    console.error("Toggle chat error:", e);
                }
            });

            // Add ENTER key support in textarea
            aiInput.addEventListener("keydown", (e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    aiSendBtn.click();
                }
            });

            // send handler
            aiSendBtn.addEventListener("click", async () => {
                try {
                    const query = aiInput.value.trim();
                    console.log("[AI CHAT] send clicked. query:", query);
                    if (!query) {
                        // small UX: flash message
                        aiResponse.innerHTML += `<div style="opacity:0.8;color:#ffd700">Please type a question.</div>`;
                        aiResponse.scrollTop = aiResponse.scrollHeight;
                        return;
                    }

                    // show user message
                    aiResponse.innerHTML += `<div><b>You:</b> ${escapeHtml(query)}</div>`;
                    aiInput.value = "";
                    aiResponse.scrollTop = aiResponse.scrollHeight;

                    // call backend ai_chat endpoint (assumes route exists)
                    const res = await fetch(`/ai_chat/${caseId}`, {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ query })
                    });

                    const data = await res.json();
                    if (data.answer) {
                        aiResponse.innerHTML += `<div><b>LexInsight:</b> ${escapeHtml(data.answer)}</div>`;
                    } else {
                        console.error("AI response error payload:", data);
                        aiResponse.innerHTML += `<div style="color:red;">Error: ${escapeHtml(data.error || JSON.stringify(data))}</div>`;
                    }
                    aiResponse.scrollTop = aiResponse.scrollHeight;
                } catch (err) {
                    console.error("AI chat error:", err);
                    aiResponse.innerHTML += `<div style="color:red;">Error sending message</div>`;
                    aiResponse.scrollTop = aiResponse.scrollHeight;
                }
            });
        }

        // small helper escape to avoid html injection in chat box
        function escapeHtml(str) {
            if (!str) return "";
            return String(str).replace(/[&<>"'`=\/]/g, function (s) {
                return ({
                    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;',
                    "'": '&#39;', '/': '&#x2F;', '`': '&#x60;', '=': '&#x3D;'
                })[s];
            });
        }

    } catch (outerErr) {
        console.error("Fatal error in case.js DOMContentLoaded handler:", outerErr);
    }
});
