// Music player view renderer
GlaDOS.views.music = {
    renderCard(container) {
        const d = GlaDOS.dashboardData.music || {};
        const track = d.track || '';
        const artist = d.artist || '';
        const playing = d.is_playing || false;
        const device = d.device || '';

        const nowPlaying = track
            ? `<div style="font-size:0.85rem;color:#e0e0e0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${GlaDOS.esc(track)}</div>
               <div style="font-size:0.75rem;color:#888;margin-top:2px">${GlaDOS.esc(artist)}</div>`
            : '<div style="color:#555;font-size:0.8rem">Nothing playing</div>';

        const deviceLabel = device
            ? `<div style="font-size:0.65rem;color:#555;margin-top:6px">&#128266; ${GlaDOS.esc(device)}</div>`
            : '';

        container.innerHTML = `
            <div class="dash-card-header">
                <span class="dash-card-icon">&#127925;</span> Music
                <span class="dash-card-badge">${playing ? '&#9654;' : '&#9724;'}</span>
            </div>
            <div class="dash-card-body">
                ${nowPlaying}
                <div style="display:flex;gap:6px;margin-top:8px">
                    <button class="tq-btn" onclick="socket.emit('music_action',{action:'PREVIOUS'})" style="flex:1;font-size:1.1rem;padding:8px">&#9198;</button>
                    <button class="tq-btn" onclick="socket.emit('music_action',{action:'${playing ? 'PAUSE' : 'RESUME'}'})" style="flex:1;font-size:1.1rem;padding:8px">${playing ? '&#9208;' : '&#9654;'}</button>
                    <button class="tq-btn" onclick="socket.emit('music_action',{action:'SKIP'})" style="flex:1;font-size:1.1rem;padding:8px">&#9197;</button>
                </div>
                ${deviceLabel}
            </div>`;
    },

    render(container, data) {
        const devices = data.devices || [];
        const track = data.track || '';
        const artist = data.artist || '';
        const playing = data.is_playing || false;

        let nowPlaying = '';
        if (track) {
            nowPlaying = `
                <div style="text-align:center;margin:20px 0">
                    <div style="font-size:1.2rem;color:#e0e0e0">${GlaDOS.esc(track)}</div>
                    <div style="font-size:0.9rem;color:#888;margin-top:4px">${GlaDOS.esc(artist)}</div>
                </div>`;
        } else {
            nowPlaying = '<div style="text-align:center;color:#555;padding:20px">Nothing playing</div>';
        }

        const controls = `
            <div style="display:flex;gap:10px;justify-content:center;margin:20px 0">
                <button class="tq-btn" onclick="socket.emit('music_action',{action:'PREVIOUS'})" style="font-size:1.3rem;padding:12px 20px">&#9198;</button>
                <button class="tq-btn" onclick="socket.emit('music_action',{action:'${playing ? 'PAUSE' : 'RESUME'}'})" style="font-size:1.3rem;padding:12px 24px">${playing ? '&#9208;' : '&#9654;'}</button>
                <button class="tq-btn" onclick="socket.emit('music_action',{action:'SKIP'})" style="font-size:1.3rem;padding:12px 20px">&#9197;</button>
                <button class="tq-btn" onclick="socket.emit('music_action',{action:'STOP'})" style="font-size:1.3rem;padding:12px 20px">&#9724;</button>
            </div>`;

        let deviceHtml = '';
        if (devices.length) {
            deviceHtml = '<div style="margin-top:20px"><div class="recipe-section-label">Devices</div>';
            devices.forEach(d => {
                const active = d.active ? ' style="border-color:#ff6600;color:#ff6600"' : '';
                deviceHtml += `<div class="rs-item"${active} onclick="socket.emit('music_action',{action:'SWITCH_DEVICE',device:'${GlaDOS.esc(d.name).replace(/'/g, "\\'")}'})">
                    <div class="rs-info">
                        <div class="rs-title">&#128266; ${GlaDOS.esc(d.name)}</div>
                        <div class="rs-meta">${GlaDOS.esc(d.type)}${d.active ? ' — Active' : ''}</div>
                    </div>
                </div>`;
            });
            deviceHtml += '</div>';
        }

        container.innerHTML = `<div class="view-title">Music</div>${nowPlaying}${controls}${deviceHtml}`;
    }
};
