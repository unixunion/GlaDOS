// Favorites view renderer
GlaDOS.views.favorites = {
    render(container, data) {
        const items = data.items || [];
        let html = `<div class="view-title">${GlaDOS.esc(data.title || 'Favorite Recipes')}</div>`;

        if (!items.length) {
            html += '<div class="empty-state">No favorites yet. Select a recipe and tap the heart to save it.</div>';
            container.innerHTML = html;
            return;
        }

        html += '<div class="fav-grid">';
        items.forEach(item => {
            const img = item.image_name
                ? `<img class="fav-thumb" src="/recipe-images/${encodeURIComponent(item.image_name)}.jpg" onerror="this.style.display='none'">`
                : '<div class="fav-thumb-placeholder"><i class="icon-chef-hat"></i></div>';
            const safeTitle = GlaDOS.esc(item.title).replace(/'/g, "\\'");
            const tags = (item.tags || []).map(t => `<span class="fav-tag">${GlaDOS.esc(t)}</span>`).join('');
            html += `<div class="fav-card" onclick="socket.emit('recipe_action',{action:'select',recipe_name:'${safeTitle}'})">
                ${img}
                <div class="fav-info">
                    <div class="fav-title">${GlaDOS.esc(item.title)}</div>
                    <div class="fav-meta">${item.ingredient_count || '?'} ingredients ${tags}</div>
                </div>
                <div class="fav-actions">
                    <button class="btn-fav active" onclick="event.stopPropagation();socket.emit('meal_planner_action',{action:'toggle_favorite',recipe_name:'${safeTitle}'})" title="Remove favorite">&hearts;</button>
                    <button class="btn-accent-outline fav-plan-btn" onclick="event.stopPropagation();socket.emit('meal_planner_action',{action:'plan_meal',recipe_name:'${safeTitle}'})" title="Add to plan">+ Plan</button>
                </div>
            </div>`;
        });
        html += '</div>';
        container.innerHTML = html;
    }
};

// Listen for favorite state updates (non-navigating)
GlaDOS.views.favorites_state = {
    render(container, data) {
        // Update favorite buttons across all views without navigating
        const favTitles = new Set((data.favorite_titles || []).map(t => t.toLowerCase()));
        document.querySelectorAll('.btn-fav').forEach(btn => {
            const name = btn.dataset.recipeName;
            if (name) {
                btn.classList.toggle('active', favTitles.has(name.toLowerCase()));
            }
        });
    }
};
