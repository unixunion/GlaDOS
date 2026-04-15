// Ebook reader chapter view — paragraphs + chapter navigation + control bar.
GlaDOS.views.ebook_reader = {
    render(container, data) {
        const book = data.book || {};
        const chapter = data.chapter || { title: '', paragraphs: [] };
        const chapterIndex = data.chapter_index || 0;
        const chapterCount = data.chapter_count || 0;
        const isPlaying = !!data.is_playing;
        const paragraphIndex = data.paragraph_index || 0;
        const bookmarks = data.bookmarks || [];

        let html = `<div class="er-header">
            <button class="btn-accent-outline" onclick="socket.emit('ebook_reader_action',{action:'back_to_library'})">
                <i class="icon-arrow-left"></i> Library
            </button>
            <div class="er-book-meta">
                <div class="er-book-title">${GlaDOS.esc(book.title || '(no book)')}</div>
                <div class="er-book-author">${GlaDOS.esc(book.author || '')}</div>
            </div>
            <div class="er-chapter-info">Chapter ${chapterIndex + 1} of ${chapterCount}</div>
        </div>`;

        // Chapter heading + body
        html += `<div class="er-chapter">
            <h2 class="er-chapter-title">${GlaDOS.esc(chapter.title || 'Untitled')}</h2>
            <div class="er-chapter-body">`;
        if (!chapter.paragraphs || chapter.paragraphs.length === 0) {
            html += '<div class="empty-state">This chapter is empty.</div>';
        } else {
            chapter.paragraphs.forEach((p, i) => {
                const cls = i === paragraphIndex ? 'er-paragraph er-paragraph-active' : 'er-paragraph';
                html += `<p class="${cls}">${GlaDOS.esc(p)}</p>`;
            });
        }
        html += `</div></div>`;

        // Control bar
        html += `<div class="er-controls">
            <button class="btn-accent-outline" onclick="socket.emit('ebook_reader_action',{action:'prev_chapter'})"
                    ${chapterIndex === 0 ? 'disabled' : ''}>
                <i class="icon-skip-back"></i> Previous
            </button>
            <button class="${isPlaying ? 'btn-primary' : 'btn-accent-outline'}"
                    onclick="socket.emit('ebook_reader_action',{action:'${isPlaying ? 'pause' : 'play'}'})"
                    title="${isPlaying ? 'Pause' : 'Play (Pass 2 — coming soon)'}">
                <i class="icon-${isPlaying ? 'pause' : 'play'}"></i> ${isPlaying ? 'Pause' : 'Play'}
            </button>
            <button class="btn-accent-outline" onclick="socket.emit('ebook_reader_action',{action:'bookmark'})"
                    title="Bookmark this position">
                <i class="icon-bookmark"></i> Bookmark
            </button>
            <select class="er-chapter-select" onchange="socket.emit('ebook_reader_action',{action:'goto_chapter',index:parseInt(this.value)})">`;
        for (let i = 0; i < chapterCount; i++) {
            html += `<option value="${i}" ${i === chapterIndex ? 'selected' : ''}>Ch ${i + 1}</option>`;
        }
        html += `</select>
            <button class="btn-accent-outline" onclick="socket.emit('ebook_reader_action',{action:'next_chapter'})"
                    ${chapterIndex >= chapterCount - 1 ? 'disabled' : ''}>
                Next <i class="icon-skip-forward"></i>
            </button>
        </div>`;

        // Bookmarks (if any for this book)
        if (bookmarks.length > 0) {
            html += '<div class="er-bookmarks"><div class="er-bookmarks-title">Bookmarks</div>';
            bookmarks.forEach(b => {
                html += `<div class="er-bookmark"
                    onclick="socket.emit('ebook_reader_action',{action:'goto_chapter',index:${b.chapter}})">
                    Chapter ${b.chapter + 1}, paragraph ${b.paragraph + 1}
                    ${b.label ? `— ${GlaDOS.esc(b.label)}` : ''}
                </div>`;
            });
            html += '</div>';
        }

        container.innerHTML = html;

        // Auto-scroll the active paragraph into view
        const active = container.querySelector('.er-paragraph-active');
        if (active && isPlaying) {
            active.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
    }
};
