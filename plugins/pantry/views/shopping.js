// Shopping list view renderer
GlaDOS.views.shopping_list = {
    cardTitle: 'Shopping',
    cardIcon: '<i class="icon-shopping-cart"></i>',
    _knownIngredients: [],
    _suggestTimer: null,

    renderCard(container) {
        const d = GlaDOS.dashboardData.shopping || {};
        container.innerHTML = `
            <div class="dash-card-header" onclick="window.location.href='/shopping'">
                <span class="dash-card-icon"><i class="icon-shopping-cart"></i></span> Shopping
                <span class="dash-card-badge">${d.count || 0}</span>
            </div>
            <div class="dash-card-body">
                ${d.count ? (d.got||0) + ' of ' + d.count + ' got' : 'List is empty'}
                <div style="margin-top:4px"><a href="/shopping" style="font-size:0.7rem;color:var(--accent);text-decoration:none;opacity:0.7"><i class="icon-smartphone"></i> Open mobile list (works offline)</a></div>
                <div class="quick-input">
                    <input type="text" id="dash-add-item" placeholder="Quick add..." onkeydown="if(event.key==='Enter') GlaDOS.views.shopping_list.dashAdd()">
                    <button onclick="GlaDOS.views.shopping_list.dashAdd()">+</button>
                </div>
            </div>`;
    },
    dashAdd() {
        const el = document.getElementById('dash-add-item');
        if (el && el.value.trim()) { socket.emit('shopping_list_action', { action: 'add_item', name: el.value.trim() }); el.value = ''; }
    },
    render(container, data) {
        const items = data.items || [];
        const total = items.length;
        const gotCount = items.filter(i => i.got).length;
        const unassignedCount = data.unassigned_count || 0;
        GlaDOS.dashboardData.shopping = { count: total, got: gotCount };

        // Cache known ingredients for client-side autocomplete
        if (data.known_ingredients && data.known_ingredients.length) {
            this._knownIngredients = data.known_ingredients;
        }

        let addForm = `<div class="shopping-add-form">
            <input type="text" id="sl-add-name" placeholder="Add item..." onkeydown="if(event.key==='Enter') GlaDOS.views.shopping_list.addItem()">
            <input type="text" id="sl-add-qty" placeholder="Qty" style="width:80px" onkeydown="if(event.key==='Enter') GlaDOS.views.shopping_list.addItem()">
            <button onclick="GlaDOS.views.shopping_list.addItem()">Add</button>
            <button class="btn-accent-outline" style="font-size:0.75rem;padding:8px 10px;white-space:nowrap" onclick="socket.emit('meal_planner_action',{action:'generate_shopping_list'})" title="Generate from meal plan"><i class="icon-calendar"></i> From Plan</button>
        </div>`;
        if (!total) { container.innerHTML = `${addForm}<div class="empty-state">Shopping list is empty</div>`; setTimeout(() => this.setupAutocomplete('sl-add-name'), 0); return; }

        const catOrder = ['produce','meat','seafood','dairy','bakery','frozen','dry_goods','beverages','condiments','spices','snacks','other'];
        const catLabels = {produce:'<i class="icon-carrot"></i> Fresh Produce',meat:'<i class="icon-beef"></i> Butchery & Meat',seafood:'<i class="icon-fish"></i> Seafood',dairy:'<i class="icon-milk"></i> Dairy & Eggs',bakery:'<i class="icon-sandwich"></i> Bakery',frozen:'<i class="icon-snowflake"></i> Frozen',dry_goods:'<i class="icon-warehouse"></i> Pantry & Dry Goods',beverages:'<i class="icon-wine"></i> Beverages',condiments:'<i class="icon-flame"></i> Condiments & Sauces',spices:'<i class="icon-leaf"></i> Spices',snacks:'<i class="icon-cookie"></i> Snacks',other:'<i class="icon-shopping-bag"></i> Other'};
        const groups = {};
        items.forEach(i => { const c = i.category||'other'; if(!groups[c]) groups[c]=[]; groups[c].push(i); });

        let html = '';
        catOrder.forEach(cat => {
            if (!groups[cat]) return;
            html += `<div class="shopping-category-header">${catLabels[cat]||GlaDOS.esc(cat)}</div>`;
            groups[cat].sort((a,b) => a.name.localeCompare(b.name));
            groups[cat].forEach(item => {
                const gc = item.got ? ' got' : '';
                const rOpts = [0,3,7,10,14,30].map(d => `<option value="${d}"${(item.recurring_days||0)===d?' selected':''}>${d?d+'d':'Off'}</option>`).join('');
                const rec = `<select class="si-recurring${item.recurring?' active':''}" onclick="event.stopPropagation()" onchange="socket.emit('shopping_list_action',{action:'set_recurring',item_id:'${item.id}',interval_days:parseInt(this.value)||0})">${rOpts}</select>`;
                html += `<div class="shopping-item${gc}">
                    <div class="si-check" onclick="socket.emit('shopping_list_action',{action:'toggle',item_id:'${item.id}'})"></div>
                    <span class="si-name" onclick="event.stopPropagation();GlaDOS.views.shopping_list.inlineEdit(this,'${item.id}','name','${GlaDOS.esc(item.name).replace(/'/g,"\\'")}')" title="Click to edit name">${GlaDOS.esc(item.name)}</span>
                    <span class="si-qty" onclick="event.stopPropagation();GlaDOS.views.shopping_list.inlineEdit(this,'${item.id}','quantity','${GlaDOS.esc(item.quantity||'').replace(/'/g,"\\'")}')" title="Click to edit quantity">${GlaDOS.esc(item.quantity||'—')}</span>
                    ${rec}
                    <button class="si-remove" onclick="event.stopPropagation();socket.emit('shopping_list_action',{action:'remove',item_id:'${item.id}'})" title="Remove"><i class="icon-x"></i></button>
                </div>`;
            });
        });

        const statusBar = `<div class="shopping-status-bar">
            <span class="progress">${gotCount} of ${total} items</span>
            ${gotCount > 0 ? `<button class="btn-primary" onclick="socket.emit('shopping_list_action',{action:'complete'})">Done Shopping (${gotCount})</button>` : ''}
        </div>`;
        const putAwayBanner = unassignedCount > 0
            ? `<div class="shopping-put-away-banner" onclick="socket.emit('pantry_action',{action:'show_put_away'})"><i class="icon-package"></i> ${unassignedCount} item${unassignedCount !== 1 ? 's' : ''} bought but not stored &mdash; <strong>Put Away</strong></div>`
            : '';
        container.innerHTML = `${addForm}${statusBar}${putAwayBanner}${html}`;
        setTimeout(() => this.setupAutocomplete('sl-add-name'), 0);
    },

    addItem() {
        const n = document.getElementById('sl-add-name'), q = document.getElementById('sl-add-qty');
        if (n && n.value.trim()) {
            socket.emit('shopping_list_action', { action:'add_item', name:n.value.trim(), quantity:q?q.value.trim():'' });
            n.value=''; if(q) q.value='';
            this.dismissSuggestions();
        }
    },

    // --- Autocomplete ---

    setupAutocomplete(inputId) {
        const input = document.getElementById(inputId);
        if (!input || input._acSetup) return;
        input._acSetup = true;
        const self = this;

        input.addEventListener('input', () => {
            clearTimeout(self._suggestTimer);
            const val = input.value.trim().toLowerCase();
            if (val.length < 2 || !self._knownIngredients.length) { self.dismissSuggestions(); return; }
            self._suggestTimer = setTimeout(() => {
                const matches = self._filterIngredients(val);
                if (matches.length) self.showSuggestions(matches, inputId);
                else self.dismissSuggestions();
            }, 150);
        });

        input.addEventListener('keydown', (e) => {
            const dd = document.getElementById('sl-suggestions');
            if (!dd) return;
            const items = dd.querySelectorAll('.sl-sug-item');
            const sel = dd.querySelector('.sl-sug-item.selected');
            let idx = Array.from(items).indexOf(sel);
            if (e.key === 'ArrowDown') {
                e.preventDefault();
                idx = Math.min(idx + 1, items.length - 1);
                items.forEach((it, i) => it.classList.toggle('selected', i === idx));
            } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                idx = Math.max(idx - 1, 0);
                items.forEach((it, i) => it.classList.toggle('selected', i === idx));
            } else if (e.key === 'Enter' && sel) {
                e.preventDefault();
                input.value = sel.textContent;
                self.dismissSuggestions();
            } else if (e.key === 'Escape') {
                self.dismissSuggestions();
            }
        });

        input.addEventListener('blur', () => {
            setTimeout(() => self.dismissSuggestions(), 150);
        });
    },

    _filterIngredients(query) {
        // Simple prefix + substring match, return top 5
        const results = [];
        const prefix = [], substring = [];
        for (const ing of this._knownIngredients) {
            if (ing.startsWith(query)) prefix.push(ing);
            else if (ing.includes(query)) substring.push(ing);
            if (prefix.length + substring.length >= 8) break;
        }
        return prefix.concat(substring).slice(0, 5);
    },

    showSuggestions(suggestions, inputId) {
        this.dismissSuggestions();
        const input = document.getElementById(inputId);
        if (!input || !suggestions.length) return;
        const dd = document.createElement('div');
        dd.id = 'sl-suggestions';
        dd.className = 'sl-suggestions-dropdown';
        suggestions.forEach(s => {
            const item = document.createElement('div');
            item.className = 'sl-sug-item';
            item.textContent = s;
            item.onmousedown = (e) => {
                e.preventDefault();
                input.value = s;
                this.dismissSuggestions();
                input.focus();
            };
            dd.appendChild(item);
        });
        input.parentElement.style.position = 'relative';
        input.parentElement.appendChild(dd);
    },

    dismissSuggestions() {
        const dd = document.getElementById('sl-suggestions');
        if (dd) dd.remove();
    },

    // --- Inline editing ---

    inlineEdit(el, itemId, field, currentValue) {
        const input = document.createElement('input');
        input.type = 'text';
        input.value = currentValue || '';
        input.style.cssText = 'background:rgba(255,255,255,0.1);border:1px solid #ff6600;border-radius:6px;padding:4px 8px;color:#e0e0e0;font-size:0.9rem;width:' + (field === 'quantity' ? '80px' : '180px');

        let saved = false;
        function save() {
            if (saved) return;
            saved = true;
            const val = input.value.trim();
            if (field === 'name' && val && val !== currentValue) {
                socket.emit('shopping_list_action', { action: 'edit_item', item_id: itemId, name: val });
            } else if (field === 'quantity' && val !== (currentValue || '')) {
                socket.emit('shopping_list_action', { action: 'set_quantity', item_id: itemId, quantity: val });
            } else {
                el.textContent = currentValue || (field === 'quantity' ? '—' : '');
                input.replaceWith(el);
            }
        }

        input.addEventListener('keydown', (e) => {
            e.stopPropagation();
            if (e.key === 'Enter') { input.blur(); }
            if (e.key === 'Escape') { saved = true; el.textContent = currentValue || (field === 'quantity' ? '—' : ''); input.replaceWith(el); }
        });
        input.addEventListener('blur', save);
        input.addEventListener('click', (e) => e.stopPropagation());
        el.replaceWith(input);
        input.focus();
        input.select();
    }
};
