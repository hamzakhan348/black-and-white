/**
 * Frontend script for the colorization web app.
 * Handles file selection, upload, polling, UI updates, dark mode, batch download.
 */

// -------------------- DOM refs --------------------
const fileInput = document.getElementById('fileInput');
const dropArea = document.getElementById('dropArea');
const processBtn = document.getElementById('processBtn');
const fileList = document.getElementById('fileList');
const resultsGallery = document.getElementById('resultsGallery');
const errorMsg = document.getElementById('errorMsg');
const globalProgressBar = document.querySelector('#globalProgress .progress-fill');
const globalProgressText = document.querySelector('#globalProgress .progress-text');
const globalProgressContainer = document.getElementById('globalProgress');
const renderFactorSlider = document.getElementById('renderFactor');
const factorDisplay = document.getElementById('factorDisplay');
const autoStartCheck = document.getElementById('autoStart');
const themeToggle = document.getElementById('themeToggle');
const batchActions = document.getElementById('batchActions');
const downloadAllBtn = document.getElementById('downloadAllBtn');
const clearAllBtn = document.getElementById('clearAllBtn');

// -------------------- State --------------------
let selectedFiles = [];                // Array of File objects
let jobStatuses = {};                 // filename -> { jobId, status, progress, resultBase64, downloadUrl, error }
let pollingIntervals = {};            // jobId -> interval ID
let isProcessing = false;
let darkMode = localStorage.getItem('darkMode') === 'true';

// -------------------- Theme toggle --------------------
function toggleDarkMode() {
    darkMode = !darkMode;
    document.body.classList.toggle('dark', darkMode);
    localStorage.setItem('darkMode', darkMode);
    themeToggle.innerHTML = darkMode ? '<i class="fas fa-sun"></i>' : '<i class="fas fa-moon"></i>';
}

themeToggle.addEventListener('click', toggleDarkMode);
if (darkMode) {
    document.body.classList.add('dark');
    themeToggle.innerHTML = '<i class="fas fa-sun"></i>';
}

// -------------------- File selection UI --------------------
function renderFileList() {
    if (selectedFiles.length === 0) {
        fileList.innerHTML = '<div class="file-item empty">No files selected</div>';
        return;
    }
    let html = '';
    selectedFiles.forEach((file) => {
        const status = jobStatuses[file.name] ? jobStatuses[file.name].status : 'queued';
        const progress = jobStatuses[file.name] ? jobStatuses[file.name].progress : 0;
        let statusText = '';
        let statusClass = '';
        let progressHtml = '';
        if (status === 'queued') {
            statusText = '⏳ Queued';
            statusClass = 'queued';
        } else if (status === 'processing') {
            statusText = `🔄 Processing ${progress}%`;
            statusClass = 'processing';
            progressHtml = `<div class="progress-indicator"><div class="fill" style="width:${progress}%"></div></div>`;
        } else if (status === 'completed') {
            statusText = '✅ Done';
            statusClass = 'done';
        } else if (status === 'failed') {
            statusText = '❌ Failed';
            statusClass = 'failed';
        } else if (status === 'cancelled') {
            statusText = '🚫 Cancelled';
            statusClass = 'failed';
        } else {
            statusText = status;
        }
        html += `
            <div class="file-item" data-filename="${file.name}">
                <span class="name" title="${file.name}">${file.name}</span>
                <span class="status ${statusClass}">
                    ${statusText}
                    ${progressHtml}
                </span>
            </div>
        `;
    });
    fileList.innerHTML = html;
}

function updateGlobalProgress() {
    const total = selectedFiles.length;
    if (total === 0) return;
    let completed = 0;
    let failed = 0;
    let totalProgress = 0;
    selectedFiles.forEach(f => {
        const job = jobStatuses[f.name];
        if (job) {
            if (job.status === 'completed') completed++;
            if (job.status === 'failed') failed++;
            totalProgress += job.progress || 0;
        }
    });
    const avg = total / total;
    const pct = Math.round(avg);
    globalProgressBar.style.width = pct + '%';
    globalProgressText.textContent = pct + '%';
    globalProgressContainer.style.display = 'block';
    if (completed + failed === total && total > 0) {
        // All done
        setTimeout(() => {
            globalProgressContainer.style.display = 'none';
        }, 2000);
    }
}

// -------------------- File input / drag & drop --------------------
function handleFiles(files) {
    selectedFiles = Array.from(files);
    // Reset job statuses for new files
    selectedFiles.forEach(f => {
        if (!jobStatuses[f.name]) {
            jobStatuses[f.name] = { status: 'queued', progress: 0, resultBase64: null, downloadUrl: null, error: null };
        } else {
            // Reset if already exists
            jobStatuses[f.name].status = 'queued';
            jobStatuses[f.name].progress = 0;
            jobStatuses[f.name].resultBase64 = null;
            jobStatuses[f.name].downloadUrl = null;
            jobStatuses[f.name].error = null;
        }
    });
    renderFileList();
    processBtn.disabled = false;
    errorMsg.textContent = '';
    // If auto-start is enabled, trigger processing immediately
    if (autoStartCheck.checked && selectedFiles.length > 0) {
        startProcessing();
    }
}

fileInput.addEventListener('change', (e) => {
    if (e.target.files.length > 0) {
        handleFiles(e.target.files);
    } else {
        selectedFiles = [];
        renderFileList();
        processBtn.disabled = true;
    }
});

dropArea.addEventListener('dragover', (e) => {
    e.preventDefault();
    dropArea.style.borderColor = 'var(--primary)';
    dropArea.style.background = 'var(--bg-upload)';
});
dropArea.addEventListener('dragleave', () => {
    dropArea.style.borderColor = 'var(--border-color)';
    dropArea.style.background = 'var(--bg-container)';
});
dropArea.addEventListener('drop', (e) => {
    e.preventDefault();
    dropArea.style.borderColor = 'var(--border-color)';
    dropArea.style.background = 'var(--bg-container)';
    if (e.dataTransfer.files.length > 0) {
        fileInput.files = e.dataTransfer.files;
        fileInput.dispatchEvent(new Event('change'));
    }
});

// Render factor slider
renderFactorSlider.addEventListener('input', () => {
    factorDisplay.textContent = renderFactorSlider.value;
});

// Process button
processBtn.addEventListener('click', startProcessing);

// -------------------- Main processing --------------------
function startProcessing() {
    if (selectedFiles.length === 0) return;

    // Reset any previous results
    resultsGallery.innerHTML = '';
    errorMsg.textContent = '';
    // Clear old polling intervals
    for (const jobId in pollingIntervals) {
        clearInterval(pollingIntervals[jobId]);
        delete pollingIntervals[jobId];
    }

    processBtn.disabled = true;
    globalProgressContainer.style.display = 'block';
    globalProgressBar.style.width = '0%';
    globalProgressText.textContent = '0%';
    isProcessing = true;

    // Prepare FormData
    const formData = new FormData();
    selectedFiles.forEach(f => formData.append('images', f));
    formData.append('render_factor', renderFactorSlider.value);

    // Upload
    fetch('/api/upload', {
        method: 'POST',
        body: formData
    })
    .then(response => response.json())
    .then(data => {
        if (data.error) {
            throw new Error(data.error);
        }
        const jobIds = data.job_ids;
        if (!jobIds || jobIds.length === 0) {
            throw new Error('No jobs created.');
        }
        // Map job IDs to filenames (maintain order)
        jobIds.forEach((jobId, idx) => {
            const fileName = selectedFiles[idx] ? selectedFiles[idx].name : 'unknown';
            jobStatuses[fileName].jobId = jobId;
            // Start polling
            pollJob(jobId, fileName);
        });
        // Show batch actions
        batchActions.style.display = 'block';
    })
    .catch(err => {
        errorMsg.textContent = 'Upload failed: ' + err.message;
        processBtn.disabled = false;
        globalProgressContainer.style.display = 'none';
        isProcessing = false;
    });
}

function pollJob(jobId, fileName) {
    const interval = setInterval(() => {
        fetch(`/api/status/${jobId}`)
        .then(res => res.json())
        .then(data => {
            if (data.error) {
                clearInterval(interval);
                jobStatuses[fileName].status = 'failed';
                jobStatuses[fileName].error = data.error;
                renderFileList();
                updateGlobalProgress();
                checkAllDone();
                return;
            }
            // Update status
            jobStatuses[fileName].status = data.status;
            jobStatuses[fileName].progress = data.progress || 0;
            if (data.result_base64) {
                jobStatuses[fileName].resultBase64 = data.result_base64;
                jobStatuses[fileName].downloadUrl = `data:image/jpeg;base64,${data.result_base64}`;
                // Also store the server download URL if provided
                if (data.download_url) {
                    jobStatuses[fileName].serverDownloadUrl = data.download_url;
                }
                // Add to gallery if not already added
                addResultToGallery(fileName, data.result_base64);
            }
            if (data.error) {
                jobStatuses[fileName].error = data.error;
            }
            renderFileList();
            updateGlobalProgress();

            if (data.status === 'completed' || data.status === 'failed') {
                clearInterval(interval);
                delete pollingIntervals[jobId];
                checkAllDone();
            }
        })
        .catch(err => {
            clearInterval(interval);
            delete pollingIntervals[jobId];
            jobStatuses[fileName].status = 'failed';
            jobStatuses[fileName].error = err.message;
            renderFileList();
            updateGlobalProgress();
            checkAllDone();
        });
    }, 1000);
    pollingIntervals[jobId] = interval;
}

function checkAllDone() {
    const allDone = selectedFiles.every(f => {
        const st = jobStatuses[f.name];
        return st && (st.status === 'completed' || st.status === 'failed');
    });
    if (allDone) {
        isProcessing = false;
        processBtn.disabled = false;
        // Hide global progress after a moment
        setTimeout(() => {
            if (globalProgressBar.style.width === '100%') {
                globalProgressContainer.style.display = 'none';
            }
        }, 2000);
        // Show batch actions
        batchActions.style.display = 'block';
    }
}

// -------------------- Results gallery --------------------
function addResultToGallery(fileName, base64Data) {
    // Avoid duplicates
    if (document.querySelector(`.result-card[data-filename="${fileName}"]`)) return;

    const card = document.createElement('div');
    card.className = 'result-card';
    card.dataset.filename = fileName;

    // Get original image data URL from file object
    let originalDataUrl = '';
    const fileObj = selectedFiles.find(f => f.name === fileName);
    if (fileObj && fileObj._dataUrl) {
        originalDataUrl = fileObj._dataUrl;
    } else {
        // Fallback: use a placeholder or generate from file
        originalDataUrl = '#';
    }

    const imgSrc = `data:image/jpeg;base64,${base64Data}`;
    const downloadUrl = jobStatuses[fileName]?.serverDownloadUrl || imgSrc;

    card.innerHTML = `
        <div class="comparison">
            <div class="after">
                <img src="${imgSrc}" alt="Colorized" loading="lazy">
            </div>
            <div class="before" style="width:50%;">
                <img src="${originalDataUrl}" alt="Original" loading="lazy">
            </div>
            <div class="slider-handle">⟷</div>
        </div>
        <div class="card-footer">
            <span class="filename" title="${fileName}">${fileName}</span>
            <div class="actions">
                <a href="${downloadUrl}" download="colorized_${fileName}" class="download-btn">
                    <i class="fas fa-download"></i>
                </a>
                <button class="delete-btn" data-filename="${fileName}">
                    <i class="fas fa-trash"></i>
                </button>
            </div>
        </div>
    `;

    resultsGallery.appendChild(card);

    // Set up slider for this card
    const handle = card.querySelector('.slider-handle');
    const beforeDiv = card.querySelector('.before');
    const container = card.querySelector('.comparison');
    let isDragging = false;

    const setPosition = (x) => {
        const rect = container.getBoundingClientRect();
        let pct = ((x - rect.left) / rect.width) * 100;
        pct = Math.min(100, Math.max(0, pct));
        beforeDiv.style.width = pct + '%';
        handle.style.left = pct + '%';
    };

    handle.addEventListener('mousedown', (e) => {
        isDragging = true;
        e.preventDefault();
    });
    handle.addEventListener('touchstart', (e) => {
        isDragging = true;
        e.preventDefault();
    });
    document.addEventListener('mousemove', (e) => {
        if (!isDragging) return;
        setPosition(e.clientX);
    });
    document.addEventListener('touchmove', (e) => {
        if (!isDragging) return;
        const touch = e.touches[0];
        setPosition(touch.clientX);
    }, { passive: true });
    document.addEventListener('mouseup', () => { isDragging = false; });
    document.addEventListener('touchend', () => { isDragging = false; });

    // Click on container to move slider
    container.addEventListener('click', (e) => {
        if (!e.target.closest('.slider-handle')) {
            setPosition(e.clientX);
        }
    });

    // Delete button
    card.querySelector('.delete-btn').addEventListener('click', () => {
        const filename = card.dataset.filename;
        const job = jobStatuses[filename];
        if (job && job.jobId) {
            // Delete job via API
            fetch(`/api/delete/${job.jobId}`, { method: 'DELETE' })
            .then(() => {
                // Remove from UI
                card.remove();
                delete jobStatuses[filename];
                renderFileList();
                // Remove from selectedFiles?
                const idx = selectedFiles.findIndex(f => f.name === filename);
                if (idx !== -1) selectedFiles.splice(idx, 1);
                if (selectedFiles.length === 0) {
                    batchActions.style.display = 'none';
                    processBtn.disabled = true;
                }
            })
            .catch(err => {
                errorMsg.textContent = 'Failed to delete: ' + err.message;
            });
        }
    });
}

// -------------------- Batch actions --------------------
downloadAllBtn.addEventListener('click', async () => {
    // Collect all completed results
    const completed = selectedFiles.filter(f => {
        const job = jobStatuses[f.name];
        return job && job.status === 'completed' && job.resultBase64;
    });
    if (completed.length === 0) {
        errorMsg.textContent = 'No completed images to download.';
        return;
    }
    // Use JSZip to create a ZIP
    try {
        const zip = new JSZip();
        for (const file of completed) {
            const base64 = jobStatuses[file.name].resultBase64;
            // Convert base64 to binary
            const binary = atob(base64);
            const array = new Uint8Array(binary.length);
            for (let i = 0; i < binary.length; i++) {
                array[i] = binary.charCodeAt(i);
            }
            zip.file(`colorized_${file.name}`, array);
        }
        const blob = await zip.generateAsync({ type: 'blob' });
        saveAs(blob, 'colorized_images.zip');
    } catch (err) {
        errorMsg.textContent = 'Failed to create ZIP: ' + err.message;
    }
});

clearAllBtn.addEventListener('click', async () => {
    // Delete all completed/failed jobs
    const toDelete = selectedFiles.filter(f => {
        const job = jobStatuses[f.name];
        return job && (job.status === 'completed' || job.status === 'failed');
    });
    for (const f of toDelete) {
        const job = jobStatuses[f.name];
        if (job && job.jobId) {
            try {
                await fetch(`/api/delete/${job.jobId}`, { method: 'DELETE' });
            } catch (e) {}
        }
        delete jobStatuses[f.name];
        // Remove from selectedFiles
        const idx = selectedFiles.findIndex(item => item.name === f.name);
        if (idx !== -1) selectedFiles.splice(idx, 1);
    }
    // Clear gallery
    resultsGallery.innerHTML = '';
    renderFileList();
    batchActions.style.display = 'none';
    processBtn.disabled = true;
    errorMsg.textContent = '';
});

// -------------------- Helpers to store data URLs --------------------
// When files are selected, read data URL for original previews
fileInput.addEventListener('change', function(e) {
    if (fileInput.files.length > 0) {
        const files = fileInput.files;
        for (let i = 0; i < files.length; i++) {
            const file = files[i];
            if (!file._dataUrl) {
                const reader = new FileReader();
                reader.onload = (ev) => {
                    file._dataUrl = ev.target.result;
                };
                reader.readAsDataURL(file);
            }
        }
    }
});

// Also for drag/drop, we already handle that via the change event.

// -------------------- Initialization --------------------
renderFileList();
