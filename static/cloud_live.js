// Weighbridge Cloud Live Controller
// Complete Feature Parity with Local Weighbridge Application

let currentLiveWeight = 0;
let isScaleStable = true;
let currentMode = 'double'; // 'double' | 'single'
let currentStage = '1ST_WEIGHT'; // '1ST_WEIGHT' | '2ND_WEIGHT'
let firstWeightVal = 0;
let autoPilotEnabled = false;
let autoPilotTriggered = false;
let autoPilotLastAttempt = 0;
let initialNextTicketNo = '';
let livePollFailures = 0;
let isRefreshingCameras = false;

// Tariff Table (Tamil Nadu Weighbridge standard rates)
const TARIFF_RATES = {
    "🚚 Lorry / லாரி": { loaded: 100, empty: 70 },
    "🚐 Tempo / டெம்போ": { loaded: 70, empty: 50 },
    "🛺 Auto / ஆட்டோ": { loaded: 50, empty: 30 },
    "🚜 Tractor / டிராக்டர்": { loaded: 80, empty: 50 },
    "🚛 Tipper / டிப்பர்": { loaded: 120, empty: 80 },
    "🚗 Car / Van / இதர": { loaded: 50, empty: 40 }
};

// 1-second polling loop for live scale reading via SWR cache
async function pollLiveWeight() {
    try {
        const res = await fetch('/api/live-weight');
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();

        currentLiveWeight = Number(data.weight || 0);
        isScaleStable = (data.is_stable !== false);

        // Update hidden form field
        const formCurrentWeightEl = document.getElementById('formCurrentWeight');
        if (formCurrentWeightEl) formCurrentWeightEl.value = currentLiveWeight;

        // Update Big Live Scale Reading Display
        const liveScaleInput = document.getElementById('liveScaleInput');
        if (liveScaleInput) {
            liveScaleInput.value = currentLiveWeight.toLocaleString('en-IN') + ' Kg';
        }

        // Update Stability Badge & Save Button
        const badgeEl = document.getElementById('liveScaleBadge');
        const badgeText = document.getElementById('liveScaleStatusText');
        const movingWarning = document.getElementById('movingWarningPrompt');
        const btnSave = document.getElementById('btnSaveWeigh');

        if (badgeEl && badgeText) {
            if (isScaleStable) {
                badgeEl.className = 'text-xs font-black px-2.5 py-1 rounded-full bg-emerald-100 text-emerald-800 flex items-center gap-1.5 shrink-0';
                badgeText.textContent = 'STABLE';
                if (movingWarning) movingWarning.classList.add('hidden');
                if (btnSave) {
                    btnSave.disabled = false;
                    btnSave.className = 'w-full sm:w-auto px-10 py-4 sm:py-5 rounded-2xl text-xl sm:text-2xl font-black text-white bg-emerald-600 hover:bg-emerald-700 active:scale-98 shadow-xl transition flex items-center justify-center gap-3 cursor-pointer';
                }
            } else {
                badgeEl.className = 'text-xs font-black px-2.5 py-1 rounded-full bg-amber-100 text-amber-800 flex items-center gap-1.5 shrink-0';
                badgeText.textContent = 'MOVING';
                if (movingWarning) movingWarning.classList.remove('hidden');
                if (btnSave) {
                    btnSave.disabled = false; // allow operator override if needed, but warning shown
                }
            }
        }

        // Update Home PC Edge Gateway status badge in header
        const edgeDot = document.getElementById('edgeStatusDot');
        const edgeText = document.getElementById('edgeStatusText');
        if (edgeDot && edgeText) {
            if (data.edge_online) {
                livePollFailures = 0;
                edgeDot.className = 'w-2.5 h-2.5 rounded-full bg-emerald-500';
                edgeText.textContent = 'Home PC Edge: Online (Tailscale)';
                edgeText.className = 'text-emerald-800 font-bold';
            } else {
                livePollFailures++;
                if (data.status_label === 'HOME_PC_OFFLINE' || livePollFailures >= 3) {
                    edgeDot.className = 'w-2.5 h-2.5 rounded-full bg-amber-500 animate-pulse';
                    edgeText.textContent = 'Home PC: Reconnecting...';
                    edgeText.className = 'text-amber-800 font-bold';
                }
            }
        }

        // Auto-Pilot Trigger: Vehicle stably parked (> 500 kg)
        if (autoPilotEnabled && isScaleStable && currentLiveWeight >= 500) {
            const vehNo = document.getElementById('vehicle_no')?.value.trim();
            const nowMs = Date.now();
            if (!vehNo && (!autoPilotTriggered || (nowMs - autoPilotLastAttempt > 15000))) {
                autoPilotTriggered = true;
                autoPilotLastAttempt = nowMs;
                runGeminiFusionANPR();
            }
        } else if (currentLiveWeight < 200) {
            // Reset trigger when scale is empty
            autoPilotTriggered = false;
            autoPilotLastAttempt = 0;
        }

        // Recalculate Net Weight in real time
        recalcNetWeight();

    } catch (err) {
        console.warn('Live scale poll error:', err);
    } finally {
        const isWeighPage = Boolean(document.getElementById('weighForm'));
        setTimeout(pollLiveWeight, isWeighPage ? 1000 : 10000);
    }
}

// Weighment Mode Toggle: Double vs Single
function toggleWeighMode(mode) {
    currentMode = mode;
    const btnDouble = document.getElementById('btnModeDouble');
    const btnSingle = document.getElementById('btnModeSingle');
    const manualTareGroup = document.getElementById('manualTareGroup');
    const netWeightContainer = document.getElementById('netWeightFieldContainer');

    if (mode === 'double') {
        btnDouble.className = 'px-4 sm:px-5 py-2 rounded-lg text-xs sm:text-sm font-black transition bg-blue-600 text-white shadow-sm';
        btnSingle.className = 'px-4 sm:px-5 py-2 rounded-lg text-xs sm:text-sm font-bold text-slate-600 hover:text-slate-900 transition';
        if (manualTareGroup) manualTareGroup.classList.add('hidden');
        if (currentStage === '2ND_WEIGHT') {
            if (netWeightContainer) netWeightContainer.classList.remove('hidden');
        } else {
            if (netWeightContainer) netWeightContainer.classList.add('hidden');
        }
    } else {
        btnSingle.className = 'px-4 sm:px-5 py-2 rounded-lg text-xs sm:text-sm font-black transition bg-blue-600 text-white shadow-sm';
        btnDouble.className = 'px-4 sm:px-5 py-2 rounded-lg text-xs sm:text-sm font-bold text-slate-600 hover:text-slate-900 transition';
        if (manualTareGroup) manualTareGroup.classList.remove('hidden');
        if (netWeightContainer) netWeightContainer.classList.remove('hidden');
        document.getElementById('scaleStageBadge').textContent = 'SINGLE WEIGHMENT / ஒற்றை';
        document.getElementById('scaleStageBadge').className = 'text-xs font-black px-2.5 py-0.5 rounded-lg bg-emerald-100 text-emerald-800 uppercase tracking-wide border border-emerald-200';
    }
    recalcNetWeight();
}

// AI Auto-Pilot Mode Toggle
function toggleAutoPilotMode() {
    autoPilotEnabled = !autoPilotEnabled;
    const led = document.getElementById('autoPilotLed');
    const txt = document.getElementById('autoPilotStatusText');
    const btn = document.getElementById('btnAutoPilotToggle');

    if (autoPilotEnabled) {
        led.className = 'w-2.5 h-2.5 rounded-full bg-emerald-500 animate-pulse';
        txt.textContent = 'ON';
        txt.className = 'text-emerald-700';
        btn.className = 'px-3 py-2 rounded-xl text-xs sm:text-sm font-black border transition flex items-center gap-2 bg-emerald-50 border-emerald-300 text-emerald-900 shadow-xs';
    } else {
        led.className = 'w-2.5 h-2.5 rounded-full bg-slate-400';
        txt.textContent = 'OFF';
        txt.className = 'text-slate-700';
        btn.className = 'px-3 py-2 rounded-xl text-xs sm:text-sm font-black border transition flex items-center gap-2 bg-slate-100 border-slate-300 text-slate-700 hover:bg-slate-200 shadow-xs';
    }
}

// Load Condition Selector: Loaded vs Empty
function setLoadCondition(status) {
    const input = document.getElementById('loadStatusInput');
    const lblLoaded = document.getElementById('lblLoadLoaded');
    const lblEmpty = document.getElementById('lblLoadEmpty');
    const emptyHint = document.getElementById('materialEmptyHint');

    if (input) input.value = status;

    if (status.includes('Loaded') || status.includes('லோடு')) {
        lblLoaded.className = 'cursor-pointer flex items-center justify-center gap-1.5 py-2 px-2.5 rounded-xl text-xs sm:text-sm font-black transition bg-emerald-600 text-white shadow select-none';
        lblEmpty.className = 'cursor-pointer flex items-center justify-center gap-1.5 py-2 px-2.5 rounded-xl text-xs sm:text-sm font-bold transition text-slate-700 hover:bg-slate-200 select-none';
        if (emptyHint) emptyHint.classList.add('hidden');
    } else {
        lblEmpty.className = 'cursor-pointer flex items-center justify-center gap-1.5 py-2 px-2.5 rounded-xl text-xs sm:text-sm font-black transition bg-slate-700 text-white shadow select-none';
        lblLoaded.className = 'cursor-pointer flex items-center justify-center gap-1.5 py-2 px-2.5 rounded-xl text-xs sm:text-sm font-bold transition text-slate-700 hover:bg-slate-200 select-none';
        if (emptyHint) emptyHint.classList.remove('hidden');
    }

    updateTariffByVehicleAndLoad();
}

// Tariff Calculation
function updateTariffByVehicleAndLoad() {
    const vt = document.getElementById('vehicle_type')?.value || '';
    const loadStatus = document.getElementById('loadStatusInput')?.value || 'Loaded';
    const isLoaded = loadStatus.includes('Loaded') || loadStatus.includes('லோடு');

    for (const [key, rates] of Object.entries(TARIFF_RATES)) {
        if (vt.includes(key.split('/')[0].trim())) {
            const chargeInput = document.getElementById('charge');
            if (chargeInput) {
                chargeInput.value = isLoaded ? rates.loaded : rates.empty;
            }
            break;
        }
    }
}

// Vehicle Input Formatter
function handleVehicleInput(val) {
    const cleaned = val.toUpperCase().replace(/[^A-Z0-9]/g, '');
    const input = document.getElementById('vehicle_no');
    if (input && input.value !== cleaned) {
        input.value = cleaned;
    }
}

// Vehicle Lookup on Blur / Enter
async function lookupVehicleDetails(val) {
    const clean = val.toUpperCase().replace(/[^A-Z0-9]/g, '').trim();
    if (!clean || clean.length < 4) return;

    try {
        const res = await fetch(`/api/vehicle-lookup?v=${encodeURIComponent(clean)}`);
        if (!res.ok) return;
        const data = await res.json();

        const storedTareBox = document.getElementById('storedTareBox');
        const storedTareVal = document.getElementById('storedTareVal');
        const storedTareDate = document.getElementById('storedTareDate');

        // Check if there is an active 1st-weight ticket (2nd Weight flow)
        if (data.has_open && data.open_ticket) {
            const ot = data.open_ticket;
            currentStage = '2ND_WEIGHT';
            firstWeightVal = Number(ot.first_weight || 0);

            document.getElementById('formStage').value = 'COMPLETED';
            document.getElementById('formFirstWeight').value = firstWeightVal;
            if (ot.ticket_no && document.getElementById('serial_no_display')) {
                document.getElementById('serial_no_display').value = ot.ticket_no;
            }

            // Fill form with initial data
            if (ot.customer_name) document.getElementById('customer_name').value = ot.customer_name;
            if (ot.mobile) document.getElementById('mobile').value = ot.mobile;
            if (ot.vehicle_type) document.getElementById('vehicle_type').value = ot.vehicle_type;
            if (ot.material) document.getElementById('material').value = ot.material;

            // 1st Weight card display
            const fwContainer = document.getElementById('firstWeightFieldContainer');
            if (fwContainer) fwContainer.classList.remove('hidden');
            document.getElementById('firstWeightDisplayInput').value = firstWeightVal.toLocaleString('en-IN') + ' Kg';
            document.getElementById('firstWeightTimestamp').textContent = `Recorded: ${ot.first_weight_at || '--'}`;

            // 1st weight badge
            const fwBadge = document.getElementById('firstWeightLoadBadge');
            if (ot.load_status && (ot.load_status.includes('Empty') || ot.load_status.includes('காலி'))) {
                fwBadge.textContent = '⚪ EMPTY';
                fwBadge.className = 'text-[10px] font-black px-2 py-0.5 rounded-md bg-slate-100 text-slate-800 border border-slate-300 uppercase';
                // Current weight is likely Loaded
                setLoadCondition('Loaded / லோடு');
            } else {
                fwBadge.textContent = '📦 LOADED';
                fwBadge.className = 'text-[10px] font-black px-2 py-0.5 rounded-md bg-emerald-100 text-emerald-800 border border-emerald-300 uppercase';
                // Current weight is likely Empty
                setLoadCondition('Empty / காலி');
            }

            // Stage badges
            document.getElementById('scaleStageBadge').textContent = '2nd WEIGHT / 2-ம் எடை';
            document.getElementById('scaleStageBadge').className = 'text-xs font-black px-2.5 py-0.5 rounded-lg bg-indigo-100 text-indigo-800 uppercase tracking-wide border border-indigo-200';
            document.getElementById('btnSaveText').textContent = 'Complete & Print to Epson L3350';

            // Show net weight container
            document.getElementById('netWeightFieldContainer').classList.remove('hidden');
            recalcNetWeight();

        } else {
            // Fresh 1st Weight
            currentStage = '1ST_WEIGHT';
            document.getElementById('formStage').value = 'FIRST_WEIGHT';
            document.getElementById('formFirstWeight').value = '';
            document.getElementById('firstWeightFieldContainer')?.classList.add('hidden');
            if (currentMode === 'double') {
                document.getElementById('netWeightFieldContainer')?.classList.add('hidden');
            }
            document.getElementById('scaleStageBadge').textContent = '1st WEIGHT / 1-ம் எடை';
            document.getElementById('scaleStageBadge').className = 'text-xs font-black px-2.5 py-0.5 rounded-lg bg-blue-100 text-blue-800 uppercase tracking-wide border border-blue-200';
            document.getElementById('btnSaveText').textContent = 'Save & Print to Epson L3350';

            // Check for stored tare
            if (data.stored_tare && storedTareBox) {
                storedTareVal.textContent = Number(data.stored_tare).toLocaleString('en-IN');
                storedTareDate.textContent = `(${data.stored_tare_date || 'Past'})`;
                storedTareBox.classList.remove('hidden');
                const manualTareEl = document.getElementById('manual_tare');
                if (manualTareEl && !manualTareEl.value) {
                    manualTareEl.value = Number(data.stored_tare);
                    recalcNetWeight();
                }
            } else if (storedTareBox) {
                storedTareBox.classList.add('hidden');
            }

            if (data.customer_name) document.getElementById('customer_name').value = data.customer_name;
            if (data.mobile) document.getElementById('mobile').value = data.mobile;
            if (data.vehicle_type) document.getElementById('vehicle_type').value = data.vehicle_type;
            if (data.material) document.getElementById('material').value = data.material;
        }

    } catch (e) {
        console.warn('Vehicle lookup error:', e);
    }
}

// Reset 2nd weight back to 1st weight
function startFreshTicket() {
    currentStage = '1ST_WEIGHT';
    firstWeightVal = 0;
    document.getElementById('formStage').value = 'FIRST_WEIGHT';
    document.getElementById('formFirstWeight').value = '';
    if (initialNextTicketNo && document.getElementById('serial_no_display')) {
        document.getElementById('serial_no_display').value = initialNextTicketNo;
    }
    document.getElementById('firstWeightFieldContainer')?.classList.add('hidden');
    if (currentMode === 'double') {
        document.getElementById('netWeightFieldContainer')?.classList.add('hidden');
    }
    document.getElementById('scaleStageBadge').textContent = '1st WEIGHT / 1-ம் எடை';
    document.getElementById('scaleStageBadge').className = 'text-xs font-black px-2.5 py-0.5 rounded-lg bg-blue-100 text-blue-800 uppercase tracking-wide border border-blue-200';
    document.getElementById('btnSaveText').textContent = 'Save & Print to Epson L3350';
}

// Calculate Net Weight
function recalcNetWeight() {
    const netEl = document.getElementById('netWeightDisplayInput');
    if (!netEl) return;

    if (currentStage === '2ND_WEIGHT') {
        const net = Math.abs(currentLiveWeight - firstWeightVal);
        netEl.value = net.toLocaleString('en-IN') + ' Kg';
    } else if (currentMode === 'single') {
        const manualTare = parseFloat(document.getElementById('manual_tare')?.value || 0);
        if (manualTare > 0) {
            const net = Math.max(0, currentLiveWeight - manualTare);
            netEl.value = net.toLocaleString('en-IN') + ' Kg';
        } else {
            netEl.value = currentLiveWeight.toLocaleString('en-IN') + ' Kg';
        }
    }
}

// Refresh Camera Previews (Preload smoothly in background with zero flicker)
async function refreshCameraPreviews() {
    if (isRefreshingCameras) return;
    isRefreshingCameras = true;

    const btn = document.getElementById('btnRefreshFeeds');
    if (btn) btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin mr-1 text-blue-600"></i> Refreshing...';

    const t = Date.now();
    const front = document.getElementById('frontCameraImg');
    const rear = document.getElementById('rearCameraImg');

    const loadImg = (imgEl, url) => {
        return new Promise((resolve) => {
            if (!imgEl) return resolve();
            const temp = new Image();
            temp.onload = () => {
                imgEl.src = temp.src;
                resolve();
            };
            temp.onerror = () => resolve();
            temp.src = url;
        });
    };

    await Promise.all([
        loadImg(front, `/api/camera-snapshot?camera=front&t=${t}`),
        loadImg(rear, `/api/camera-snapshot?camera=rear&t=${t}`)
    ]);

    if (btn) btn.innerHTML = '<i class="fa-solid fa-arrows-rotate mr-1 text-blue-600"></i> Refresh Feeds';
    isRefreshingCameras = false;
}

// Gemini Dual-Camera Fusion ANPR
async function runGeminiFusionANPR() {
    const btnText = document.getElementById('geminiFusionBtnText');
    const feedback = document.getElementById('geminiScanFeedback');

    if (btnText) btnText.innerHTML = '<i class="fa-solid fa-spinner fa-spin mr-1"></i> Scanning Plates...';
    if (feedback) feedback.classList.add('hidden');

    try {
        const res = await fetch('/api/gemini/detect', { method: 'POST' });
        const data = await res.json();

        if (data.success && data.vehicle_no) {
            const vInput = document.getElementById('vehicle_no');
            if (vInput) {
                vInput.value = data.vehicle_no;
                lookupVehicleDetails(data.vehicle_no);
            }
            const detectedClass = data.vehicle_class || data.vehicle_type;
            if (detectedClass && document.getElementById('vehicle_type')) {
                const vtSelect = document.getElementById('vehicle_type');
                const matchOpt = Array.from(vtSelect.options).find(o => o.value === detectedClass || o.value.includes(detectedClass));
                if (matchOpt) {
                    vtSelect.value = matchOpt.value;
                    updateTariffByVehicleAndLoad();
                }
            }
            if (data.material && document.getElementById('material')) {
                const matSelect = document.getElementById('material');
                const matchMat = Array.from(matSelect.options).find(o => o.value === data.material || o.value.includes(data.material));
                if (matchMat) matSelect.value = matchMat.value;
            }

            if (feedback) {
                feedback.className = 'mt-2 p-2 rounded-xl text-xs font-bold border border-emerald-300 bg-emerald-50 text-emerald-900 flex items-center justify-between';
                feedback.innerHTML = `<span>✨ Detected: <strong>${data.vehicle_no}</strong> (${detectedClass || 'Vehicle'})</span><button type="button" onclick="this.parentElement.classList.add('hidden')" class="text-emerald-700">&times;</button>`;
                feedback.classList.remove('hidden');
            }
        } else {
            if (feedback) {
                feedback.className = 'mt-2 p-2 rounded-xl text-xs font-bold border border-amber-300 bg-amber-50 text-amber-900 flex items-center justify-between';
                feedback.innerHTML = `<span>⚠️ ${data.error || data.message || 'Plate not detected clearly'}. Try manual entry.</span><button type="button" onclick="this.parentElement.classList.add('hidden')" class="text-amber-700">&times;</button>`;
                feedback.classList.remove('hidden');
            }
        }
    } catch (e) {
        console.warn('Gemini detection error:', e);
    } finally {
        if (btnText) btnText.textContent = 'Dual AI Fusion';
    }
}

// Voice Recognition
function startVoiceRecognition() {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
        alert('Voice input not supported in this browser. Please use Chrome/Edge.');
        return;
    }
    const recognition = new SpeechRecognition();
    recognition.lang = 'en-IN';
    recognition.onresult = (event) => {
        const transcript = event.results[0][0].transcript;
        const cleaned = transcript.toUpperCase().replace(/[^A-Z0-9]/g, '');
        if (cleaned) {
            document.getElementById('vehicle_no').value = cleaned;
            lookupVehicleDetails(cleaned);
        }
    };
    recognition.start();
}

// Photo Upload Scan
async function handlePhotoUpload(e) {
    const file = e.target.files[0];
    if (!file) return;

    const btnText = document.getElementById('geminiFusionBtnText');
    const feedback = document.getElementById('geminiScanFeedback');
    if (btnText) btnText.innerHTML = '<i class="fa-solid fa-spinner fa-spin mr-1"></i> Scanning Photo...';
    if (feedback) feedback.classList.add('hidden');

    try {
        const formData = new FormData();
        formData.append('file', file);
        const res = await fetch('/api/gemini/upload', { method: 'POST', body: formData });
        const data = await res.json();

        if (data.success && data.vehicle_no) {
            const vInput = document.getElementById('vehicle_no');
            if (vInput) {
                vInput.value = data.vehicle_no;
                lookupVehicleDetails(data.vehicle_no);
            }
            if (feedback) {
                feedback.className = 'mt-2 p-2 rounded-xl text-xs font-bold border border-emerald-300 bg-emerald-50 text-emerald-900 flex items-center justify-between';
                feedback.innerHTML = `<span>📸 Photo Scan: <strong>${data.vehicle_no}</strong></span><button type="button" onclick="this.parentElement.classList.add('hidden')" class="text-emerald-700">&times;</button>`;
                feedback.classList.remove('hidden');
            }
        } else if (feedback) {
            feedback.className = 'mt-2 p-2 rounded-xl text-xs font-bold border border-amber-300 bg-amber-50 text-amber-900 flex items-center justify-between';
            feedback.innerHTML = `<span>⚠️ ${data.error || 'Could not read plate from uploaded photo.'}</span><button type="button" onclick="this.parentElement.classList.add('hidden')" class="text-amber-700">&times;</button>`;
            feedback.classList.remove('hidden');
        }
    } catch (err) {
        console.warn('Photo upload scan error:', err);
    } finally {
        e.target.value = '';
        if (btnText) btnText.textContent = 'Dual AI Fusion';
    }
}

// Pre-Print Verification Modal
function handleSaveWeighClick() {
    const vehNo = document.getElementById('vehicle_no')?.value.trim();
    if (!vehNo) {
        alert('Please enter vehicle number! / வண்டி எண்ணை உள்ளிடவும்');
        document.getElementById('vehicle_no')?.focus();
        return;
    }

    // Populate modal summary table
    document.getElementById('confirmVehNo').textContent = vehNo;
    document.getElementById('confirmWeight').textContent = currentLiveWeight.toLocaleString('en-IN') + ' KG';
    document.getElementById('confirmVehType').textContent = document.getElementById('vehicle_type')?.value || '--';
    document.getElementById('confirmLoadStatus').textContent = document.getElementById('loadStatusInput')?.value || 'Loaded';
    document.getElementById('confirmMaterial').textContent = document.getElementById('material')?.value || 'General Cargo';
    document.getElementById('confirmCharge').textContent = '₹ ' + (document.getElementById('charge')?.value || '100');

    const confirmStage = document.getElementById('confirmStage');
    const confirmNetRow = document.getElementById('confirmNetRow');
    const confirmNetWeight = document.getElementById('confirmNetWeight');

    if (currentMode === 'single') {
        confirmStage.textContent = 'Single Weighment / ஒற்றை முறை';
        const manualTare = parseFloat(document.getElementById('manual_tare')?.value || 0);
        if (manualTare > 0) {
            confirmNetRow.classList.remove('hidden');
            confirmNetWeight.textContent = Math.max(0, currentLiveWeight - manualTare).toLocaleString('en-IN') + ' KG';
        } else {
            confirmNetRow.classList.add('hidden');
        }
    } else if (currentStage === '2ND_WEIGHT') {
        confirmStage.textContent = '2nd Weight Completion / 2-ம் எடை நிறைவு';
        confirmNetRow.classList.remove('hidden');
        confirmNetWeight.textContent = Math.abs(currentLiveWeight - firstWeightVal).toLocaleString('en-IN') + ' KG';
    } else {
        confirmStage.textContent = '1st Weight Record / 1-ம் எடை பதிவு';
        confirmNetRow.classList.add('hidden');
    }

    // Load Live Camera Snapshots into Modal
    const t = Date.now();
    const frontImg = document.getElementById('confirmFrontImg');
    const rearImg = document.getElementById('confirmRearImg');
    if (frontImg) frontImg.src = `/api/camera-snapshot?camera=front&t=${t}`;
    if (rearImg) rearImg.src = `/api/camera-snapshot?camera=rear&t=${t}`;

    // Show Modal
    const modal = document.getElementById('confirmSaveModal');
    if (modal) modal.classList.remove('hidden');
}

function closeConfirmModal() {
    const modal = document.getElementById('confirmSaveModal');
    if (modal) modal.classList.add('hidden');
}

// Final Save & Print Submission
async function doConfirmSubmit() {
    const btn = document.getElementById('btnConfirmSubmit');
    const label = document.getElementById('confirmBtnLabel');
    if (btn) btn.disabled = true;
    if (label) label.textContent = 'Printing to Epson L3350...';

    const ticketNo = parseInt(document.getElementById('serial_no_display')?.value || 1001);
    const vehNo = document.getElementById('vehicle_no')?.value.trim();
    const customer = document.getElementById('customer_name')?.value.trim();
    const mobile = document.getElementById('mobile')?.value.trim();
    const vehicleType = document.getElementById('vehicle_type')?.value;
    const loadStatus = document.getElementById('loadStatusInput')?.value;
    const material = document.getElementById('material')?.value;
    const charge = parseFloat(document.getElementById('charge')?.value || 100);
    const copies = parseInt(document.getElementById('copies')?.value || 1);

    let gross = 0;
    let tare = 0;
    let net = 0;
    let stage = 'COMPLETED';

    if (currentMode === 'single') {
        const manualTare = parseFloat(document.getElementById('manual_tare')?.value || 0);
        if (loadStatus.includes('Empty') || loadStatus.includes('காலி')) {
            tare = currentLiveWeight;
            gross = currentLiveWeight;
            net = 0;
        } else {
            gross = currentLiveWeight;
            tare = manualTare;
            net = Math.max(0, gross - tare);
        }
        stage = 'COMPLETED';
    } else if (currentStage === '1ST_WEIGHT') {
        stage = 'FIRST_WEIGHT';
        if (loadStatus.includes('Empty') || loadStatus.includes('காலி')) {
            tare = currentLiveWeight;
        } else {
            gross = currentLiveWeight;
        }
        net = 0;
    } else {
        // 2nd weight
        stage = 'COMPLETED';
        if (loadStatus.includes('Empty') || loadStatus.includes('காலி')) {
            tare = currentLiveWeight;
            gross = firstWeightVal;
        } else {
            gross = currentLiveWeight;
            tare = firstWeightVal;
        }
        net = Math.abs(gross - tare);
    }

    try {
        const payload = {
            ticket_no: ticketNo,
            vehicle_no: vehNo,
            customer_name: customer,
            mobile: mobile,
            vehicle_type: vehicleType,
            load_status: loadStatus,
            material: material,
            gross_weight: gross,
            tare_weight: tare,
            net_weight: net,
            first_weight: (stage === 'FIRST_WEIGHT' ? currentLiveWeight : firstWeightVal),
            stage: stage,
            charge: charge,
            copies: copies
        };

        const res = await fetch('/api/ticket/save-and-print', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        const data = await res.json();

        if (res.ok && data.success) {
            if (data.print_success) {
                alert(`✅ Ticket #${data.ticket_no} Saved & Printed Successfully!\n${data.print_status}`);
            } else {
                alert(`⚠️ Ticket #${data.ticket_no} Saved to Cloud DB, but Printer Failed:\n${data.print_status}`);
            }
            window.location.reload();
        } else {
            alert(`Save error: ${data.detail || 'Unknown error'}`);
        }
    } catch (e) {
        alert('Network/API Error: ' + e);
    } finally {
        if (btn) btn.disabled = false;
        if (label) label.textContent = 'Confirm & Print to Epson L3350';
        closeConfirmModal();
    }
}

// Initialize on page load
window.addEventListener('DOMContentLoaded', () => {
    const serialEl = document.getElementById('serial_no_display');
    if (serialEl) initialNextTicketNo = serialEl.value;
    pollLiveWeight();
    if (document.getElementById('frontCameraImg')) {
        refreshCameraPreviews();
    }
});
