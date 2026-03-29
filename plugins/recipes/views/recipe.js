// Recipe view renderer
GlaDOS.views.recipe = {
    renderCard(container) {
        const d = GlaDOS.dashboardData.recipes || {};
        container.innerHTML = `
            <div class="dash-card-header">
                <span class="dash-card-icon">&#127859;</span> Recipes
                <span class="dash-card-badge">${d.count || '...'}</span>
            </div>
            <div class="dash-card-body">
                <div class="quick-input">
                    <input type="text" id="dash-recipe-search" placeholder="Search recipes..." onkeydown="if(event.key==='Enter') GlaDOS.views.recipe.dashSearch()">
                    <button onclick="GlaDOS.views.recipe.dashSearch()">&#128269;</button>
                </div>
                <button onclick="socket.emit('recipe_action',{action:'search_from_pantry'})" style="margin-top:6px;width:100%;padding:6px 0;background:rgba(255,102,0,0.12);color:#ff8c00;border:1px solid rgba(255,102,0,0.2);border-radius:6px;cursor:pointer;font-size:0.8rem">What can I make?</button>
            </div>`;
    },
    dashSearch() {
        const el = document.getElementById('dash-recipe-search');
        if (el && el.value.trim()) { socket.emit('recipe_action', { action: 'search', query: el.value.trim() }); el.value = ''; }
    },
    render(container, data) {
        const title = data.title || 'Recipe';
        let img = '';
        if (data.image_name) img = `<img class="recipe-image" src="/recipe-images/${encodeURIComponent(data.image_name)}.jpg" onerror="this.style.display='none'" alt="${GlaDOS.esc(title)}">`;
        const safeTitle = GlaDOS.esc(title).replace(/'/g, "\\'");
        const actions = `<div style="display:flex;gap:8px;margin:12px 0">
            <button class="btn-primary" style="font-size:0.85rem;padding:8px 16px" onclick="socket.emit('pantry_action',{action:'check_recipe',recipe_name:'${safeTitle}'})">Check Pantry</button>
            <button class="btn-primary" style="font-size:0.85rem;padding:8px 16px;background:#2a3a5c" onclick="socket.emit('pantry_action',{action:'add_recipe_to_list',recipe_name:'${safeTitle}'})">Add Missing to List</button>
        </div>`;
        if (data.ingredients && data.directions) {
            const ings = (Array.isArray(data.ingredients) ? data.ingredients : data.ingredients.split('\n')).map(i => i.replace(/^[-\s]*/, '').trim()).filter(Boolean).map(i => `<li>${GlaDOS.esc(i)}</li>`).join('');
            const steps = (Array.isArray(data.directions) ? data.directions : data.directions.split('\n')).map(s => s.replace(/^(Step\s*\d+[:\s]*)/i, '').trim()).filter(Boolean).map(s => `<li>${GlaDOS.esc(s)}</li>`).join('');
            container.innerHTML = `${img}<div class="view-title">${GlaDOS.esc(title)}</div>${actions}<div class="recipe-section-label">Ingredients</div><ul class="ingredient-list">${ings}</ul><div class="recipe-section-label">Directions</div><ol class="step-list">${steps}</ol>`;
        } else {
            container.innerHTML = `${img}<div class="view-title">${GlaDOS.esc(title)}</div>${actions}<div class="info-content">${GlaDOS.esc(data.content || '')}</div>`;
        }
    }
};

GlaDOS.views.recipe_search = {
    render(container, data) {
        const results = data.results || [];
        const query = data.query || '';
        if (!results.length) {
            container.innerHTML = `<div class="view-title">No recipes found for "${GlaDOS.esc(query)}"</div>`;
            return;
        }
        const items = results.map(r => {
            const img = r.image_name
                ? `<img class="rs-thumb" src="/recipe-images/${encodeURIComponent(r.image_name)}.jpg" onerror="this.style.display='none'">`
                : '<div class="rs-thumb-placeholder">&#127859;</div>';
            return `<div class="rs-item" onclick="socket.emit('recipe_action',{action:'select',recipe_name:'${GlaDOS.esc(r.title).replace(/'/g, "\\'")}'})">
                ${img}
                <div class="rs-info">
                    <div class="rs-title">${GlaDOS.esc(r.title)}</div>
                    <div class="rs-meta">${r.ingredient_count || '?'} ingredients</div>
                </div>
            </div>`;
        }).join('');
        container.innerHTML = `<div class="view-title">${GlaDOS.esc(data.title || 'Recipe Search')}</div><div class="rs-list">${items}</div>`;
    }
};
