// Pantry view renderer
GlaDOS.views.pantry = {
    renderCard(container) {
        const d = GlaDOS.dashboardData.pantry || {};
        const expList = (d.expiring || []).slice(0, 3).map(e => {
            const cls = e.days_left <= 0 ? ' expired' : '';
            const label = e.days_left <= 0 ? 'expired' : e.days_left === 1 ? 'tomorrow' : e.days_left + 'd';
            return `<div class="dash-expiring-item${cls}">${GlaDOS.esc(e.name)} — ${label}</div>`;
        }).join('');
        container.innerHTML = `
            <div class="dash-card-header" onclick="socket.emit('pantry_action',{action:'show'})">
                <span class="dash-card-icon"><i class="icon-warehouse"></i></span> Pantry
                <span class="dash-card-badge${(d.expiring||[]).length ? ' urgent' : ''}">${d.count || 0}</span>
            </div>
            <div class="dash-card-body">
                ${expList || '<span class="text-muted">Nothing expiring</span>'}
            </div>`;
    },
    render(container, data) {
        const locations = data.locations || [];
        const allLocations = locations.filter(l => l.id !== '_unassigned');
        const expiring = data.expiring_soon || [];
        const unassignedLoc = locations.find(l => l.id === '_unassigned');
        const unassignedCount = unassignedLoc ? unassignedLoc.items.length : 0;
        let html = '<div class="pantry-toolbar">'
            + (unassignedCount > 0 ? `<button class="toolbar-highlight" onclick="socket.emit('pantry_action',{action:'show_put_away'})"><i class="icon-package"></i> Put Away (${unassignedCount})</button>` : '')
            + '<button onclick="socket.emit(\'pantry_action\',{action:\'reclassify\'})"><i class="icon-tag"></i> Reclassify All</button>'
            + '<button onclick="socket.emit(\'pantry_action\',{action:\'get_shelf_life_config\'})"><i class="icon-settings"></i> Shelf Life</button>'
            + '</div>';
        if (expiring.length) {
            const expItems = expiring.map(i => {
                const l = i.days_left<0?'expired':i.days_left===0?'today':i.days_left===1?'tomorrow':`in ${i.days_left} days`;
                const actionLabel = i.days_left < 0 ? 'Toss' : 'Used it';
                const actionCls = i.days_left < 0 ? 'exp-btn-toss' : 'exp-btn-used';
                return `<div class="exp-row"><span class="exp-info">${GlaDOS.esc(i.name)} (${GlaDOS.esc(i.location)}) — ${l}</span><button class="exp-btn ${actionCls}" onclick="socket.emit('pantry_action',{action:'remove_item',item_id:'${i.id}'});GlaDOS.views.pantry.toast('Removed ${GlaDOS.esc(i.name).replace(/'/g,"\\'")}')">${actionLabel}</button></div>`;
            }).join('');
            html += `<div class="pantry-expiry-banner">
                <div class="pantry-expiry-banner-title"><i class="icon-alert-triangle"></i> Expiring Soon
                    <button class="exp-recipe-btn" onclick="socket.emit('recipe_action',{action:'search_from_pantry',expiring_items:JSON.parse(this.dataset.items)});" data-items='${JSON.stringify(expiring.map(i=>i.name))}'><i class="icon-chef-hat"></i> Find Recipes</button>
                </div>
                ${expItems}
            </div>`;
        }
        GlaDOS.dashboardData.pantry = { count: locations.reduce((s,l)=>s+l.items.length, 0), expiring: expiring };

        locations.forEach((loc, idx) => {
            const exp = idx === 0 || loc.items.length > 0 ? ' expanded' : '';
            const locTypeIcon = loc.type === 'fridge' ? '<i class="icon-refrigerator"></i>' : loc.type === 'freezer' ? '<i class="icon-snowflake"></i>' : '<i class="icon-warehouse"></i>';
            const locTypeLabel = loc.type === 'fridge' ? 'fridge' : loc.type === 'freezer' ? 'freezer' : 'room temp';
            let itemsHtml = '';
            if (!loc.items.length) { itemsHtml = '<div class="pantry-empty">Empty</div>'; }
            else {
                loc.items.forEach(item => {
                    const notes = item.notes ? `<span class="pi-notes">${GlaDOS.esc(item.notes)}</span>` : '';
                    let expHtml = '';
                    if (item.expires) {
                        const ed = new Date(item.expires+'T00:00:00'), td = new Date(); td.setHours(0,0,0,0);
                        const dl = Math.floor((ed-td)/86400000);
                        let cls = 'ok'; if(dl<=0) cls='expired'; else if(dl<=3) cls='expiring-soon'; else if(dl<=7) cls='expiring-week';
                        const rl = dl<0?'expired':dl===0?'today':dl===1?'tomorrow':dl+'d';
                        const estCls = item.expiry_source === 'estimated' ? ' estimated' : '';
                        const prefix = item.expiry_source === 'estimated' ? '~' : '';
                        expHtml = `<span class="pi-expiry ${cls}${estCls}" title="${item.expiry_source === 'estimated' ? 'Estimated — click to set exact date' : ''}">${prefix}${rl} <input type="date" class="pantry-expiry-picker" value="${item.expires}" onchange="socket.emit('pantry_action',{action:'set_expiry',item_id:'${item.id}',expires:this.value})" onclick="event.stopPropagation()"></span>`;
                    } else {
                        expHtml = `<span><input type="date" class="pantry-expiry-picker empty" onchange="socket.emit('pantry_action',{action:'set_expiry',item_id:'${item.id}',expires:this.value})" onclick="event.stopPropagation()" title="Set expiry"></span>`;
                    }
                    let tagsHtml = '<span class="pi-tags">';
                    if (item.item_type) {
                        const tl = item.item_type === 'ready_meal' ? 'meal' : item.item_type;
                        tagsHtml += `<span class="pi-tag tag-${item.item_type}">${GlaDOS.esc(tl)}</span>`;
                    }
                    if (item.category && item.category !== 'other') {
                        const cl = item.category.replace(/_/g, ' ');
                        tagsHtml += `<span class="pi-tag tag-${item.category}">${GlaDOS.esc(cl)}</span>`;
                    }
                    tagsHtml += '</span>';
                    // Move dropdown
                    const moveOpts = allLocations.filter(l => l.id !== loc.id).map(l =>
                        `<option value="${l.id}">${GlaDOS.esc(l.name)}</option>`
                    ).join('');
                    const moveHtml = moveOpts ? `<select class="pi-move" onchange="if(this.value){socket.emit('pantry_action',{action:'move_item',item_id:'${item.id}',new_location_id:this.value});GlaDOS.views.pantry.toast('Moved ${GlaDOS.esc(item.name).replace(/'/g,"\\'")}');this.value='';}" title="Move to..."><option value="">Move</option>${moveOpts}</select>` : '';
                    itemsHtml += `<div class="pantry-item"><span class="pi-name">${GlaDOS.esc(item.name)}</span>${notes}${tagsHtml}${expHtml}${moveHtml}<button class="si-edit" onclick="GlaDOS.views.pantry.editItem('${item.id}','${GlaDOS.esc(item.name).replace(/'/g,"\\'")}','${GlaDOS.esc(item.notes||'').replace(/'/g,"\\'")}')" title="Edit"><i class="icon-pencil"></i></button><button class="pi-remove" onclick="GlaDOS.views.pantry.removeItem('${item.id}','${GlaDOS.esc(item.name).replace(/'/g,"\\'")}','${loc.id}')" title="Remove"><i class="icon-x"></i></button></div>`;
                });
            }
            const addHtml = `<div class="pantry-add-item">
                <input type="text" placeholder="Add item..." id="pa-n-${loc.id}" onkeydown="if(event.key==='Enter')GlaDOS.views.pantry.addItem('${loc.id}')">
                <input type="text" placeholder="Notes" style="width:80px" id="pa-t-${loc.id}" onkeydown="if(event.key==='Enter')GlaDOS.views.pantry.addItem('${loc.id}')">
                <input type="date" id="pa-e-${loc.id}" style="width:130px" title="Expiry">
                <button onclick="GlaDOS.views.pantry.addItem('${loc.id}')">+</button>
            </div>`;
            // Location type selector
            const typeSelect = loc.id !== '_unassigned' ? `<select class="pantry-loc-type" onchange="socket.emit('pantry_action',{action:'set_location_type',location_id:'${loc.id}',type:this.value})" onclick="event.stopPropagation()"><option value="fridge"${loc.type==='fridge'?' selected':''}>Fridge</option><option value="freezer"${loc.type==='freezer'?' selected':''}>Freezer</option><option value="room_temp"${loc.type==='room_temp'?' selected':''}>Room Temp</option></select>` : '';
            html += `<div class="pantry-location${exp}">
                <div class="pantry-loc-header" onclick="this.parentElement.classList.toggle('expanded')"><span class="pantry-loc-name">${locTypeIcon} ${GlaDOS.esc(loc.name)}</span>${typeSelect}<span class="pantry-loc-count">${loc.items.length}</span></div>
                <div class="pantry-loc-items">${itemsHtml}${addHtml}</div>
            </div>`;
        });
        html += `<div class="pantry-add-loc"><input type="text" id="pa-new-loc" placeholder="New location..." onkeydown="if(event.key==='Enter')GlaDOS.views.pantry.addLocation()"><button onclick="GlaDOS.views.pantry.addLocation()">Add</button></div>`;
        container.innerHTML = html;
    },

    addItem(lid) {
        const n=document.getElementById('pa-n-'+lid), t=document.getElementById('pa-t-'+lid), e=document.getElementById('pa-e-'+lid);
        if(n&&n.value.trim()) { const name=n.value.trim(); socket.emit('pantry_action',{action:'add_item',location_id:lid,name:name,notes:t?t.value.trim():'',expires:e?e.value:''}); this.toast('Added '+name); n.value=''; if(t)t.value=''; if(e)e.value=''; }
    },

    addLocation() {
        const el=document.getElementById('pa-new-loc');
        if(el&&el.value.trim()){socket.emit('pantry_action',{action:'add_location',name:el.value.trim()});el.value='';}
    },

    removeItem(id, name, locationId) {
        socket.emit('pantry_action', {action: 'remove_item', item_id: id});
        this.toast('Removed ' + name, () => {
            socket.emit('pantry_action', {action: 'add_item', location_id: locationId, name: name});
        });
    },

    editItem(id, name, notes) {
        const newName = prompt('Item name:', name);
        if (newName === null) return;
        const newNotes = prompt('Notes (or leave blank):', notes);
        if (newNotes === null) return;
        socket.emit('pantry_action', { action: 'edit_item', item_id: id, name: newName.trim(), notes: newNotes.trim() });
    },

    toast(msg, undoFn) {
        let el = document.getElementById('pantry-toast');
        if (!el) {
            el = document.createElement('div');
            el.id = 'pantry-toast';
            el.className = 'pantry-toast';
            document.body.appendChild(el);
        }
        if (undoFn) {
            el.innerHTML = `${GlaDOS.esc(msg)} <button class="toast-undo" onclick="event.stopPropagation()">Undo</button>`;
            el.querySelector('.toast-undo').onclick = () => { undoFn(); el.classList.remove('visible'); };
        } else {
            el.textContent = msg;
        }
        el.classList.add('visible');
        clearTimeout(el._timer);
        el._timer = setTimeout(() => el.classList.remove('visible'), 5000);
    }
};

// Shelf life config view
GlaDOS.views.pantry_shelf_life = {
    render(container, data) {
        const config = data.config || {};
        const categories = Object.keys(config).sort();
        const locTypes = ['fridge', 'freezer', 'room_temp'];
        const locLabels = { fridge: '<i class="icon-refrigerator"></i> Fridge', freezer: '<i class="icon-snowflake"></i> Freezer', room_temp: '<i class="icon-warehouse"></i> Room Temp' };
        let rows = categories.map(cat => {
            const label = cat.replace(/_/g, ' ');
            const cells = locTypes.map(lt => {
                const val = (config[cat] || {})[lt] || 0;
                return `<td><input type="number" min="0" max="999" value="${val}" data-cat="${cat}" data-lt="${lt}" class="sl-input"></td>`;
            }).join('');
            return `<tr><td class="sl-cat">${GlaDOS.esc(label)}</td>${cells}</tr>`;
        }).join('');
        container.innerHTML = `
            <div class="view-title">Shelf Life Defaults (days)</div>
            <p class="text-help" style="margin-bottom:12px">How long items last in each storage type. Changes apply to new items only.</p>
            <table class="sl-table">
                <thead><tr><th>Category</th>${locTypes.map(lt => `<th>${locLabels[lt]}</th>`).join('')}</tr></thead>
                <tbody>${rows}</tbody>
            </table>
            <div style="margin-top:12px;display:flex;gap:8px">
                <button class="btn-primary" onclick="GlaDOS.views.pantry_shelf_life.save()">Save</button>
                <button class="btn-primary" style="background:#333" onclick="socket.emit('pantry_action',{action:'show'})">Back to Pantry</button>
            </div>`;
    },
    save() {
        const inputs = document.querySelectorAll('.sl-input');
        const config = {};
        inputs.forEach(inp => {
            const cat = inp.dataset.cat, lt = inp.dataset.lt;
            if (!config[cat]) config[cat] = {};
            config[cat][lt] = parseInt(inp.value) || 0;
        });
        socket.emit('pantry_action', { action: 'update_shelf_life', config });
        socket.emit('pantry_action', { action: 'show' });
    }
};

// Put-away guided view — assign locations to unassigned items after shopping
GlaDOS.views.pantry_put_away = {
    render(container, data) {
        const items = data.items || [];
        const locations = data.locations || [];
        if (!items.length) {
            container.innerHTML = '<div class="view-title">All items put away!</div>';
            setTimeout(() => socket.emit('pantry_action', {action: 'show'}), 1500);
            return;
        }
        const locTypeIcon = t => t === 'fridge' ? '<i class="icon-refrigerator"></i>' : t === 'freezer' ? '<i class="icon-snowflake"></i>' : '<i class="icon-warehouse"></i>';
        const itemCards = items.map(item => {
            const catLabel = (item.category || 'other').replace(/_/g, ' ');
            const typeLabel = item.item_type === 'ready_meal' ? 'meal' : '';
            const suggested = item.suggested_location_id;
            const locBtns = locations.map(l => {
                const isSuggested = l.id === suggested;
                const cls = isSuggested ? 'pa-loc-btn suggested' : 'pa-loc-btn';
                const icon = locTypeIcon(l.type);
                return `<button class="${cls}" onclick="socket.emit('pantry_action',{action:'assign_location',item_id:'${item.id}',location_id:'${l.id}'});GlaDOS.views.pantry.toast('${GlaDOS.esc(item.name).replace(/'/g,"\\'")} → ${GlaDOS.esc(l.name).replace(/'/g,"\\'")}')">${icon} ${GlaDOS.esc(l.name)}</button>`;
            }).join('');
            return `<div class="pa-item">
                <div class="pa-item-header">
                    <span class="pa-item-name">${GlaDOS.esc(item.name)}</span>
                    <span class="pa-item-meta">${catLabel}${typeLabel ? ' &middot; ' + typeLabel : ''}${item.notes ? ' &middot; ' + GlaDOS.esc(item.notes) : ''}</span>
                </div>
                <div class="pa-loc-btns">${locBtns}</div>
            </div>`;
        }).join('');
        container.innerHTML = `
            <div class="view-title">Put Away Shopping</div>
            <p class="text-help" style="margin-bottom:14px">${items.length} item${items.length !== 1 ? 's' : ''} to put away. Suggested locations highlighted.</p>
            ${itemCards}
            <div style="margin-top:16px;display:flex;gap:8px">
                <button class="btn-primary" style="background:#333" onclick="socket.emit('pantry_action',{action:'show'})">Skip &mdash; go to Pantry</button>
            </div>`;
    }
};
