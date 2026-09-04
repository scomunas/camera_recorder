let globalConfig = null;
let allTimelineFiles = [];      // All files for the selected camera
let dayFiles = [];              // Files filtered for the selected date
let currentFileIndex = -1;

const SECONDS_IN_DAY = 86400;   // 24 * 60 * 60

document.addEventListener('DOMContentLoaded', () => {
    initTabs();
    loadConfiguration();
});

// Handle Tab Switching
function initTabs() {
    const tabs = document.querySelectorAll('.tab');
    const sections = document.querySelectorAll('.section');

    tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            tabs.forEach(t => t.classList.remove('active'));
            sections.forEach(s => s.classList.remove('active'));

            tab.classList.add('active');
            const targetId = tab.getAttribute('data-tab');
            document.getElementById(targetId).classList.add('active');
        });
    });
}

// Load configuration and initialize cameras
async function loadConfiguration() {
    try {
        const baseUrl = getBaseUrl();
        const response = await fetch(`${baseUrl}/api/config`);
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        globalConfig = await response.json();
        initializeCameras(globalConfig);
        populateHistoricalSelector(globalConfig);
        initHistoricalEvents();
    } catch (error) {
        console.error('Failed to load configuration:', error);
        showError('Could not load camera configuration. Is the backend server running?');
    }
}

function getBaseUrl() {
    return window.location.protocol + '//' + window.location.hostname + ':5000';
}

function initializeCameras(config) {
    const grid = document.getElementById('cameraGrid');
    grid.innerHTML = '';
    
    const { server, cameras } = config;
    
    cameras.forEach(camera => {
        const streamUrl = `http://${server.ip}:${server.http_port}/stream.html?src=${camera.id}`;
        
        const card = document.createElement('div');
        card.className = 'camera-card';
        
        card.innerHTML = `
            <div class="camera-header">
                <div class="camera-title">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"></path><circle cx="12" cy="13" r="4"></circle></svg>
                    ${camera.name}
                </div>
                <div class="camera-status">
                    <span class="status-dot"></span> Online
                </div>
            </div>
            <div class="video-container">
                <iframe 
                    src="${streamUrl}" 
                    allowfullscreen 
                    allow="autoplay; fullscreen"
                    title="${camera.name} Stream"
                ></iframe>
            </div>
        `;
        
        grid.appendChild(card);
    });
}

function showError(message) {
    const grid = document.getElementById('cameraGrid');
    grid.innerHTML = `
        <div class="placeholder" style="grid-column: 1 / -1;">
            <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="var(--danger)" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg>
            <p style="color: var(--danger)">${message}</p>
        </div>
    `;
}

// ============================================
// Historical Section Logic
// ============================================

function populateHistoricalSelector(config) {
    const select = document.getElementById('historyCameraSelect');
    config.cameras.forEach(cam => {
        const option = document.createElement('option');
        option.value = cam.id;
        option.textContent = cam.name;
        select.appendChild(option);
    });
}

function initHistoricalEvents() {
    const select = document.getElementById('historyCameraSelect');
    const datePicker = document.getElementById('historyDatePicker');
    const timelineBar = document.getElementById('timelineBar');
    const video = document.getElementById('historicalVideo');

    select.addEventListener('change', (e) => {
        const camId = e.target.value;
        if (camId) {
            loadAllFiles(camId);
        } else {
            resetHistorical();
        }
    });

    datePicker.addEventListener('change', () => {
        filterAndRenderDay();
    });

    // Timeline bar interaction: click and drag
    let isDragging = false;

    timelineBar.addEventListener('mousedown', (e) => {
        isDragging = true;
        handleTimelineClick(e);
    });

    document.addEventListener('mousemove', (e) => {
        if (isDragging) {
            handleTimelineClick(e);
        }
    });

    document.addEventListener('mouseup', () => {
        if (isDragging) {
            isDragging = false;
        }
    });

    // Touch support
    timelineBar.addEventListener('touchstart', (e) => {
        isDragging = true;
        handleTimelineClick(e.touches[0]);
        e.preventDefault();
    });

    document.addEventListener('touchmove', (e) => {
        if (isDragging) {
            handleTimelineClick(e.touches[0]);
        }
    });

    document.addEventListener('touchend', () => {
        isDragging = false;
    });

    // Auto-advance to next file when current ends
    video.addEventListener('ended', () => {
        if (currentFileIndex >= 0 && currentFileIndex + 1 < dayFiles.length) {
            playFileByIndex(currentFileIndex + 1);
        }
    });
}

function resetHistorical() {
    document.getElementById('videoOverlay').style.display = 'flex';
    document.getElementById('videoOverlay').textContent = 'Select a camera to load history';
    document.getElementById('historyControls').style.display = 'none';
    document.getElementById('historicalVideo').src = '';
    allTimelineFiles = [];
    dayFiles = [];
    currentFileIndex = -1;
}

async function loadAllFiles(camId) {
    const overlay = document.getElementById('videoOverlay');
    overlay.style.display = 'flex';
    overlay.textContent = 'Loading recordings...';

    try {
        const response = await fetch(`${getBaseUrl()}/api/timeline?cam_name=${camId}`);
        if (!response.ok) throw new Error('Timeline fetch failed');
        
        const data = await response.json();
        allTimelineFiles = data.files;

        if (allTimelineFiles.length === 0) {
            overlay.textContent = 'No historical recordings found for this camera.';
            document.getElementById('historyControls').style.display = 'none';
            return;
        }

        // Show controls
        document.getElementById('historyControls').style.display = 'flex';

        // Populate date picker with available dates and set to latest
        populateAvailableDates();

    } catch (e) {
        console.error(e);
        overlay.textContent = 'Error connecting to backend API. Is server.py running?';
    }
}

function populateAvailableDates() {
    const datePicker = document.getElementById('historyDatePicker');

    // Find min/max dates
    const dates = allTimelineFiles.map(f => {
        const d = new Date(f.timestamp * 1000);
        return d.toISOString().split('T')[0]; // YYYY-MM-DD
    });
    const uniqueDates = [...new Set(dates)];

    if (uniqueDates.length > 0) {
        datePicker.min = uniqueDates[0];
        datePicker.max = uniqueDates[uniqueDates.length - 1];
        datePicker.value = uniqueDates[uniqueDates.length - 1]; // Default to latest day
    }

    filterAndRenderDay();
}

function filterAndRenderDay() {
    const datePicker = document.getElementById('historyDatePicker');
    const selectedDate = datePicker.value;

    if (!selectedDate) return;

    // Filter files for this specific day
    dayFiles = allTimelineFiles.filter(f => {
        const d = new Date(f.timestamp * 1000);
        return d.toISOString().split('T')[0] === selectedDate;
    });

    renderTimelineMarkers();

    if (dayFiles.length > 0) {
        document.getElementById('videoOverlay').style.display = 'none';
        document.getElementById('timelineTimeDisplay').textContent = 'Click on the timeline to play';
    } else {
        document.getElementById('videoOverlay').style.display = 'flex';
        document.getElementById('videoOverlay').textContent = 'No recordings for this date.';
    }

    currentFileIndex = -1;
}

function renderTimelineMarkers() {
    const track = document.getElementById('timelineTrack');
    track.innerHTML = '';

    dayFiles.forEach((file, index) => {
        const dt = new Date(file.timestamp * 1000);
        const secondsInDay = dt.getHours() * 3600 + dt.getMinutes() * 60 + dt.getSeconds();
        const pct = (secondsInDay / SECONDS_IN_DAY) * 100;

        const marker = document.createElement('div');
        marker.className = 'timeline-marker';
        marker.style.left = `${pct}%`;
        marker.title = dt.toLocaleTimeString('es-ES');
        marker.dataset.index = index;
        track.appendChild(marker);
    });
}

function handleTimelineClick(e) {
    const bar = document.getElementById('timelineBar');
    const rect = bar.getBoundingClientRect();
    let x = e.clientX - rect.left;
    x = Math.max(0, Math.min(x, rect.width));
    const pct = x / rect.width;
    const secondsInDay = Math.floor(pct * SECONDS_IN_DAY);

    // Update cursor position
    const cursor = document.getElementById('timelineCursor');
    cursor.style.display = 'block';
    cursor.style.left = `${pct * 100}%`;

    // Update time display
    const h = Math.floor(secondsInDay / 3600);
    const m = Math.floor((secondsInDay % 3600) / 60);
    const s = secondsInDay % 60;
    const timeStr = `${h.toString().padStart(2, '0')}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
    document.getElementById('timelineTimeDisplay').textContent = timeStr;

    // Find the closest file at or before this time
    let targetIndex = -1;
    for (let i = 0; i < dayFiles.length; i++) {
        const dt = new Date(dayFiles[i].timestamp * 1000);
        const fileSec = dt.getHours() * 3600 + dt.getMinutes() * 60 + dt.getSeconds();
        if (fileSec <= secondsInDay) {
            targetIndex = i;
        } else {
            break;
        }
    }

    if (targetIndex >= 0 && targetIndex !== currentFileIndex) {
        playFileByIndex(targetIndex);
    }
}

function playFileByIndex(index) {
    if (index >= dayFiles.length) return;
    
    const file = dayFiles[index];
    const video = document.getElementById('historicalVideo');
    const videoUrl = `${getBaseUrl()}/api/video/${file.filepath}`;

    // Update active marker
    document.querySelectorAll('.timeline-marker').forEach(m => m.classList.remove('active'));
    const activeMarker = document.querySelector(`.timeline-marker[data-index="${index}"]`);
    if (activeMarker) activeMarker.classList.add('active');

    // Update cursor to marker position
    if (activeMarker) {
        const cursor = document.getElementById('timelineCursor');
        cursor.style.display = 'block';
        cursor.style.left = activeMarker.style.left;
    }

    // Update time display
    const dt = new Date(file.timestamp * 1000);
    document.getElementById('timelineTimeDisplay').textContent = dt.toLocaleTimeString('es-ES');

    document.getElementById('videoOverlay').style.display = 'none';

    currentFileIndex = index;
    video.src = videoUrl;
    video.load();
    video.play().catch(e => console.log('Autoplay prevented:', e));
}
