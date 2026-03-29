// Shopping list view renderer
GlaDOS.views.shopping_list = {
    cardTitle: 'Shopping',
    cardIcon: '&#128722;',
    renderCard(container) {
        const d = GlaDOS.dashboardData.shopping || {};
        container.innerHTML = `
            <div class="dash-card-header" onclick="socket.emit('shopping_list_action',{action:'show'})">
                <span class="dash-card-icon">&#128722;</span> Shopping
                <span class="dash-card-badge">${d.count || 0}</span>
            </div>
            <div class="dash-card-body">
                ${d.count ? (d.got||0) + ' of ' + d.count + ' got' : 'List is empty'}
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
        GlaDOS.dashboardData.shopping = { count: total, got: gotCount };

        let addForm = `<div class="shopping-add-form">
            <input type="text" id="sl-add-name" placeholder="Add item..." onkeydown="if(event.key==='Enter') GlaDOS.views.shopping_list.addItem()">
            <input type="text" id="sl-add-qty" placeholder="Qty" style="width:80px" onkeydown="if(event.key==='Enter') GlaDOS.views.shopping_list.addItem()">
            <button onclick="GlaDOS.views.shopping_list.addItem()">Add</button>
        </div>`;
        if (!total) { container.innerHTML = `${addForm}<div style="text-align:center;color:#555;padding:40px">Shopping list is empty</div>`; return; }

        const catOrder = ['dairy','produce','meat','seafood','bakery','frozen','dry_goods','beverages','condiments','snacks','other'];
        const catLabels = {dairy:'Dairy & Eggs',produce:'Produce',meat:'Meat',seafood:'Seafood',bakery:'Bakery',frozen:'Frozen',dry_goods:'Dry Goods',beverages:'Beverages',condiments:'Condiments',snacks:'Snacks',other:'Other'};
        const groups = {};
        items.forEach(i => { const c = i.category||'other'; if(!groups[c]) groups[c]=[]; groups[c].push(i); });

        let html = '';
        catOrder.forEach(cat => {
            if (!groups[cat]) return;
            html += `<div class="shopping-category-header">${GlaDOS.esc(catLabels[cat]||cat)}</div>`;
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
                    <button class="si-remove" onclick="event.stopPropagation();socket.emit('shopping_list_action',{action:'remove',item_id:'${item.id}'})" title="Remove">&#10005;</button>
                </div>`;
            });
        });

        container.innerHTML = `${addForm}${html}
            <div class="shopping-footer">
                <span class="progress">${gotCount} of ${total} items</span>
                ${gotCount > 0 ? '<button class="btn-primary" onclick="socket.emit(\'shopping_list_action\',{action:\'complete\'})">Done Shopping</button>' : ''}
            </div>`;
    },

    addItem() {
        const n = document.getElementById('sl-add-name'), q = document.getElementById('sl-add-qty');
        if (n && n.value.trim()) { socket.emit('shopping_list_action', { action:'add_item', name:n.value.trim(), quantity:q?q.value.trim():'' }); n.value=''; if(q) q.value=''; }
    },

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
