// Recipe browse view — dual-axis category grid + recipe list
GlaDOS.views.recipe_browse = {
    _currentCategory: null,

    render(container, data) {
        if (data.mode === 'categories') {
            this._renderCategories(container, data);
        } else if (data.mode === 'list') {
            this._renderList(container, data);
        }
    },

    _renderCategories(container, data) {
        const cats = data.categories || {};
        const mealTypes = cats.meal_types || [];
        const cuisines = cats.cuisines || [];

        let html = `<div class="view-title">Browse Recipes</div>`;
        html += `<div class="rb-toolbar">
            <button class="btn-primary" onclick="socket.emit('recipe_action',{action:'random_recipe',pantry_aware:true})"><i class="icon-chef-hat"></i> Surprise Me</button>
            <button class="btn-accent-outline" onclick="socket.emit('recipe_action',{action:'random_recipe',pantry_aware:false})"><i class="icon-shuffle"></i> Random</button>
            <button class="btn-accent-outline" onclick="socket.emit('recipe_action',{action:'show_add_form'})"><i class="icon-plus"></i> Add Recipe</button>
        </div>`;

        // Favorites pseudo-category
        const favData = GlaDOS.dashboardData.meal_planner || {};
        if (favData.favorites > 0) {
            html += `<div class="rb-cat-tile rb-cat-favorites" onclick="socket.emit('meal_planner_action',{action:'show_favorites'})" style="margin-bottom:12px">
                <i class="icon-heart"></i>
                <div class="rb-cat-name">Favorites</div>
                <div class="rb-cat-count">${favData.favorites}</div>
            </div>`;
        }

        // By Meal Type
        if (mealTypes.length) {
            html += '<div class="rb-section-title">By Meal</div>';
            html += '<div class="rb-cat-grid">';
            mealTypes.forEach(cat => {
                const label = cat.name.replace(/_/g, ' ');
                html += `<div class="rb-cat-tile" onclick="socket.emit('recipe_action',{action:'browse_category',category:'meal:${cat.name}'})">
                    <i class="icon-${cat.icon || 'chef-hat'}"></i>
                    <div class="rb-cat-name">${label}</div>
                    <div class="rb-cat-count">${cat.count}</div>
                </div>`;
            });
            html += '</div>';
        }

        // By Cuisine
        if (cuisines.length) {
            html += '<div class="rb-section-title">By Cuisine</div>';
            html += '<div class="rb-cat-grid">';
            cuisines.forEach(cat => {
                const label = cat.name.replace(/_/g, ' ');
                html += `<div class="rb-cat-tile" onclick="socket.emit('recipe_action',{action:'browse_category',category:'cuisine:${cat.name}'})">
                    <i class="icon-${cat.icon || 'chef-hat'}"></i>
                    <div class="rb-cat-name">${label}</div>
                    <div class="rb-cat-count">${cat.count}</div>
                </div>`;
            });
            html += '</div>';
        }

        container.innerHTML = html;
    },

    _renderList(container, data) {
        const items = data.items || [];
        const category = data.category || '';
        const offset = data.offset || 0;
        const total = data.total || 0;
        this._currentCategory = category;

        let html = `<div class="view-title">${GlaDOS.esc(data.title || 'Recipes')}
            <span class="rb-total">${total} recipes</span>
        </div>`;

        html += `<div class="rb-toolbar">
            <button class="btn-accent-outline" onclick="socket.emit('recipe_action',{action:'browse'})"><i class="icon-grid"></i> All Categories</button>
            <button class="btn-accent-outline" onclick="socket.emit('recipe_action',{action:'random_recipe',category:'${category}',pantry_aware:true})"><i class="icon-chef-hat"></i> Surprise Me</button>
            <button class="btn-accent-outline" onclick="socket.emit('recipe_action',{action:'random_recipe',category:'${category}',pantry_aware:false})"><i class="icon-shuffle"></i> Random</button>
        </div>`;

        if (!items.length) {
            html += '<div class="empty-state">No recipes in this category</div>';
            container.innerHTML = html;
            return;
        }

        html += '<div class="rb-recipe-grid">';
        items.forEach(item => {
            const img = item.image_name
                ? `<img class="rb-thumb" src="/recipe-images/${encodeURIComponent(item.image_name)}.jpg" onerror="this.parentElement.classList.add('no-img')">`
                : '<div class="rb-thumb-placeholder"><i class="icon-chef-hat"></i></div>';
            const safeTitle = GlaDOS.esc(item.title).replace(/'/g, "\\'");
            const matchPct = item.ingredient_count ? Math.round((item.have_count || 0) / item.ingredient_count * 100) : 0;
            const matchCls = matchPct >= 80 ? 'rs-match-high' : matchPct >= 50 ? 'rs-match-mid' : 'rs-match-low';
            const matchLabel = item.have_count != null ? `<span class="${matchCls}">${matchPct}% match</span>` : '';
            html += `<div class="rb-recipe-card" onclick="socket.emit('recipe_action',{action:'select',recipe_name:'${safeTitle}'})">
                ${img}
                <div class="rb-recipe-info">
                    <div class="rb-recipe-title">${GlaDOS.esc(item.title)}</div>
                    <div class="rb-recipe-meta">${item.ingredient_count} ingredients ${matchLabel}</div>
                </div>
                <button class="rs-fav-btn" data-recipe-name="${safeTitle}" onclick="event.stopPropagation();socket.emit('meal_planner_action',{action:'toggle_favorite',recipe_name:'${safeTitle}'})" title="Favorite">&hearts;</button>
            </div>`;
        });
        html += '</div>';

        // Pagination
        const shown = offset + items.length;
        if (shown < total) {
            html += `<div class="rb-pagination">
                <button class="btn-accent-outline" onclick="socket.emit('recipe_action',{action:'browse_category',category:'${category}',offset:${shown}})">
                    Load More (${total - shown} remaining)
                </button>
            </div>`;
        }

        container.innerHTML = html;
    }
};
