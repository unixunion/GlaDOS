// Info view renderer
GlaDOS.views.info = {
    render(container, data) {
        container.innerHTML = `<div class="view-title">${GlaDOS.esc(data.title||'Information')}</div><div class="info-content">${GlaDOS.esc(data.content||'')}</div>`;
    }
};
