// Meal plan view renderer
GlaDOS.views.meal_plan = {
    cardTitle: 'Meal Plan',
    cardIcon: '<i class="icon-calendar"></i>',

    renderCard(container) {
        const d = GlaDOS.dashboardData.meal_planner || {};
        const planned = d.planned || 0;
        const favs = d.favorites || 0;
        container.innerHTML = `
            <div class="dash-card-header" onclick="socket.emit('meal_planner_action',{action:'show_meal_plan'})">
                <span class="dash-card-icon"><i class="icon-calendar"></i></span> Meal Plan
                <span class="dash-card-badge">${planned}/7</span>
            </div>
            <div class="dash-card-body">
                ${planned ? planned + ' meals planned' : 'No meals planned'}
                ${favs ? '<br>' + favs + ' favorites' : ''}
                <div style="display:flex;gap:6px;margin-top:6px">
                    <button class="btn-accent-outline" onclick="socket.emit('meal_planner_action',{action:'show_favorites'})" style="flex:1;padding:6px 0;font-size:0.8rem"><i class="icon-heart"></i> Favorites</button>
                    <button class="btn-accent-outline" onclick="socket.emit('meal_planner_action',{action:'show_meal_plan'})" style="flex:1;padding:6px 0;font-size:0.8rem"><i class="icon-calendar"></i> Plan</button>
                </div>
            </div>`;
    },

    render(container, data) {
        const meals = data.meals || [];
        const suggestions = data.suggestions || [];
        const week = data.week || '';
        const days = ['monday','tuesday','wednesday','thursday','friday','saturday','sunday'];
        const dayLabels = {monday:'Mon',tuesday:'Tue',wednesday:'Wed',thursday:'Thu',friday:'Fri',saturday:'Sat',sunday:'Sun'};

        let html = `<div class="view-title">${GlaDOS.esc(data.title || 'Meal Plan')}</div>`;

        // Toolbar
        html += `<div class="mp-toolbar">
            <button class="btn-accent-outline" onclick="socket.emit('meal_planner_action',{action:'show_favorites'})"><i class="icon-heart"></i> Favorites (${data.favorites_count || 0})</button>
            <button class="btn-primary" onclick="socket.emit('meal_planner_action',{action:'generate_shopping_list'})"><i class="icon-shopping-cart"></i> Generate Shopping List</button>
        </div>`;

        // Suggestions section (if present)
        if (suggestions.length) {
            html += '<div class="mp-section-title">Suggested Meals</div>';
            html += '<div class="mp-suggestions">';
            suggestions.forEach(s => {
                const img = s.image_name
                    ? `<img class="mp-thumb" src="/recipe-images/${encodeURIComponent(s.image_name)}.jpg" onerror="this.style.display='none'">`
                    : '<div class="mp-thumb-placeholder"><i class="icon-chef-hat"></i></div>';
                const safeTitle = GlaDOS.esc(s.title).replace(/'/g, "\\'");
                const matchCls = s.pantry_match_pct >= 80 ? 'rs-match-high' : s.pantry_match_pct >= 50 ? 'rs-match-mid' : 'rs-match-low';
                const favIcon = s.is_favorite ? ' <i class="icon-heart" style="color:var(--accent)"></i>' : '';
                html += `<div class="mp-suggestion-card" onclick="socket.emit('meal_planner_action',{action:'plan_meal',recipe_name:'${safeTitle}'})">
                    ${img}
                    <div class="mp-sug-info">
                        <div class="mp-sug-title">${GlaDOS.esc(s.title)}${favIcon}</div>
                        <div class="mp-sug-meta ${matchCls}">${s.pantry_match_pct}% match, need ${s.missing_count}</div>
                    </div>
                </div>`;
            });
            html += '</div>';
        }

        // Weekly plan
        if (meals.length || !suggestions.length) {
            html += '<div class="mp-section-title">This Week</div>';
            html += '<div class="mp-week">';
            days.forEach(day => {
                const meal = meals.find(m => m.day === day);
                if (meal) {
                    const img = meal.image_name
                        ? `<img class="mp-day-thumb" src="/recipe-images/${encodeURIComponent(meal.image_name)}.jpg" onerror="this.style.display='none'">`
                        : '';
                    const safeTitle = GlaDOS.esc(meal.recipe_title).replace(/'/g, "\\'");
                    html += `<div class="mp-day filled">
                        <span class="mp-day-label">${dayLabels[day]}</span>
                        ${img}
                        <span class="mp-day-recipe" onclick="socket.emit('recipe_action',{action:'select',recipe_name:'${safeTitle}'})">${GlaDOS.esc(meal.recipe_title)}</span>
                        <button class="mp-day-remove" onclick="event.stopPropagation();socket.emit('meal_planner_action',{action:'remove_planned_meal',day:'${day}'})" title="Remove"><i class="icon-x"></i></button>
                    </div>`;
                } else {
                    html += `<div class="mp-day empty">
                        <span class="mp-day-label">${dayLabels[day]}</span>
                        <span class="mp-day-empty">—</span>
                    </div>`;
                }
            });
            html += '</div>';
        }

        if (!meals.length && !suggestions.length) {
            html += '<div class="empty-state">No meals planned. Say "suggest meals for the week" or browse your favorites.</div>';
        }

        container.innerHTML = html;
    }
};
