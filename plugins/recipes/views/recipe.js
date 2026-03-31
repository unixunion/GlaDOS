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
                <button onclick="socket.emit('recipe_action',{action:'search_from_pantry'})" style="margin-top:6px;width:100%;padding:6px 0;background:rgba(255,102,0,0.12);color:#ff8c00;border:1px solid rgba(255,102,0,0.2);border-radius:6px;cursor:pointer;font-size:0.8rem">&#127860; Use what's expiring</button>
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
        const readyMeals = data.ready_meals || [];
        const query = data.query || '';
        let html = `<div class="view-title">${GlaDOS.esc(data.title || 'Recipe Search')}</div>`;

        // Ready meals section — complete dishes ready to eat/heat
        if (readyMeals.length) {
            const mealItems = readyMeals.map(m => {
                let meta = GlaDOS.esc(m.location || '');
                if (m.expires) {
                    const ed = new Date(m.expires + 'T00:00:00'), td = new Date(); td.setHours(0,0,0,0);
                    const dl = Math.floor((ed - td) / 86400000);
                    const label = dl < 0 ? 'expired' : dl === 0 ? 'today' : dl === 1 ? 'tomorrow' : dl + 'd';
                    const cls = dl <= 0 ? ' rm-exp-urgent' : dl <= 3 ? ' rm-exp-soon' : '';
                    meta += ` &mdash; <span class="rm-expiry${cls}">${label}</span>`;
                }
                return `<div class="rm-item">
                    <span class="rm-icon">&#127373;</span>
                    <div class="rm-info">
                        <div class="rm-name">${GlaDOS.esc(m.name)}</div>
                        <div class="rm-meta">${meta}</div>
                    </div>
                </div>`;
            }).join('');
            html += `<div class="rm-section">
                <div class="rm-section-title">&#127860; Ready to Eat</div>
                <div class="rm-list">${mealItems}</div>
            </div>`;
        }

        // Recipe suggestions section
        if (results.length) {
            const items = results.map(r => {
                const img = r.image_name
                    ? `<img class="rs-thumb" src="/recipe-images/${encodeURIComponent(r.image_name)}.jpg" onerror="this.style.display='none'">`
                    : '<div class="rs-thumb-placeholder">&#127859;</div>';
                let meta, metaCls = '';
                if (r.have_count != null) {
                    const pct = r.ingredient_count ? Math.round(r.have_count / r.ingredient_count * 100) : 0;
                    metaCls = pct >= 80 ? ' rs-match-high' : pct >= 50 ? ' rs-match-mid' : ' rs-match-low';
                    meta = `${r.have_count}/${r.ingredient_count} have`;
                    if (r.missing_count > 0) meta += ` (need ${r.missing_count})`;
                } else {
                    meta = `${r.ingredient_count || '?'} ingredients`;
                }
                const safeTitle = GlaDOS.esc(r.title).replace(/'/g, "\\'");
                const addBtn = r.missing_count > 0
                    ? `<button class="rs-add-btn" onclick="event.stopPropagation();socket.emit('pantry_action',{action:'add_recipe_to_list',recipe_name:'${safeTitle}'})">+ List</button>`
                    : '';
                return `<div class="rs-item" onclick="socket.emit('recipe_action',{action:'select',recipe_name:'${safeTitle}'})">
                    ${img}
                    <div class="rs-info">
                        <div class="rs-title">${GlaDOS.esc(r.title)}</div>
                        <div class="rs-meta${metaCls}">${meta}</div>
                    </div>
                    ${addBtn}
                </div>`;
            }).join('');
            if (readyMeals.length) {
                html += `<div class="rm-section-title" style="margin-top:16px">&#127859; Recipes You Can Make</div>`;
            }
            html += `<div class="rs-list">${items}</div>`;
        }

        if (!results.length && !readyMeals.length) {
            html = `<div class="view-title">No recipes found for "${GlaDOS.esc(query)}"</div>`;
        }

        container.innerHTML = html;
    }
};
