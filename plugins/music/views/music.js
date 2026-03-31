// Music player view renderer
GlaDOS.views.music = {
    renderCard(container) {
        const d = GlaDOS.dashboardData.music || {};
        const track = d.track || '';
        const artist = d.artist || '';
        const playing = d.is_playing || false;
        const device = d.device || '';

        const nowPlaying = track
            ? `<div class="music-track-name">${GlaDOS.esc(track)}</div>
               <div class="music-track-artist">${GlaDOS.esc(artist)}</div>`
            : '<div class="music-empty">Nothing playing</div>';

        const deviceLabel = device
            ? `<div class="music-device"><i class="icon-speaker"></i> ${GlaDOS.esc(device)}</div>`
            : '';

        container.innerHTML = `
            <div class="dash-card-header">
                <span class="dash-card-icon"><i class="icon-music"></i></span> Music
                <span class="dash-card-badge">${playing ? '<i class="icon-play"></i>' : '<i class="icon-square"></i>'}</span>
            </div>
            <div class="dash-card-body">
                ${nowPlaying}
                <div style="display:flex;gap:6px;margin-top:8px">
                    <button class="tq-btn" onclick="socket.emit('music_action',{action:'PREVIOUS'})" style="flex:1;font-size:1.1rem;padding:8px"><i class="icon-skip-back"></i></button>
                    <button class="tq-btn" onclick="socket.emit('music_action',{action:'${playing ? 'PAUSE' : 'RESUME'}'})" style="flex:1;font-size:1.1rem;padding:8px">${playing ? '<i class="icon-pause"></i>' : '<i class="icon-play"></i>'}</button>
                    <button class="tq-btn" onclick="socket.emit('music_action',{action:'SKIP'})" style="flex:1;font-size:1.1rem;padding:8px"><i class="icon-skip-forward"></i></button>
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
                    <div class="music-now-track">${GlaDOS.esc(track)}</div>
                    <div class="music-now-artist">${GlaDOS.esc(artist)}</div>
                </div>`;
        } else {
            nowPlaying = '<div class="music-empty" style="text-align:center;padding:20px">Nothing playing</div>';
        }

        const controls = `
            <div style="display:flex;gap:10px;justify-content:center;margin:20px 0">
                <button class="tq-btn" onclick="socket.emit('music_action',{action:'PREVIOUS'})" style="font-size:1.3rem;padding:12px 20px"><i class="icon-skip-back"></i></button>
                <button class="tq-btn" onclick="socket.emit('music_action',{action:'${playing ? 'PAUSE' : 'RESUME'}'})" style="font-size:1.3rem;padding:12px 24px">${playing ? '<i class="icon-pause"></i>' : '<i class="icon-play"></i>'}</button>
                <button class="tq-btn" onclick="socket.emit('music_action',{action:'SKIP'})" style="font-size:1.3rem;padding:12px 20px"><i class="icon-skip-forward"></i></button>
                <button class="tq-btn" onclick="socket.emit('music_action',{action:'STOP'})" style="font-size:1.3rem;padding:12px 20px"><i class="icon-square"></i></button>
            </div>`;

        let deviceHtml = '';
        if (devices.length) {
            deviceHtml = '<div style="margin-top:20px"><div class="recipe-section-label">Devices</div>';
            devices.forEach(d => {
                const active = d.active ? ' music-device-active' : '';
                deviceHtml += `<div class="rs-item${active}" onclick="socket.emit('music_action',{action:'SWITCH_DEVICE',device:'${GlaDOS.esc(d.name).replace(/'/g, "\\'")}'})">
                    <div class="rs-info">
                        <div class="rs-title"><i class="icon-speaker"></i> ${GlaDOS.esc(d.name)}</div>
                        <div class="rs-meta">${GlaDOS.esc(d.type)}${d.active ? ' — Active' : ''}</div>
                    </div>
                </div>`;
            });
            deviceHtml += '</div>';
        }

        container.innerHTML = `<div class="view-title">Music</div>${nowPlaying}${controls}${deviceHtml}`;
    }
};
