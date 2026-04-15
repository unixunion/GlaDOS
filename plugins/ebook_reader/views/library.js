// Ebook reader library view — book grid with filter + semantic search.
GlaDOS.views.ebook_library = {
    _allBooks: [],
    _localFilter: '',

    renderCard(container) {
        const d = GlaDOS.dashboardData.ebook_reader;
        // First render: no data yet — request a state push from the plugin.
        // The dashboard auto re-renders when new dashboard_data arrives.
        if (!d) {
            socket.emit('ebook_library_action', { action: 'get_state' });
        }
        const data = d || {};
        const total = data.total_books || 0;
        const cur = data.current_book;

        let body;
        if (!d) {
            // Not yet loaded — show a placeholder while we wait for the
            // plugin to respond to the get_state request above.
            body = `<div class="el-card-empty">Loading library...</div>`;
        } else if (cur) {
            const chapter = (cur.chapter_index || 0) + 1;
            const chapters = cur.chapter_count || 0;
            const progress = chapters > 0 ? Math.round((chapter / chapters) * 100) : 0;
            body = `
                <div class="el-card-current">
                    <div class="el-card-current-title">${GlaDOS.esc(cur.title || '(untitled)')}</div>
                    <div class="el-card-current-author">${GlaDOS.esc(cur.author || '')}</div>
                    <div class="el-card-current-progress">
                        Chapter ${chapter} of ${chapters}
                        <div class="el-progress-bar"><div class="el-progress-fill" style="width:${progress}%"></div></div>
                    </div>
                </div>
                <div style="display:flex;gap:6px;margin-top:8px">
                    <button class="tq-btn" style="flex:1" onclick="event.stopPropagation();socket.emit('ebook_reader_action',{action:'show'})"><i class="icon-book-open"></i> Resume</button>
                    <button class="tq-btn" style="flex:1" onclick="event.stopPropagation();socket.emit('ebook_library_action',{action:'show'})">Library</button>
                </div>
            `;
        } else if (total > 0) {
            body = `<div class="el-card-empty">${total} books in the library</div>
                <button class="tq-btn" style="width:100%;margin-top:8px"
                    onclick="event.stopPropagation();socket.emit('ebook_library_action',{action:'show'})">
                    Browse Library
                </button>`;
        } else {
            body = `<div class="el-card-empty">No books ingested yet.<br>
                Run <code>tools/ingest_ebooks_qdrant.py</code></div>`;
        }

        container.innerHTML = `
            <div class="dash-card-header">
                <span class="dash-card-icon"><i class="icon-book-open"></i></span> Reader
                ${total > 0 ? `<span class="dash-card-badge">${total}</span>` : ''}
            </div>
            <div class="dash-card-body" onclick="socket.emit('ebook_library_action',{action:'show'})" style="cursor:pointer">
                ${body}
            </div>
        `;
    },

    render(container, data) {
        this._allBooks = data.books || [];
        const total = data.total_books || this._allBooks.length;
        const isFiltered = !!data.is_filtered;
        const searchQuery = data.search_query || '';
        const recently = data.recently_opened || [];

        let html = `<div class="view-title">Library
            <span class="el-total">${this._allBooks.length}${isFiltered ? ` of ${total}` : ''} books</span>
        </div>`;

        // Toolbar: client-side filter + semantic search
        html += `<div class="el-toolbar">
            <input id="el-filter" class="el-input" type="text" placeholder="Filter title or author..."
                   value="${GlaDOS.esc(this._localFilter)}"
                   oninput="GlaDOS.views.ebook_library._onFilterInput(this.value)">
            <input id="el-semantic" class="el-input" type="text" placeholder="Semantic search e.g. 'fantasy with dragons'..."
                   value="${GlaDOS.esc(searchQuery)}"
                   onkeydown="if(event.key==='Enter')GlaDOS.views.ebook_library._onSemanticSearch(this.value)">
            <button class="btn-primary" onclick="GlaDOS.views.ebook_library._onSemanticSearch(document.getElementById('el-semantic').value)">Search</button>
        </div>`;

        if (isFiltered) {
            html += `<div class="el-search-banner">
                Semantic results for: <strong>${GlaDOS.esc(searchQuery)}</strong>
                <button class="btn-accent-outline" onclick="socket.emit('ebook_library_action',{action:'clear_search'})">Clear</button>
            </div>`;
        }

        // Recently opened (only when not filtering, and only if non-empty)
        if (!isFiltered && recently.length > 0) {
            const recentBooks = recently
                .map(r => this._allBooks.find(b => b.book_id === r.book_id))
                .filter(Boolean);
            if (recentBooks.length > 0) {
                html += '<div class="el-section-title">Recently Opened</div>';
                html += '<div class="el-recent-row">';
                recentBooks.forEach(b => { html += this._renderCard(b, true); });
                html += '</div>';
            }
        }

        // Main grid (will be filtered live by _applyLocalFilter)
        html += '<div class="el-section-title">All Books</div>';
        html += '<div id="el-grid" class="el-grid">';
        this._allBooks.forEach(b => { html += this._renderCard(b, false); });
        html += '</div>';

        if (this._allBooks.length === 0) {
            html += `<div class="empty-state">No books in the library yet. Run
                <code>tools/ingest_ebooks_qdrant.py</code> to ingest a Gutenberg ZIM.</div>`;
        }

        container.innerHTML = html;
        // Re-apply any active client-side filter after re-rendering
        if (this._localFilter) {
            this._applyLocalFilter();
        }
    },

    _renderCard(b, compact) {
        const safeId = GlaDOS.esc(b.book_id);
        const title = GlaDOS.esc(b.title || '(untitled)');
        const author = GlaDOS.esc(b.author || 'Unknown');
        const lcc = b.lcc ? `<span class="el-lcc">LCC ${GlaDOS.esc(b.lcc)}</span>` : '';
        const wc = b.word_count
            ? `<span class="el-words">${this._fmtWordCount(b.word_count)}</span>`
            : '';
        const subjects = (b.subjects || []).slice(0, 3).map(s =>
            `<span class="el-subject">${GlaDOS.esc(s)}</span>`).join('');
        const cls = compact ? 'el-card el-card-compact' : 'el-card';
        return `<div class="${cls}" data-title="${title.toLowerCase()}" data-author="${author.toLowerCase()}"
            onclick="socket.emit('ebook_library_action',{action:'open',book_id:'${safeId}'})">
            <div class="el-card-cover"><i class="icon-book-open"></i></div>
            <div class="el-card-body">
                <div class="el-card-title">${title}</div>
                <div class="el-card-author">${author}</div>
                <div class="el-card-meta">${lcc} ${wc}</div>
                ${subjects ? `<div class="el-card-subjects">${subjects}</div>` : ''}
            </div>
        </div>`;
    },

    _fmtWordCount(n) {
        if (n >= 1000) return `${Math.round(n / 1000)}k words`;
        return `${n} words`;
    },

    _onFilterInput(value) {
        this._localFilter = (value || '').toLowerCase().trim();
        this._applyLocalFilter();
    },

    _applyLocalFilter() {
        const q = this._localFilter;
        const cards = document.querySelectorAll('#el-grid .el-card');
        cards.forEach(card => {
            if (!q) {
                card.style.display = '';
                return;
            }
            const title = card.getAttribute('data-title') || '';
            const author = card.getAttribute('data-author') || '';
            card.style.display = (title.includes(q) || author.includes(q)) ? '' : 'none';
        });
    },

    _onSemanticSearch(query) {
        const q = (query || '').trim();
        if (!q) {
            socket.emit('ebook_library_action', { action: 'clear_search' });
            return;
        }
        socket.emit('ebook_library_action', { action: 'search_semantic', query: q });
    }
};
