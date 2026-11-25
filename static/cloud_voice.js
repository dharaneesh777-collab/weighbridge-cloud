/**
 * Smart Bilingual Gemini Voice Assistant (Tamil, English & Tanglish)
 * for Weighbridge Cloud Portal
 */
(function () {
  'use strict';

  let recognition = null;
  let isListening = false;
  let ttsEnabled = true;
  let selectedLang = localStorage.getItem('wb_voice_lang') || 'auto'; // 'auto' | 'en-IN' | 'ta-IN'

  window.addEventListener('DOMContentLoaded', () => {
    initVoiceAssistant();
  });

  function initVoiceAssistant() {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    const supported = Boolean(SpeechRecognition);

    if (supported) {
      recognition = new SpeechRecognition();
      recognition.continuous = false;
      recognition.interimResults = true;
      recognition.maxAlternatives = 1;

      recognition.onstart = () => {
        isListening = true;
        updateUIListening(true);
        showVoiceToast('🎙️ Listening... Speak in தமிழ் or English', 'info');
      };

      recognition.onresult = (event) => {
        let interimTranscript = '';
        let finalTranscript = '';

        for (let i = event.resultIndex; i < event.results.length; ++i) {
          if (event.results[i].isFinal) {
            finalTranscript += event.results[i][0].transcript;
          } else {
            interimTranscript += event.results[i][0].transcript;
          }
        }

        const display = finalTranscript || interimTranscript;
        if (display) setVoiceStatusText(`"${display}"`, 'text-blue-200');
        if (finalTranscript.trim()) {
          handleUserSpeech(finalTranscript.trim());
        }
      };

      recognition.onerror = (event) => {
        isListening = false;
        updateUIListening(false);
        if (event.error === 'not-allowed') {
          showVoiceToast('⚠️ Mic blocked. Use text box below or allow mic permission.', 'error');
        } else if (event.error !== 'no-speech') {
          showVoiceToast(`Mic note: ${event.error}`, 'warning');
        }
      };

      recognition.onend = () => {
        isListening = false;
        updateUIListening(false);
      };
    }

    injectVoiceUI(supported);

    window.addEventListener('keydown', (e) => {
      if (e.altKey && (e.key === 'v' || e.key === 'V')) {
        e.preventDefault();
        toggleVoiceTrayAndListen(supported);
      }
    });
  }

  function injectVoiceUI(supported) {
    if (document.getElementById('geminiVoiceContainer')) return;

    const container = document.createElement('div');
    container.id = 'geminiVoiceContainer';
    container.className = 'fixed bottom-5 right-5 z-50 flex flex-col items-end gap-2 font-sans pointer-events-auto';

    container.innerHTML = `
      <div id="geminiVoiceTray" class="hidden w-80 sm:w-96 bg-slate-900/95 backdrop-blur-md text-white rounded-2xl shadow-2xl border border-slate-700/80 p-4 transition-all duration-200">
        <div class="flex items-center justify-between border-b border-slate-800 pb-2.5 mb-2.5">
          <div class="flex items-center gap-2">
            <span class="w-2.5 h-2.5 rounded-full bg-emerald-400 animate-pulse"></span>
            <span class="font-black text-sm tracking-wide text-transparent bg-clip-text bg-gradient-to-r from-violet-400 to-indigo-300">
              Gemini Voice AI
            </span>
            <span class="text-[10px] bg-indigo-950 text-indigo-300 border border-indigo-700/50 px-1.5 py-0.5 rounded-md font-semibold">EN / தமிழ்</span>
          </div>

          <div class="flex items-center gap-1.5">
            <select id="geminiVoiceLangSelect" class="bg-slate-800 text-[11px] font-bold text-slate-200 rounded-lg px-2 py-1 border border-slate-700 outline-none cursor-pointer">
              <option value="auto">🌐 Auto (தமிழ்/EN)</option>
              <option value="ta-IN">🇮🇳 தமிழ்</option>
              <option value="en-IN">🇬🇧 English</option>
            </select>
            <button id="geminiVoiceTtsBtn" type="button" title="Toggle Spoken Feedback" class="p-1 rounded-lg hover:bg-slate-800 text-slate-300 hover:text-white transition text-xs cursor-pointer">
              🔊
            </button>
            <button id="geminiVoiceCloseBtn" type="button" title="Close" class="p-1 rounded-lg hover:bg-slate-800 text-slate-400 hover:text-white transition text-xs cursor-pointer">
              ✕
            </button>
          </div>
        </div>

        <div class="bg-slate-950/75 rounded-xl p-3 border border-slate-800/80 min-h-[56px] flex flex-col justify-center">
          <div id="geminiVoiceStatus" class="text-xs text-slate-300 font-medium">
            ${supported ? 'Click mic or type a command in Tamil / English / Tanglish...' : 'Mic unavailable here — type a Tamil / English command below'}
          </div>
          <div id="geminiVoiceActionPill" class="hidden mt-2 text-[11px] font-bold px-2 py-1 rounded-md"></div>
        </div>

        <!-- Direct Bilingual Command Input (useful in noisy cabs or mic-less devices) -->
        <form id="geminiVoiceTextForm" class="mt-2.5 flex items-center gap-1.5">
          <input id="geminiVoiceTextInput" type="text" autocomplete="off"
                 placeholder="Type command (e.g. TN33AB1234 மணல் 2 copies)"
                 class="flex-1 bg-slate-800/90 text-white placeholder-slate-400 text-xs px-3 py-2 rounded-xl border border-slate-700 focus:border-indigo-400 outline-none">
          <button id="geminiVoiceSendBtn" type="submit"
                  class="px-3 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-black transition cursor-pointer">
            Run
          </button>
        </form>

        <!-- Clickable Sample Commands -->
        <div class="mt-2.5 pt-2 border-t border-slate-800/80 text-[10px] text-slate-400 flex flex-wrap gap-1.5">
          <span class="text-slate-500 font-semibold">Try:</span>
          <button type="button" class="voice-cmd-sample hover:text-indigo-300 bg-slate-800/70 hover:bg-slate-800 px-1.5 py-0.5 rounded cursor-pointer transition">TN33AB1234 Murugan மணல் 2 copies</button>
          <button type="button" class="voice-cmd-sample hover:text-indigo-300 bg-slate-800/70 hover:bg-slate-800 px-1.5 py-0.5 rounded cursor-pointer transition">இன்றைய கலெக்ஷன் எவ்வளவு?</button>
          <button type="button" class="voice-cmd-sample hover:text-indigo-300 bg-slate-800/70 hover:bg-slate-800 px-1.5 py-0.5 rounded cursor-pointer transition">எடை எவ்வளவு?</button>
          <button type="button" class="voice-cmd-sample hover:text-indigo-300 bg-slate-800/70 hover:bg-slate-800 px-1.5 py-0.5 rounded cursor-pointer transition">கேமரா ஸ்கேன்</button>
          <button type="button" class="voice-cmd-sample hover:text-indigo-300 bg-slate-800/70 hover:bg-slate-800 px-1.5 py-0.5 rounded cursor-pointer transition">Clear form</button>
        </div>
      </div>

      <!-- Floating Action Button (FAB) -->
      <button id="geminiVoiceFab" type="button"
              class="relative w-14 h-14 rounded-full bg-gradient-to-tr from-violet-600 via-indigo-600 to-purple-500 hover:from-violet-500 hover:to-indigo-500 text-white shadow-2xl flex items-center justify-center transition-all duration-200 hover:scale-105 active:scale-95 focus:outline-none cursor-pointer"
              title="Gemini Bilingual Voice Assistant (Alt + V)">
        <span id="geminiVoicePulse" class="hidden absolute inset-0 rounded-full bg-emerald-400 opacity-75 animate-ping"></span>
        <span id="geminiVoiceIcon" class="relative z-10 text-2xl">🎙️</span>
      </button>
    `;

    document.body.appendChild(container);

    const fab = document.getElementById('geminiVoiceFab');
    const tray = document.getElementById('geminiVoiceTray');
    const closeBtn = document.getElementById('geminiVoiceCloseBtn');
    const ttsBtn = document.getElementById('geminiVoiceTtsBtn');
    const langSelect = document.getElementById('geminiVoiceLangSelect');
    const textForm = document.getElementById('geminiVoiceTextForm');
    const textInput = document.getElementById('geminiVoiceTextInput');

    if (langSelect) langSelect.value = selectedLang;

    fab.addEventListener('click', () => {
      toggleVoiceTrayAndListen(supported);
    });

    closeBtn.addEventListener('click', () => {
      stopVoiceListening();
      tray.classList.add('hidden');
    });

    ttsBtn.addEventListener('click', () => {
      ttsEnabled = !ttsEnabled;
      ttsBtn.textContent = ttsEnabled ? '🔊' : '🔇';
      ttsBtn.title = ttsEnabled ? 'Spoken Feedback ON' : 'Spoken Feedback OFF';
    });

    langSelect.addEventListener('change', (e) => {
      selectedLang = e.target.value;
      localStorage.setItem('wb_voice_lang', selectedLang);
    });

    textForm.addEventListener('submit', (e) => {
      e.preventDefault();
      const cmd = (textInput.value || '').trim();
      if (!cmd) return;
      textInput.value = '';
      handleUserSpeech(cmd);
    });

    container.querySelectorAll('.voice-cmd-sample').forEach((btn) => {
      btn.addEventListener('click', () => {
        const cmd = (btn.textContent || '').trim();
        if (cmd) handleUserSpeech(cmd);
      });
    });
  }

  function openVoiceTray() {
    const tray = document.getElementById('geminiVoiceTray');
    if (tray && tray.classList.contains('hidden')) {
      tray.classList.remove('hidden');
    }
  }

  function toggleVoiceTrayAndListen(supported = Boolean(recognition)) {
    openVoiceTray();
    if (!supported || !recognition) {
      document.getElementById('geminiVoiceTextInput')?.focus();
      return;
    }
    if (isListening) {
      stopVoiceListening();
    } else {
      startVoiceListening();
    }
  }

  function startVoiceListening() {
    if (!recognition) return;
    try {
      recognition.lang = (selectedLang === 'auto') ? 'ta-IN' : selectedLang;
      recognition.start();
    } catch (err) {
      console.warn('Recognition start error:', err);
    }
  }

  function stopVoiceListening() {
    if (!recognition) return;
    try { recognition.stop(); } catch (_) {}
    isListening = false;
    updateUIListening(false);
  }

  function updateUIListening(listening) {
    const pulse = document.getElementById('geminiVoicePulse');
    const icon = document.getElementById('geminiVoiceIcon');
    const fab = document.getElementById('geminiVoiceFab');

    if (listening) {
      pulse?.classList.remove('hidden');
      if (icon) icon.textContent = '🔴';
      fab?.classList.add('ring-4', 'ring-rose-400/60');
    } else {
      pulse?.classList.add('hidden');
      if (icon) icon.textContent = '🎙️';
      fab?.classList.remove('ring-4', 'ring-rose-400/60');
    }
  }

  function setVoiceStatusText(text, colorClass = 'text-slate-300') {
    const el = document.getElementById('geminiVoiceStatus');
    if (el) {
      el.className = `text-xs font-medium ${colorClass}`;
      el.textContent = text;
    }
  }

  function showActionPill(text, type = 'success') {
    const pill = document.getElementById('geminiVoiceActionPill');
    if (!pill) return;
    pill.classList.remove('hidden');
    if (type === 'success') {
      pill.className = 'mt-2 text-[11px] font-bold px-2.5 py-1 rounded-md bg-emerald-900/60 text-emerald-200 border border-emerald-700/50';
      pill.textContent = `✨ ${text}`;
    } else {
      pill.className = 'mt-2 text-[11px] font-bold px-2.5 py-1 rounded-md bg-amber-900/60 text-amber-200 border border-amber-700/50';
      pill.textContent = `ℹ️ ${text}`;
    }
  }

  function showVoiceToast(msg, type = 'info') {
    const cls = type === 'error' ? 'text-rose-400' : (type === 'warning' ? 'text-amber-300' : 'text-blue-300');
    setVoiceStatusText(msg, cls);
  }

  function speakResponse(text) {
    if (!ttsEnabled || !window.speechSynthesis || !text) return;
    try {
      window.speechSynthesis.cancel();
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.rate = 1.02;
      const hasTamil = /[\u0B80-\u0BFF]/.test(text);
      utterance.lang = hasTamil ? 'ta-IN' : 'en-IN';
      const voices = window.speechSynthesis.getVoices();
      if (voices.length > 0) {
        const match = voices.find((v) => hasTamil ? v.lang.startsWith('ta') : (v.lang.startsWith('en-IN') || v.lang.startsWith('en')));
        if (match) utterance.voice = match;
      }
      window.speechSynthesis.speak(utterance);
    } catch (_) {}
  }

  async function handleUserSpeech(speech) {
    if (!speech) return;
    openVoiceTray();
    setVoiceStatusText(`Analyzing: "${speech}"...`, 'text-amber-300');
    const icon = document.getElementById('geminiVoiceIcon');
    if (icon) icon.textContent = '⏳';

    try {
      const resp = await fetch('/api/gemini/voice_command', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          speech: speech,
          lang: selectedLang === 'auto' ? 'mixed' : selectedLang
        })
      });

      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      if (icon) icon.textContent = '🎙️';

      if (!data.success) {
        setVoiceStatusText(data.error || 'Could not understand command.', 'text-rose-400');
        showActionPill(data.error || 'Retry command', 'warning');
        return;
      }

      await executeVoiceAction(data);
    } catch (err) {
      console.warn('Voice command error:', err);
      if (icon) icon.textContent = '🎙️';
      setVoiceStatusText('Network error connecting to Gemini Voice AI.', 'text-rose-400');
    }
  }

  function selectMatchingOption(selectEl, targetValue) {
    if (!selectEl || !targetValue) return null;
    const targetLower = String(targetValue).toLowerCase();
    for (const opt of selectEl.options) {
      const ov = opt.value.toLowerCase();
      const ot = opt.text.toLowerCase();
      if (ov === targetLower || ot === targetLower || ov.includes(targetLower) || targetLower.includes(ov.split('/')[0].trim())) {
        selectEl.value = opt.value;
        return opt.value;
      }
    }
    return null;
  }

  async function executeVoiceAction(res) {
    const action = res.action;
    const spoken = res.spoken_reply || 'Command executed.';

    if (action === 'copilot_query') {
      const answer = res.copilot_answer || spoken;
      setVoiceStatusText(`💡 ${answer}`, 'text-amber-200');
      let pillText = answer;
      if (res.metrics) {
        pillText = `Today (IST): ${res.metrics.today_tickets_count} tickets | ₹${res.metrics.today_total_revenue_rs} | ${res.metrics.today_total_net_tons} t (${res.metrics.today_open_count} open)`;
      }
      showActionPill(pillText, 'success');
      speakResponse(spoken);
      return;
    }

    if (action === 'read_weight') {
      const cleanWt = (typeof window.getCloudLiveWeight === 'function')
        ? Math.round(window.getCloudLiveWeight())
        : parseInt(document.getElementById('formCurrentWeight')?.value || '0', 10);
      const isTamil = /[\u0B80-\u0BFF]/.test(spoken);
      const wtReply = isTamil ? `தற்போதைய எடை ${cleanWt} கிலோ.` : `Current weight is ${cleanWt} kilograms.`;
      setVoiceStatusText(`⚖️ Live Scale: ${cleanWt.toLocaleString('en-IN')} Kg`, 'text-emerald-300');
      showActionPill(`Live Weight: ${cleanWt.toLocaleString('en-IN')} Kg`, 'success');
      speakResponse(wtReply);
      return;
    }

    if (action === 'scan_plate') {
      speakResponse(spoken);
      setVoiceStatusText('📸 Triggering Dual-Camera Gemini ANPR...', 'text-violet-300');
      showActionPill('Scanning Front & Rear cameras...', 'info');
      if (typeof window.runGeminiFusionANPR === 'function') {
        await window.runGeminiFusionANPR();
      } else {
        showActionPill('Open Weigh Scale page to scan cameras', 'warning');
      }
      return;
    }

    if (action === 'submit_weigh') {
      speakResponse(spoken);
      if (typeof window.handleSaveWeighClick === 'function') {
        setVoiceStatusText('🖨️ Opening Pre-Print Verification...', 'text-emerald-300');
        showActionPill('Verifying slip before print', 'success');
        window.handleSaveWeighClick();
      } else {
        showActionPill('Navigate to Weigh Scale page to save', 'warning');
      }
      return;
    }

    if (action === 'clear_form') {
      speakResponse(spoken);
      if (typeof window.resetCloudWeighForm === 'function') {
        window.resetCloudWeighForm();
      }
      setVoiceStatusText('🧹 Form Cleared / படிவம் அழிக்கப்பட்டது', 'text-slate-200');
      showActionPill('Form reset for next vehicle', 'info');
      return;
    }

    if (action === 'navigate' && res.navigate_to) {
      speakResponse(spoken);
      const dest = res.navigate_to === '/tickets' ? '/tickets' : '/';
      setVoiceStatusText(`🚀 Opening ${dest}...`, 'text-blue-300');
      showActionPill(`Navigating to ${dest}`, 'info');
      setTimeout(() => { window.location.href = dest; }, 600);
      return;
    }

    if (action === 'fill_form') {
      speakResponse(spoken);
      const filled = [];

      if (res.weighment_type === 'single' || res.weighment_type === 'double') {
        if (typeof window.toggleWeighMode === 'function') {
          window.toggleWeighMode(res.weighment_type);
          filled.push(`Mode: ${res.weighment_type}`);
        }
      }

      if (res.vehicle_no) {
        const vEl = document.getElementById('vehicle_no');
        if (vEl) {
          vEl.value = res.vehicle_no;
          filled.push(`Plate: ${res.vehicle_no}`);
          if (typeof window.lookupVehicleDetails === 'function') {
            await window.lookupVehicleDetails(res.vehicle_no);
          }
        }
      }

      if (res.customer_name) {
        const cEl = document.getElementById('customer_name');
        if (cEl) {
          cEl.value = res.customer_name;
          filled.push(`Customer: ${res.customer_name}`);
        }
      }

      if (res.mobile) {
        const mEl = document.getElementById('mobile');
        if (mEl) {
          mEl.value = res.mobile;
          filled.push(`Mobile: ${res.mobile}`);
        }
      }

      if (res.vehicle_type) {
        const matchedType = selectMatchingOption(document.getElementById('vehicle_type'), res.vehicle_type);
        if (matchedType) {
          filled.push(`Type: ${matchedType}`);
          if (typeof window.updateTariffByVehicleAndLoad === 'function') {
            window.updateTariffByVehicleAndLoad();
          }
        }
      }

      if (res.load_status && typeof window.setLoadCondition === 'function') {
        window.setLoadCondition(res.load_status);
        filled.push(`Load: ${res.load_status}`);
      }

      if (res.material) {
        const matchedMat = selectMatchingOption(document.getElementById('material'), res.material);
        if (matchedMat) filled.push(`Material: ${matchedMat}`);
      }

      if (res.copies) {
        const cpEl = document.getElementById('copies');
        if (cpEl) {
          cpEl.value = String(res.copies);
          filled.push(`Copies: ${res.copies}`);
        }
      }

      if (res.manual_tare !== null && res.manual_tare !== undefined) {
        const mtEl = document.getElementById('manual_tare');
        if (mtEl) {
          mtEl.value = String(res.manual_tare);
          filled.push(`Tare: ${res.manual_tare} kg`);
          if (typeof window.recalcNetWeight === 'function') {
            window.recalcNetWeight();
          }
        }
      }

      setVoiceStatusText(`✅ ${spoken}`, 'text-emerald-300');
      showActionPill(filled.length > 0 ? filled.join(' | ') : 'Form updated', 'success');
      return;
    }

    speakResponse(spoken);
    setVoiceStatusText(`💬 ${spoken}`, 'text-blue-300');
    showActionPill(spoken, 'info');
  }

  window.toggleGeminiVoiceAssistant = () => toggleVoiceTrayAndListen(Boolean(recognition));
  window.runBilingualVoiceTextCommand = handleUserSpeech;
})();
