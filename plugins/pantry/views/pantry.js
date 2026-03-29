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
                <span class="dash-card-icon">&#127968;</span> Pantry
                <span class="dash-card-badge${(d.expiring||[]).length ? ' urgent' : ''}">${d.count || 0}</span>
            </div>
            <div class="dash-card-body">
                ${expList || '<span style="color:#555">Nothing expiring</span>'}
            </div>`;
    },
    render(container, data) {
        const locations = data.locations || [];
        const expiring = data.expiring_soon || [];
        let html = '<div class="pantry-toolbar"><button onclick="socket.emit(\'pantry_action\',{action:\'reclassify\'})">&#x1F3F7; Reclassify All</button></div>';
        if (expiring.length) {
            html += `<div class="pantry-expiry-banner"><div class="pantry-expiry-banner-title">&#9888; Expiring Soon</div>${expiring.map(i => {
                const l = i.days_left<0?'expired':i.days_left===0?'today':i.days_left===1?'tomorrow':`in ${i.days_left} days`;
                return `<div>${GlaDOS.esc(i.name)} (${GlaDOS.esc(i.location)}) — ${l}</div>`;
            }).join('')}</div>`;
        }
        GlaDOS.dashboardData.pantry = { count: locations.reduce((s,l)=>s+l.items.length, 0), expiring: expiring };

        locations.forEach((loc, idx) => {
            const exp = idx === 0 || loc.items.length > 0 ? ' expanded' : '';
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
                        expHtml = `<span class="pi-expiry ${cls}">${rl} <input type="date" class="pantry-expiry-picker" value="${item.expires}" onchange="socket.emit('pantry_action',{action:'set_expiry',item_id:'${item.id}',expires:this.value})" onclick="event.stopPropagation()"></span>`;
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
                    itemsHtml += `<div class="pantry-item"><span class="pi-name">${GlaDOS.esc(item.name)}</span>${notes}${tagsHtml}${expHtml}<button class="si-edit" onclick="GlaDOS.views.pantry.editItem('${item.id}','${GlaDOS.esc(item.name).replace(/'/g,"\\'")}','${GlaDOS.esc(item.notes||'').replace(/'/g,"\\'")}')" title="Edit">&#9998;</button><button class="pi-remove" onclick="socket.emit('pantry_action',{action:'remove_item',item_id:'${item.id}'})" title="Remove">&#10005;</button></div>`;
                });
            }
            const addHtml = `<div class="pantry-add-item">
                <input type="text" placeholder="Add item..." id="pa-n-${loc.id}" onkeydown="if(event.key==='Enter')GlaDOS.views.pantry.addItem('${loc.id}')">
                <input type="text" placeholder="Notes" style="width:80px" id="pa-t-${loc.id}" onkeydown="if(event.key==='Enter')GlaDOS.views.pantry.addItem('${loc.id}')">
                <input type="date" id="pa-e-${loc.id}" style="width:130px" title="Expiry">
                <button onclick="GlaDOS.views.pantry.addItem('${loc.id}')">+</button>
            </div>`;
            html += `<div class="pantry-location${exp}">
                <div class="pantry-loc-header" onclick="this.parentElement.classList.toggle('expanded')"><span class="pantry-loc-name">${GlaDOS.esc(loc.name)}</span><span class="pantry-loc-count">${loc.items.length}</span></div>
                <div class="pantry-loc-items">${itemsHtml}${addHtml}</div>
            </div>`;
        });
        html += `<div class="pantry-add-loc"><input type="text" id="pa-new-loc" placeholder="New location..." onkeydown="if(event.key==='Enter')GlaDOS.views.pantry.addLocation()"><button onclick="GlaDOS.views.pantry.addLocation()">Add</button></div>`;
        container.innerHTML = html;
    },

    addItem(lid) {
        const n=document.getElementById('pa-n-'+lid), t=document.getElementById('pa-t-'+lid), e=document.getElementById('pa-e-'+lid);
        if(n&&n.value.trim()) { socket.emit('pantry_action',{action:'add_item',location_id:lid,name:n.value.trim(),notes:t?t.value.trim():'',expires:e?e.value:''}); n.value=''; if(t)t.value=''; if(e)e.value=''; }
    },

    addLocation() {
        const el=document.getElementById('pa-new-loc');
        if(el&&el.value.trim()){socket.emit('pantry_action',{action:'add_location',name:el.value.trim()});el.value='';}
    },

    editItem(id, name, notes) {
        const newName = prompt('Item name:', name);
        if (newName === null) return;
        const newNotes = prompt('Notes (or leave blank):', notes);
        if (newNotes === null) return;
        socket.emit('pantry_action', { action: 'edit_item', item_id: id, name: newName.trim(), notes: newNotes.trim() });
    }
};
