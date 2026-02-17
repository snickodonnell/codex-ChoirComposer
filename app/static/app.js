let melodyScore = null;
let satbScore = null;

const sectionsEl = document.getElementById('sections');
const arrangementListEl = document.getElementById('arrangementList');
const arrangementSectionSelectEl = document.getElementById('arrangementSectionSelect');
const melodyMeta = document.getElementById('melodyMeta');
const satbMeta = document.getElementById('satbMeta');
const workflowStageLabelEl = document.getElementById('workflowStageLabel');
const workflowStageHintEl = document.getElementById('workflowStageHint');
const regenerateClustersEl = document.getElementById('regenerateClusters');
const draftVersionSelectEl = document.getElementById('draftVersionSelect');
const satbDraftVersionSelectEl = document.getElementById('satbDraftVersionSelect');
const satbRegenerateClustersEl = document.getElementById('satbRegenerateClusters');

const generateMelodyBtn = document.getElementById('generateMelody');
const refineBtn = document.getElementById('refine');
const regenerateBtn = document.getElementById('regenerate');
const startMelodyBtn = document.getElementById('startMelody');
const pauseMelodyBtn = document.getElementById('pauseMelody');
const stopMelodyBtn = document.getElementById('stopMelody');
const generateSATBBtn = document.getElementById('generateSATB');
const refineSatbBtn = document.getElementById('refineSATB');
const regenerateSatbBtn = document.getElementById('regenerateSATB');
const startSATBBtn = document.getElementById('startSATB');
const pauseSATBBtn = document.getElementById('pauseSATB');
const stopSATBBtn = document.getElementById('stopSATB');
const exportPDFBtn = document.getElementById('exportPDF');
const exportMusicXMLBtn = document.getElementById('exportMusicXML');
const loadTestDataBtn = document.getElementById('loadTestData');
const refreshMelodyPreviewBtn = document.getElementById('refreshMelodyPreview');
const refreshSatbPreviewBtn = document.getElementById('refreshSatbPreview');
const melodyPreviewEl = document.getElementById('melodyPreview');
const satbPreviewEl = document.getElementById('satbPreview');
const melodyPreviewStatusEl = document.getElementById('melodyPreviewStatus');
const satbPreviewStatusEl = document.getElementById('satbPreviewStatus');
const melodyPreviewZoomEl = document.getElementById('melodyPreviewZoom');
const satbPreviewZoomEl = document.getElementById('satbPreviewZoom');

const formErrorsEl = document.getElementById('formErrors');
const VALID_TONICS = new Set(['C','C#','Db','D','D#','Eb','E','F','F#','Gb','G','G#','Ab','A','A#','Bb','B']);
const VALID_MODES = new Set(['ionian','dorian','phrygian','lydian','mixolydian','aeolian','locrian','major','minor','natural minor']);
let sectionIdCounter = 0;
const MAX_DRAFT_VERSIONS = 20;
const HYMN_TEST_DATA_SECTIONS = [
  { label: 'Verse', is_verse: true, text: 'Amazing grace, how sweet the sound\nThat saved a wretch like me\nI once was lost, but now am found\nWas blind, but now I see' },
  { label: 'Verse', is_verse: true, text: "T'was grace that taught my heart to fear\nAnd grace my fears relieved\nHow precious did that grace appear\nThe hour I first believed" },
  { label: 'Verse', is_verse: true, text: "Through many dangers, toils, and snares\nI have already come\n'Tis grace hath brought me safe thus far\nAnd grace will lead me home" },
];
const HYMN_TEST_DATA_ANACRUSIS_MODE = 'manual';
const HYMN_TEST_DATA_ANACRUSIS_BEATS = 2;
let melodyDraftVersions = [];
let activeDraftVersionId = null;
let satbDraftVersionsByMelodyVersion = new Map();
let activeSatbDraftVersionId = null;
let activePlayback = null;
let playbackEventLog = [];
if (typeof window !== 'undefined') {
  window.playbackEventLog = playbackEventLog;
}

function emitPlaybackLog(event, fields = {}) {
  const entry = {
    ts: new Date().toISOString(),
    event,
    ...fields,
  };
  playbackEventLog = [...playbackEventLog.slice(-199), entry];
  if (typeof window !== 'undefined') {
    window.playbackEventLog = playbackEventLog;
  }
  console.info('[playback]', entry);

  fetch(apiUrl('/api/client-log'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(entry),
  }).catch(() => {
    // Logging should never block playback behavior.
  });
}

class MusicPlaybackEngine {
  constructor() {
    this.session = null;
  }

  _syncGlobalState() {
    activePlayback = this.session ? {
      id: this.session.id,
      type: this.session.type,
      state: this.session.state,
      offsetSeconds: this.session.offsetSeconds,
      totalSeconds: this.session.totalSeconds,
    } : null;
  }

  _disposeSynth() {
    if (!this.session?.synth) return;
    this.session.synth.dispose();
    this.session.synth = null;
  }

  _clearFinishTimer() {
    if (!this.session?.finishTimer) return;
    window.clearTimeout(this.session.finishTimer);
    this.session.finishTimer = null;
  }

  _scheduleFromOffset(playback, offsetSeconds) {
    const now = Tone.now();
    const synth = playback.poly ? new Tone.PolySynth(Tone.Synth).toDestination() : new Tone.Synth().toDestination();

    playback.events.forEach((event) => {
      const eventEnd = event.time + event.seconds;
      if (eventEnd <= offsetSeconds) return;
      const secondsIntoEvent = Math.max(0, offsetSeconds - event.time);
      const startDelay = Math.max(0, event.time - offsetSeconds);
      const duration = Math.max(0.02, event.seconds - secondsIntoEvent);
      if (playback.poly) {
        synth.triggerAttackRelease(event.pitches, duration, now + startDelay);
        return;
      }
      synth.triggerAttackRelease(event.pitches[0], duration, now + startDelay);
    });

    const remainingMs = Math.max(0, (playback.totalSeconds - offsetSeconds + 0.05) * 1000);
    this.session = {
      id: playback.id,
      type: playback.type,
      poly: playback.poly,
      events: playback.events,
      totalSeconds: playback.totalSeconds,
      offsetSeconds,
      startedAt: now,
      state: 'playing',
      synth,
      finishTimer: window.setTimeout(() => {
        if (this.session?.id === playback.id) {
          this.stop(playback.type, 'completed');
        }
      }, remainingMs),
    };
    this._syncGlobalState();
  }

  async start(playback) {
    await Tone.start();

    if (!playback.events.length || playback.totalSeconds <= 0) {
      emitPlaybackLog('playback_start_rejected_empty', { type: playback.type, id: playback.id });
      return;
    }

    if (this.session?.id === playback.id && this.session.state === 'playing') {
      emitPlaybackLog('playback_start_ignored_already_playing', { type: playback.type, id: playback.id });
      return;
    }

    if (this.session && this.session.id !== playback.id) {
      this.stop(this.session.type, 'interrupted_by_new_playback');
    }

    if (this.session?.id === playback.id && this.session.state === 'paused') {
      const resumeAt = this.session.offsetSeconds;
      this._disposeSynth();
      this._clearFinishTimer();
      this._scheduleFromOffset(playback, resumeAt);
      emitPlaybackLog('playback_resumed', { type: playback.type, id: playback.id, offsetSeconds: Number(resumeAt.toFixed(3)) });
      return;
    }

    this.stop(playback.type, 'restart');
    this._scheduleFromOffset(playback, 0);
    emitPlaybackLog('playback_started', {
      type: playback.type,
      id: playback.id,
      events: playback.events.length,
      totalSeconds: Number(playback.totalSeconds.toFixed(3)),
    });
  }

  pause(type) {
    if (!this.session || this.session.type !== type || this.session.state !== 'playing') return;
    const elapsed = this.session.offsetSeconds + (Tone.now() - this.session.startedAt);
    this.session.offsetSeconds = Math.min(elapsed, this.session.totalSeconds);
    this.session.state = 'paused';
    this._disposeSynth();
    this._clearFinishTimer();
    this._syncGlobalState();
    emitPlaybackLog('playback_paused', {
      type,
      id: this.session.id,
      offsetSeconds: Number(this.session.offsetSeconds.toFixed(3)),
    });
  }

  stop(type, reason = 'user_stop') {
    if (!this.session || this.session.type !== type) return;
    const { id, offsetSeconds, totalSeconds } = this.session;
    this._disposeSynth();
    this._clearFinishTimer();
    this.session = null;
    this._syncGlobalState();
    emitPlaybackLog('playback_stopped', {
      type,
      id,
      reason,
      progressSeconds: Number(Math.min(offsetSeconds, totalSeconds).toFixed(3)),
    });
  }

  stopAny(reason = 'reset') {
    if (!this.session) return;
    this.stop(this.session.type, reason);
  }
}

const playbackEngine = new MusicPlaybackEngine();

function resolveApiBaseUrl() {
  if (window.location.protocol === 'file:') {
    return 'http://127.0.0.1:8000';
  }
  return '';
}

function apiUrl(path) {
  return `${resolveApiBaseUrl()}${path}`;
}

function clearValidationHighlights() {
  document.querySelectorAll('.field-error').forEach((el) => el.classList.remove('field-error'));
}

function markFieldError(el) {
  if (el instanceof HTMLElement) {
    el.classList.add('field-error');
  }
}

function createValidationIssue(message, field = null) {
  return { message, field };
}

function normalizeMode(mode) {
  const cleaned = (mode || '').trim().toLowerCase();
  if (cleaned === 'major') return 'ionian';
  if (cleaned === 'minor' || cleaned === 'natural minor') return 'aeolian';
  return cleaned;
}

function validatePreferences() {
  const errors = [];
  const keyEl = document.getElementById('key');
  const modeEl = document.getElementById('primaryMode');
  const timeEl = document.getElementById('time');
  const tempoEl = document.getElementById('tempo');
  const barsPerVerseEl = document.getElementById('barsPerVerse');
  const keyRaw = keyEl.value?.trim() || '';
  const modeRaw = modeEl.value?.trim() || '';
  const timeRaw = timeEl.value?.trim() || '';
  const tempoRaw = tempoEl.value?.trim() || '';
  const barsPerVerseRaw = barsPerVerseEl.value?.trim() || '';

  if (keyRaw) {
    const m = keyRaw.match(/^([A-Ga-g])([#b]?)(m?)$/);
    if (!m) {
      errors.push(createValidationIssue('Key must look like C, F#, Bb, or Am.', keyEl));
    } else {
      const tonic = `${m[1].toUpperCase()}${m[2]}`;
      if (!VALID_TONICS.has(tonic)) {
        errors.push(createValidationIssue('Key tonic must be A–G with optional # or b accidental.', keyEl));
      }
      if (m[3] && modeRaw) {
        errors.push(createValidationIssue('Use either minor suffix in key (e.g., Am) OR Primary Mode (e.g., A + aeolian), not both.', modeEl));
      }
    }
  }

  if (modeRaw) {
    if (!VALID_MODES.has(modeRaw.toLowerCase())) {
      errors.push(createValidationIssue('Primary Mode must be one of: ionian, dorian, phrygian, lydian, mixolydian, aeolian, locrian (or major/minor).', modeEl));
    }
  }

  if (timeRaw) {
    const m = timeRaw.match(/^(\d{1,2})\s*\/\s*(\d{1,2})$/);
    if (!m) {
      errors.push(createValidationIssue('Time signature must be formatted like 4/4, 3/4, or 6/8.', timeEl));
    } else {
      const top = Number(m[1]);
      const bottom = Number(m[2]);
      if (top < 1 || top > 16) errors.push(createValidationIssue('Time-signature numerator must be between 1 and 16.', timeEl));
      if (![1, 2, 4, 8, 16, 32].includes(bottom)) errors.push(createValidationIssue('Time-signature denominator must be 1, 2, 4, 8, 16, or 32.', timeEl));
    }
  }

  if (tempoRaw) {
    const tempo = Number(tempoRaw);
    if (!Number.isFinite(tempo) || tempo < 30 || tempo > 300) {
      errors.push(createValidationIssue('Tempo must be between 30 and 300 BPM.', tempoEl));
    }
  }

  if (barsPerVerseRaw) {
    const barsPerVerse = Number(barsPerVerseRaw);
    if (!Number.isInteger(barsPerVerse) || barsPerVerse < 4 || barsPerVerse > 64) {
      errors.push(createValidationIssue('Bars per Verse must be a whole number between 4 and 64.', barsPerVerseEl));
    }
  }

  return errors;
}

function showErrors(errors) {
  clearValidationHighlights();
  if (!errors.length) {
    formErrorsEl.textContent = '';
    formErrorsEl.style.display = 'none';
    return;
  }
  const messages = errors.map((error) => {
    if (typeof error === 'string') return error;
    if (error?.field) markFieldError(error.field);
    return error?.message || String(error);
  });
  formErrorsEl.innerHTML = messages.map((message) => `• ${message}`).join('<br/>');
  formErrorsEl.style.display = 'block';

  const firstField = errors.find((error) => error?.field)?.field;
  if (firstField instanceof HTMLElement) {
    firstField.scrollIntoView({ behavior: 'smooth', block: 'center' });
    firstField.focus();
  }
}

function getRequestIdFromHeaders(headers) {
  if (!headers) return null;
  if (typeof headers.get === 'function') {
    return headers.get('X-Request-ID') || headers.get('x-request-id');
  }
  if (typeof headers === 'object') {
    return headers['X-Request-ID'] || headers['x-request-id'] || null;
  }
  return null;
}

function formatApiErrorMessage(error) {
  const data = error?.response?.data;
  let message = null;

  if (data && typeof data === 'object') {
    if (typeof data.message === 'string' && data.message.trim()) {
      message = data.message;
    } else if (typeof data.detail === 'string' && data.detail.trim()) {
      message = data.detail;
    } else if (typeof data.error === 'string' && data.error.trim()) {
      message = data.error;
    } else if (Array.isArray(data.detail)) {
      message = data.detail
        .map((d) => {
          if (typeof d === 'string') return d;
          if (!d || typeof d !== 'object') return JSON.stringify(d);
          const loc = Array.isArray(d.loc) ? d.loc.join('.') : 'request';
          const detailMessage = typeof d.msg === 'string' ? d.msg : JSON.stringify(d);
          return `${loc}: ${detailMessage}`;
        })
        .join(' | ');
    } else {
      try {
        message = JSON.stringify(data);
      } catch (_) {
        message = String(data);
      }
    }
  }

  if (!message) {
    message = error?.message ? String(error.message) : String(error);
  }

  const requestId = error?.request_id || getRequestIdFromHeaders(error?.response?.headers) || (data && typeof data === 'object' ? data.request_id : null);
  return requestId ? `${message} (request_id: ${requestId})` : message;
}

function getSectionRows() {
  return [...sectionsEl.querySelectorAll('.section-row')];
}

function getSectionLibrary() {
  return getSectionRows().map((row) => ({
    id: row.dataset.sectionId,
    label: row.querySelector('.section-label').value.trim(),
    is_verse: row.querySelector('.section-is-verse')?.checked ?? false,
    text: row.querySelector('.section-text').value,
  }));
}

function getArrangementMusicUnits() {
  const sectionById = new Map(getSectionLibrary().map((s) => [s.id, s]));
  const clusters = [];
  [...arrangementListEl.querySelectorAll('.arrangement-item')].forEach((item) => {
    const section = sectionById.get(item.dataset.sectionId);
    if (!section) return;
        const unit = section.is_verse ? 'verse' : (section.label || 'default');
    if (!clusters.includes(unit)) clusters.push(unit);
  });
  return clusters;
}

function refreshRegenerateClusterOptions() {
  const clusters = getArrangementMusicUnits();
  const selects = [regenerateClustersEl, satbRegenerateClustersEl].filter(Boolean);

  selects.forEach((selectEl) => {
    const previousOptions = [...selectEl.options].map((o) => o.value);
    const selected = new Set([...selectEl.selectedOptions].map((o) => o.value));
    const shouldDefaultSelectAll = selected.size === 0 || (previousOptions.length > 0 && selected.size === previousOptions.length);

    selectEl.innerHTML = clusters.map((cluster) => `<option value="${cluster}">${cluster}</option>`).join('');
    [...selectEl.options].forEach((option) => {
      option.selected = shouldDefaultSelectAll ? true : selected.has(option.value);
    });
  });
}

function buildSectionClusterMap(payload) {
  const sectionById = new Map(payload.sections.map((section) => [section.id, section]));
  const mapping = {};

  if (payload.arrangement.length) {
    let arrangedIndex = 0;
    payload.arrangement.forEach((item) => {
      const section = sectionById.get(item.section_id);
      if (!section) return;
      arrangedIndex += 1;
      mapping[`sec-${arrangedIndex}`] = section.is_verse ? 'verse' : (section.label || 'default');
    });
    return mapping;
  }

  payload.sections.forEach((section, idx) => {
    mapping[`sec-${idx + 1}`] = section.is_verse ? 'verse' : (section.label || 'default');
  });
  return mapping;
}

function activeDraftVersion() {
  return melodyDraftVersions.find((version) => version.id === activeDraftVersionId) || null;
}

function updateDraftVersionOptions() {
  if (!melodyDraftVersions.length) {
    draftVersionSelectEl.innerHTML = '<option value="">No versions yet</option>';
    draftVersionSelectEl.disabled = true;
    return;
  }
  draftVersionSelectEl.disabled = false;
  draftVersionSelectEl.innerHTML = melodyDraftVersions
    .map((version, idx) => `<option value="${version.id}">v${idx + 1} · ${version.label}</option>`)
    .join('');
  if (activeDraftVersionId) {
    draftVersionSelectEl.value = activeDraftVersionId;
  }
}

function renderMelody(score, heading = 'Melody') {
  melodyScore = normalizeScoreForRendering(score);
  document.getElementById('melodySheet').innerHTML = '';
  melodyMeta.textContent = JSON.stringify(melodyScore.meta, null, 2);
  document.getElementById('melodyChords').textContent = formatChordLine(melodyScore);
  drawStaff('melodySheet', heading, flattenVoice(melodyScore, 'soprano', { includeRests: true }), melodyScore.meta.time_signature);
  refreshPreview('melody');
}

function activeMelodyVersionId() {
  return activeDraftVersionId || null;
}

function activeSatbDraftVersions() {
  const melodyVersionId = activeMelodyVersionId();
  if (!melodyVersionId) return [];
  return satbDraftVersionsByMelodyVersion.get(melodyVersionId) || [];
}

function activeSatbDraftVersion() {
  return activeSatbDraftVersions().find((version) => version.id === activeSatbDraftVersionId) || null;
}

function updateSatbDraftVersionOptions() {
  const satbDraftVersions = activeSatbDraftVersions();
  if (!satbDraftVersions.length) {
    satbDraftVersionSelectEl.innerHTML = '<option value="">No versions yet</option>';
    satbDraftVersionSelectEl.disabled = true;
    return;
  }
  satbDraftVersionSelectEl.disabled = false;
  satbDraftVersionSelectEl.innerHTML = satbDraftVersions
    .map((version, idx) => `<option value="${version.id}">v${idx + 1} · ${version.label}</option>`)
    .join('');
  if (activeSatbDraftVersionId) {
    satbDraftVersionSelectEl.value = activeSatbDraftVersionId;
  }
}

let melodyPreviewScale = 1;
let satbPreviewScale = 1;

function setPreviewStatus(target, message) {
  const statusEl = target === 'melody' ? melodyPreviewStatusEl : satbPreviewStatusEl;
  if (statusEl) statusEl.textContent = message;
}

function clearPreview(target) {
  const previewEl = target === 'melody' ? melodyPreviewEl : satbPreviewEl;
  if (previewEl) previewEl.innerHTML = '';
  setPreviewStatus(target, 'No preview generated yet.');
}

function currentPreviewScale(target) {
  return target === 'melody' ? melodyPreviewScale : satbPreviewScale;
}

function applyPreviewScale(target) {
  const previewEl = target === 'melody' ? melodyPreviewEl : satbPreviewEl;
  const scale = currentPreviewScale(target);
  if (!previewEl) return;
  previewEl.querySelectorAll('svg').forEach((svg) => {
    svg.style.transform = `scale(${scale})`;
    svg.style.width = `${100 / scale}%`;
  });
}

function renderPreviewSvgs(target, artifacts, cacheHit) {
  const previewEl = target === 'melody' ? melodyPreviewEl : satbPreviewEl;
  if (!previewEl) return;

  if (!artifacts?.length) {
    previewEl.innerHTML = '';
    setPreviewStatus(target, 'Preview service returned no pages.');
    return;
  }

  previewEl.innerHTML = artifacts.map((artifact) => `
    <div class="preview-svg-page" data-page="${artifact.page}">
      ${artifact.svg}
    </div>
  `).join('');
  applyPreviewScale(target);
  setPreviewStatus(target, `Rendered ${artifacts.length} page(s)${cacheHit ? ' · cache hit' : ''}.`);
}

async function refreshPreview(target) {
  const score = target === 'melody' ? melodyScore : satbScore;
  if (!score) {
    setPreviewStatus(target, `Generate ${target.toUpperCase()} first.`);
    return;
  }

  setPreviewStatus(target, 'Rendering preview…');
  try {
    const res = await post('/api/engrave/preview', {
      score,
      preview_mode: target,
      include_all_pages: target === 'satb',
      scale: 42,
    });
    const payload = await res.json();
    renderPreviewSvgs(target, payload.artifacts, payload.cache_hit);
  } catch (error) {
    setPreviewStatus(target, `Preview failed: ${String(error.message || error)}`);
  }
}


function renderSatb(score, harmonizationNotes = null, heading = 'SATB') {
  satbScore = normalizeScoreForRendering(score);
  satbMeta.textContent = JSON.stringify({
    ...satbScore.meta,
    harmonization: harmonizationNotes || 'Chord-led SATB voicing with diatonic progression integrity checks.',
  }, null, 2);
  document.getElementById('satbChords').textContent = formatChordLine(satbScore);
  document.getElementById('satbSheet').innerHTML = '';

  ['soprano', 'alto', 'tenor', 'bass'].forEach((v) => drawStaff('satbSheet', heading === 'SATB' ? v.toUpperCase() : `${heading} · ${v.toUpperCase()}`, flattenVoice(satbScore, v, { includeRests: true }), satbScore.meta.time_signature));
  refreshPreview('satb');
}

function safeRenderSatb(score, harmonizationNotes, heading = 'SATB') {
  try {
    renderSatb(score, harmonizationNotes, heading);
    return true;
  } catch (_) {
    try {
      renderSatb(normalizeScoreForRendering(score), harmonizationNotes, heading);
      return true;
    } catch (_) {
      showErrors(['SATB generated, but score rendering is temporarily unavailable. Playback/export still work.']);
      return false;
    }
  }
}

function appendSatbDraftVersion(score, harmonizationNotes, label, melodyVersionId = activeMelodyVersionId()) {
  if (!melodyVersionId) {
    showErrors(['Generate or select a melody draft before creating SATB drafts.']);
    return;
  }
  const version = {
    id: `satb-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
    melodyVersionId,
    score,
    harmonizationNotes,
    label,
  };
  const satbDraftVersions = [...(satbDraftVersionsByMelodyVersion.get(melodyVersionId) || []), version];
  const capped = satbDraftVersions.length > MAX_DRAFT_VERSIONS
    ? satbDraftVersions.slice(satbDraftVersions.length - MAX_DRAFT_VERSIONS)
    : satbDraftVersions;
  satbDraftVersionsByMelodyVersion.set(melodyVersionId, capped);
  activeSatbDraftVersionId = version.id;
  stopPlayback('satb');
  safeRenderSatb(score, harmonizationNotes, label);
  updateSatbDraftVersionOptions();
}

function fingerprintNotes(events) {
  return events.map((event) => `${event.pitches.join('+')}@${event.seconds.toFixed(4)}`).join('|');
}

function stopActivePlayback() {
  playbackEngine.stopAny('manual_stop_active');
}

async function startPlayback(playback) {
  await playbackEngine.start(playback);
}

function pausePlayback(type) {
  playbackEngine.pause(type);
}

function stopPlayback(type) {
  playbackEngine.stop(type);
}

async function refineActiveMelody({ regenerate }) {
  if (!melodyScore) {
    showErrors([`Generate a melody before ${regenerate ? 'regenerating' : 'refining'}.`]);
    return;
  }
  const instruction = document.getElementById('instruction').value || (regenerate ? 'fresh melodic idea' : 'smooth out leaps');
  const melodyVersionId = activeMelodyVersionId();
  const currentVersion = activeDraftVersion();
  const payload = {
    score: melodyScore,
    instruction,
    regenerate,
  };

  if (regenerate) {
    payload.selected_units = [...regenerateClustersEl.selectedOptions].map((o) => o.value);
    payload.section_clusters = currentVersion?.sectionClusterMap || {};
  }

  const res = await post('/api/refine-melody', payload);
  const score = (await res.json()).score;
  if (regenerate) {
    appendDraftVersion(score, currentVersion?.sectionClusterMap || {}, 'Melody (regenerated)');
  } else {
    upsertActiveVersion(score, 'Melody (refined)');
    if (melodyVersionId) {
      satbDraftVersionsByMelodyVersion.delete(melodyVersionId);
    }
  }
  resetSatbStage();
  updateActionAvailability();
}

async function refineActiveSatb({ regenerate }) {
  if (!satbScore) {
    showErrors([`Generate SATB before ${regenerate ? 'regenerating' : 'refining'}.`]);
    return;
  }
  const instructionEl = document.getElementById('satbInstruction');
  const instruction = instructionEl?.value || (regenerate ? 'fresh melodic idea' : 'smooth out leaps');
  const currentVersion = activeDraftVersion();
  const payload = {
    score: satbScore,
    instruction,
    regenerate,
  };
  if (regenerate) {
    payload.selected_units = [...satbRegenerateClustersEl.selectedOptions].map((o) => o.value);
    payload.section_clusters = currentVersion?.sectionClusterMap || {};
  }

  const res = await post('/api/refine-satb', payload);
  const responsePayload = await res.json();
  appendSatbDraftVersion(
    responsePayload.score,
    responsePayload.harmonization_notes,
    regenerate ? 'SATB (regenerated)' : 'SATB (refined)',
  );
  updateActionAvailability();
}

function upsertActiveVersion(score, label) {
  const current = activeDraftVersion();
  if (!current) return;
  stopPlayback('melody');
  current.score = score;
  current.label = label;
  safeRenderMelody(score, label);
  updateDraftVersionOptions();
}

function appendDraftVersion(score, sectionClusterMap, label) {
  const version = {
    id: `draft-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
    score,
    sectionClusterMap,
    label,
  };
  melodyDraftVersions.push(version);
  if (melodyDraftVersions.length > MAX_DRAFT_VERSIONS) {
    melodyDraftVersions = melodyDraftVersions.slice(melodyDraftVersions.length - MAX_DRAFT_VERSIONS);
  }
  activeDraftVersionId = version.id;
  stopPlayback('melody');
  safeRenderMelody(score, label);
  updateDraftVersionOptions();
}

function describeSection(sectionId) {
  const match = getSectionLibrary().find((s) => s.id === sectionId);
  if (!match) return `Missing section (${sectionId})`;
  const label = match.is_verse ? 'Verse' : (match.label || 'Untitled Label');
  return `${label} (${sectionId})`;
}

function refreshArrangementLibrarySelect() {
  const current = arrangementSectionSelectEl.value;
  const sections = getSectionLibrary();
  arrangementSectionSelectEl.innerHTML = sections
    .map((s) => `<option value="${s.id}">${describeSection(s.id)}</option>`)
    .join('');
  if (!sections.length) {
    arrangementSectionSelectEl.innerHTML = '<option value="">No sections available</option>';
    arrangementSectionSelectEl.disabled = true;
    return;
  }
  arrangementSectionSelectEl.disabled = false;
  if (sections.some((s) => s.id === current)) {
    arrangementSectionSelectEl.value = current;
  }
  refreshRegenerateClusterOptions();
}


function derivePhraseBlocksFromText(text) {
  const blocks = (text || '')
    .replace(/\r\n/g, '\n')
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line.length > 0)
    .map((line) => ({ text: line, must_end_at_barline: true, breath_after_phrase: false, merge_with_next_phrase: false }));
  if (!blocks.length && (text || '').trim()) {
    blocks.push({ text: text.trim(), must_end_at_barline: true, breath_after_phrase: false, merge_with_next_phrase: false });
  }
  return blocks;
}


function countPhraseBlockSyllables(phraseBlocks) {
  return phraseBlocks
    .flatMap((block) => (block.text || '').split(/\s+/))
    .map((token) => token.trim())
    .filter(Boolean)
    .reduce((sum, token) => sum + token.split('-').filter(Boolean).length, 0);
}

function recommendAnacrusisBeats(phraseBlocks) {
  const { beatsPerMeasure } = parseTimeSignature(document.getElementById('time').value || '4/4');
  const syllables = countPhraseBlockSyllables(phraseBlocks);
  if (syllables < 7 || beatsPerMeasure <= 0) return 0;
  const remainder = syllables % Math.round(beatsPerMeasure);
  if (remainder === 1) return 1;
  if (beatsPerMeasure >= 4 && remainder === 3 && syllables >= 11) return 1;
  return 0;
}

function refreshArrangementAnacrusisUI(item) {
  const phraseBlocks = getArrangementItemPhraseBlocks(item);
  const recommended = recommendAnacrusisBeats(phraseBlocks);
  const modeSelect = item.querySelector('.arrangement-anacrusis-mode');
  const beatsInput = item.querySelector('.arrangement-anacrusis-beats');
  const recommendationEl = item.querySelector('.arrangement-anacrusis-recommendation');
  if (!modeSelect || !beatsInput || !recommendationEl) return;

  recommendationEl.textContent = recommended > 0
    ? `Auto recommendation: ${recommended} beat pickup (stable for unchanged inputs).`
    : 'Auto recommendation: no pickup (preferred; stable for unchanged inputs).';

  const mode = modeSelect.value || 'off';
  const manualMode = mode === 'manual';
  beatsInput.disabled = !manualMode;
  beatsInput.value = manualMode
    ? (beatsInput.value || `${recommended || 1}`)
    : `${recommended}`;
}

function renderPhraseBlocksEditor(item, phraseBlocks) {
  const host = item.querySelector('.arrangement-phrase-blocks');
  if (!host) return;
  host.innerHTML = phraseBlocks.map((block, idx) => {
    const isLast = idx === phraseBlocks.length - 1;
    const mergeChecked = block.merge_with_next_phrase && !isLast;
    return `
      <div class="phrase-block-row" data-phrase-index="${idx}">
        <input class="phrase-block-text" value="${block.text}" readonly />
        <label class="phrase-block-toggle">
          <input type="checkbox" class="arrangement-merge-next-toggle" ${mergeChecked ? 'checked' : ''} ${isLast ? 'disabled' : ''} />
          merge with next phrase
        </label>
        <label class="phrase-block-toggle">
          <input type="checkbox" class="arrangement-breath-after-toggle" ${block.breath_after_phrase ? 'checked' : ''} />
          breath after phrase
        </label>
      </div>
    `;
  }).join('');
}

function getArrangementItemPhraseBlocks(item) {
  const rows = [...item.querySelectorAll('.phrase-block-row')];
  return rows
    .map((row, idx) => ({
      text: row.querySelector('.phrase-block-text')?.value || '',
      must_end_at_barline: true,
      breath_after_phrase: row.querySelector('.arrangement-breath-after-toggle')?.checked ?? false,
      merge_with_next_phrase: (row.querySelector('.arrangement-merge-next-toggle')?.checked ?? false) && idx < rows.length - 1,
    }))
    .filter((block) => block.text.trim().length > 0);
}

function addArrangementItem(sectionId, phraseBlocks = null, anacrusisMode = "off", anacrusisBeats = 0) {
  if (!sectionId) return;
  const section = getSectionLibrary().find((entry) => entry.id === sectionId);
  const resolvedPhraseBlocks = phraseBlocks || derivePhraseBlocksFromText(section?.text || '');
  const item = document.createElement('div');
  item.className = 'arrangement-item';
  item.dataset.sectionId = sectionId;
  item.innerHTML = `
    <div class="arrangement-item-main">
      <div class="arrangement-item-meta"></div>
      <label>Anacrusis handling
        <select class="arrangement-anacrusis-mode">
          <option value="off">Off (default)</option>
          <option value="auto">Auto recommend</option>
          <option value="manual">Manual pickup</option>
        </select>
      </label>
      <label>Anacrusis beats (before section downbeat)
        <input class="arrangement-anacrusis-beats" type="number" min="0" max="3.5" step="0.5" value="0" disabled />
      </label>
      <div class="arrangement-anacrusis-help">Pickup creates a short first bar for this section instance; phrase ends still align to barlines.</div>
      <div class="arrangement-anacrusis-recommendation"></div>
      <div class="arrangement-phrase-blocks"></div>
    </div>
    <div class="arrangement-item-controls">
      <button type="button" class="arrangement-up">↑</button>
      <button type="button" class="arrangement-down">↓</button>
      <button type="button" class="arrangement-remove">Remove</button>
    </div>
  `;
  arrangementListEl.appendChild(item);
  renderPhraseBlocksEditor(item, resolvedPhraseBlocks);
  const modeSelect = item.querySelector('.arrangement-anacrusis-mode');
  const beatsInput = item.querySelector('.arrangement-anacrusis-beats');
  if (modeSelect) modeSelect.value = anacrusisMode || 'off';
  if (beatsInput) beatsInput.value = Number(anacrusisBeats) || 0;
  refreshArrangementAnacrusisUI(item);
  refreshArrangementLabels();
}

function refreshArrangementLabels() {
  let verseNumber = 0;
  [...arrangementListEl.querySelectorAll('.arrangement-item')].forEach((item, idx) => {
    const meta = item.querySelector('.arrangement-item-meta');
    if (!meta) return;
    const section = getSectionLibrary().find((entry) => entry.id === item.dataset.sectionId);
    if (section?.is_verse) {
      verseNumber += 1;
      meta.textContent = `${idx + 1}. Verse ${verseNumber}`;
    } else {
      meta.textContent = `${idx + 1}. ${describeSection(item.dataset.sectionId)}`;
    }
  });
  refreshRegenerateClusterOptions();
}

function clearSectionsAndArrangement() {
  sectionsEl.innerHTML = '';
  arrangementListEl.innerHTML = '';
  refreshArrangementLibrarySelect();
  refreshRegenerateClusterOptions();
}

function loadHymnTestData() {
  clearSectionsAndArrangement();
  HYMN_TEST_DATA_SECTIONS.forEach((section) => addSectionRow(section.label, section.text, section.is_verse || false));

  const rows = getSectionRows();
  addArrangementItem(rows[0]?.dataset.sectionId, null, HYMN_TEST_DATA_ANACRUSIS_MODE, HYMN_TEST_DATA_ANACRUSIS_BEATS);
  addArrangementItem(rows[1]?.dataset.sectionId, null, HYMN_TEST_DATA_ANACRUSIS_MODE, HYMN_TEST_DATA_ANACRUSIS_BEATS);
  addArrangementItem(rows[2]?.dataset.sectionId, null, HYMN_TEST_DATA_ANACRUSIS_MODE, HYMN_TEST_DATA_ANACRUSIS_BEATS);

  document.getElementById('key').value = 'G';
  document.getElementById('primaryMode').value = 'major';
  document.getElementById('time').value = '3/4';
  document.getElementById('tempo').value = '88';
  document.getElementById('mood').value = 'Prayerful';
  document.getElementById('lyricPreset').value = 'syllabic';
  document.getElementById('barsPerVerse').value = '16';
  document.getElementById('instruction').value = 'Keep a reverent, singable contour.';

  melodyScore = null;
  satbScore = null;
  melodyDraftVersions = [];
  activeDraftVersionId = null;
  satbDraftVersionsByMelodyVersion = new Map();
  activeSatbDraftVersionId = null;
  stopActivePlayback();
  document.getElementById('melodySheet').innerHTML = '';
  document.getElementById('satbSheet').innerHTML = '';
  document.getElementById('melodyChords').textContent = formatChordLine(null);
  document.getElementById('satbChords').textContent = formatChordLine(null);
  melodyMeta.textContent = '';
  satbMeta.textContent = '';
  clearPreview('melody');
  clearPreview('satb');

  clearValidationHighlights();
  showErrors([]);
  refreshArrangementLibrarySelect();
  refreshArrangementLabels();
  refreshRegenerateClusterOptions();
  updateDraftVersionOptions();
  updateSatbDraftVersionOptions();
  updateActionAvailability();
}

function setSectionMode(row, isSaved) {
  row.dataset.mode = isSaved ? 'saved' : 'edit';
  const lockable = ['.section-label', '.section-text'];
  for (const selector of lockable) {
    const el = row.querySelector(selector);
    if (el) el.readOnly = isSaved;
  }

  const toggleBtn = row.querySelector('.toggle-section-mode');
  if (toggleBtn) {
    toggleBtn.textContent = isSaved ? 'Edit section' : 'Save section';
  }
}

function addSectionRow(defaultLabel = 'verse', text = '', isVerse = false) {
  const row = document.createElement('div');
  row.className = 'section-row';
  row.dataset.sectionId = `section-${++sectionIdCounter}`;
  row.innerHTML = `
    <div class="section-row-controls">
      <button type="button" class="move-section-up">↑</button>
      <button type="button" class="move-section-down">↓</button>
      <button type="button" class="toggle-section-mode">Save section</button>
    </div>
    <label>Section Label <input class="section-label" value="${defaultLabel}" placeholder="e.g. Verse, Chorus, Tag" /></label>
    <label class="section-verse-toggle"><input type="checkbox" class="section-is-verse" ${isVerse ? 'checked' : ''} /> Verse section</label>
    <label>Lyrics <textarea class="section-text" placeholder="Enter lyrics here">${text}</textarea></label>
  `;
  setSectionMode(row, false);
  sectionsEl.appendChild(row);
  refreshArrangementLibrarySelect();
}

sectionsEl.addEventListener('click', (event) => {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;
  const row = target.closest('.section-row');
  if (!row) return;

  if (target.classList.contains('move-section-up')) {
    const prev = row.previousElementSibling;
    if (prev) {
      sectionsEl.insertBefore(row, prev);
      refreshArrangementLibrarySelect();
    }
  }

  if (target.classList.contains('move-section-down')) {
    const next = row.nextElementSibling;
    if (next) {
      sectionsEl.insertBefore(next, row);
      refreshArrangementLibrarySelect();
    }
  }

  if (target.classList.contains('toggle-section-mode')) {
    setSectionMode(row, row.dataset.mode !== 'saved');
  }
});

sectionsEl.addEventListener('input', (event) => {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;
  if (target.classList.contains('section-label') || target.classList.contains('section-is-verse')) {
    refreshArrangementLibrarySelect();
    refreshArrangementLabels();
  }
});

arrangementListEl.addEventListener('click', (event) => {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;
  const item = target.closest('.arrangement-item');
  if (!item) return;

  if (target.classList.contains('arrangement-up')) {
    const prev = item.previousElementSibling;
    if (prev) arrangementListEl.insertBefore(item, prev);
  }

  if (target.classList.contains('arrangement-down')) {
    const next = item.nextElementSibling;
    if (next) arrangementListEl.insertBefore(next, item);
  }

  if (target.classList.contains('arrangement-remove')) {
    item.remove();
  }

  refreshArrangementLabels();
});

arrangementListEl.addEventListener('input', (event) => {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;
  if (target.classList.contains('arrangement-anacrusis-mode') || target.classList.contains('arrangement-anacrusis-beats')) {
    const arrangementItem = target.closest('.arrangement-item');
    if (arrangementItem) refreshArrangementAnacrusisUI(arrangementItem);
  }
});

function collectPayload() {
  const errors = validatePreferences();
  const sectionLibrary = getSectionLibrary().filter((s) => s.text.trim().length > 0);
  const sectionById = new Map(sectionLibrary.map((s) => [s.id, s]));
  const sectionsCard = sectionsEl.closest('.card');
  const arrangementCard = arrangementListEl.closest('.card');
  const arrangement = [...arrangementListEl.querySelectorAll('.arrangement-item')].map((item) => ({
    section_id: item.dataset.sectionId,
    is_verse: sectionById.get(item.dataset.sectionId)?.is_verse || false,
    anacrusis_mode: item.querySelector('.arrangement-anacrusis-mode')?.value || 'off',
    anacrusis_beats: Number(item.querySelector('.arrangement-anacrusis-beats')?.value) || 0,
    phrase_blocks: getArrangementItemPhraseBlocks(item),
  }));

  if (!sectionLibrary.length) {
    errors.push(createValidationIssue('Please add lyrics to at least one section before generating a melody.', sectionsCard));
  }

  if (arrangement.length && !arrangement.every((item) => sectionById.has(item.section_id))) {
    errors.push(createValidationIssue('Arrangement references one or more missing sections. Remove outdated items and re-add sections from the picker.', arrangementCard));
  }

  if (arrangement.length && !arrangement.some((item) => sectionById.has(item.section_id))) {
    errors.push(createValidationIssue('Arrangement must contain at least one valid section item with lyrics.', arrangementCard));
  }

  if (arrangement.some((item) => !item.phrase_blocks.length)) {
    errors.push(createValidationIssue('Each arrangement item must include at least one phrase block.', arrangementCard));
  }

  if (!arrangement.length) {
    errors.push(createValidationIssue('Add at least one section to the arrangement list so the melody has a song order.', arrangementCard));
  }

  if (errors.length) {
    showErrors(errors);
    const validationError = new Error('Validation failed');
    validationError.isValidationError = true;
    throw validationError;
  }
  showErrors([]);

  return {
    sections: sectionLibrary,
    arrangement,
    preferences: {
      key: document.getElementById('key').value || null,
      primary_mode: normalizeMode(document.getElementById('primaryMode').value) || null,
      time_signature: document.getElementById('time').value || null,
      tempo_bpm: document.getElementById('tempo').value ? Number(document.getElementById('tempo').value) : null,
      mood: document.getElementById('mood').value,
      lyric_rhythm_preset: document.getElementById('lyricPreset').value,
      bars_per_verse: document.getElementById('barsPerVerse').value ? Number(document.getElementById('barsPerVerse').value) : null,
    }
  };
}

function flattenVoice(score, voice, { includeRests = false } = {}) {
  const voiceNotes = score.measures.flatMap((m) => m.voices[voice]);
  return includeRests ? voiceNotes : voiceNotes.filter((n) => !n.is_rest);
}


function normalizeScoreForRendering(score) {
  const { beatsPerMeasure } = parseTimeSignature(score?.meta?.time_signature);
  const voices = ['soprano', 'alto', 'tenor', 'bass'];
  const normalized = structuredClone(score);

  const normalizedByVoice = Object.fromEntries(voices.map((voice) => [voice, []]));
  let maxMeasures = 0;

  voices.forEach((voice) => {
    const source = flattenVoice(score, voice, { includeRests: true });
    if (!source.length) return;

    let current = [];
    let used = 0;
    source.forEach((note) => {
      let remaining = Number(note.beats) || 0;
      let firstChunk = true;
      while (remaining > 0.0001) {
        const room = Math.max(0, beatsPerMeasure - used);
        if (room < 0.0001) {
          normalizedByVoice[voice].push(current);
          current = [];
          used = 0;
          continue;
        }
        const chunk = Math.min(remaining, room);
        const clone = { ...note, beats: chunk };
        if (!clone.is_rest && !firstChunk) {
          clone.lyric = null;
          clone.lyric_mode = 'tie_continue';
        }
        current.push(clone);
        remaining -= chunk;
        used += chunk;
        firstChunk = false;
        if (used >= beatsPerMeasure - 0.0001) {
          normalizedByVoice[voice].push(current);
          current = [];
          used = 0;
        }
      }
    });

    if (current.length) {
      if (used < beatsPerMeasure - 0.0001) {
        current.push({ pitch: 'REST', beats: beatsPerMeasure - used, is_rest: true, section_id: 'padding', lyric_mode: 'none' });
      }
      normalizedByVoice[voice].push(current);
    }

    maxMeasures = Math.max(maxMeasures, normalizedByVoice[voice].length);
  });

  if (maxMeasures === 0) return normalized;

  voices.forEach((voice) => {
    while (normalizedByVoice[voice].length < maxMeasures) {
      normalizedByVoice[voice].push([{ pitch: 'REST', beats: beatsPerMeasure, is_rest: true, section_id: 'padding', lyric_mode: 'none' }]);
    }
  });

  normalized.measures = Array.from({ length: maxMeasures }, (_, idx) => ({
    number: idx + 1,
    voices: Object.fromEntries(voices.map((voice) => [voice, normalizedByVoice[voice][idx]])),
  }));

  const byMeasure = new Map((normalized.chord_progression || []).map((ch) => [ch.measure_number, ch]));
  normalized.chord_progression = Array.from({ length: maxMeasures }, (_, idx) => byMeasure.get(idx + 1) || {
    measure_number: idx + 1,
    section_id: normalized.measures[idx].voices.soprano[0]?.section_id || 'padding',
    symbol: 'C',
    degree: 1,
    pitch_classes: [0, 4, 7],
  });

  return normalized;
}


function formatChordLine(score) {
  if (!score?.chord_progression?.length) return 'Chord progression: —';
  return `Chord progression: ${score.chord_progression.map(c => `m${c.measure_number}:${c.symbol}`).join(' | ')}`;
}

function noteToVexKey(p) {
  const m = p.match(/^([A-G]#?b?)(\d)$/);
  if (!m) return 'c/4';
  return `${m[1].toLowerCase()}/${m[2]}`;
}

function parseTimeSignature(timeSignature) {
  const match = String(timeSignature || '').trim().match(/^(\d+)\s*\/\s*(\d+)$/);
  if (!match) {
    return { beatsPerMeasure: 4, display: '4/4' };
  }
  const numerator = Number(match[1]);
  const denominator = Number(match[2]);
  if (!Number.isFinite(numerator) || !Number.isFinite(denominator) || denominator <= 0) {
    return { beatsPerMeasure: 4, display: '4/4' };
  }
  return {
    beatsPerMeasure: (numerator * 4) / denominator,
    display: `${numerator}/${denominator}`,
  };
}

function splitBeatsIntoDurations(beats) {
  const chunks = [];
  let remaining = Math.max(0, Number(beats) || 0);
  const units = [
    { beats: 4, duration: 'w' },
    { beats: 3, duration: 'h.' },
    { beats: 2, duration: 'h' },
    { beats: 1.5, duration: 'q.' },
    { beats: 1, duration: 'q' },
    { beats: 0.5, duration: '8' },
  ];

  while (remaining > 0.001) {
    const matched = units.find((unit) => remaining + 0.001 >= unit.beats);
    if (!matched) {
      chunks.push('8');
      remaining -= 0.5;
      continue;
    }
    chunks.push(matched.duration);
    remaining -= matched.beats;
  }
  return chunks;
}

function buildVexNotes(notes, timeSignature) {
  const { beatsPerMeasure } = parseTimeSignature(timeSignature);
  const staveNotes = [];
  let beatCursor = 0;

  const appendRestPadding = (beatsToPad) => {
    splitBeatsIntoDurations(beatsToPad).forEach((duration) => {
      staveNotes.push(`b/4/${duration}r`);
      const durationBeats = duration === 'w' ? 4 : duration === 'h.' ? 3 : duration === 'h' ? 2 : duration === 'q.' ? 1.5 : duration === 'q' ? 1 : 0.5;
      beatCursor += durationBeats;
      if (beatCursor >= beatsPerMeasure - 0.001) beatCursor = 0;
    });
  };

  notes.forEach((note) => {
    const key = note.is_rest ? 'b/4' : noteToVexKey(note.pitch);
    splitBeatsIntoDurations(note.beats).forEach((duration) => {
      const durationBeats = duration === 'w' ? 4 : duration === 'h.' ? 3 : duration === 'h' ? 2 : duration === 'q.' ? 1.5 : duration === 'q' ? 1 : 0.5;
      if (beatCursor > 0.001 && beatCursor + durationBeats > beatsPerMeasure + 0.001) {
        appendRestPadding(beatsPerMeasure - beatCursor);
      }
      if (beatCursor >= beatsPerMeasure - 0.001) beatCursor = 0;
      staveNotes.push(`${key}/${duration}${note.is_rest ? 'r' : ''}`);
      beatCursor += durationBeats;
      if (beatCursor >= beatsPerMeasure - 0.001) beatCursor = 0;
    });
  });

  if (beatCursor > 0.001 && beatCursor < beatsPerMeasure - 0.001) {
    appendRestPadding(beatsPerMeasure - beatCursor);
  }

  return staveNotes;
}

function drawStaff(containerId, title, notes, timeSignature) {
  const root = document.getElementById(containerId);
  const wrap = document.createElement('div');
  wrap.className = 'staff-wrap';
  const heading = document.createElement('h4');
  heading.textContent = title;
  wrap.appendChild(heading);

  const vfDiv = document.createElement('div');
  const vfDivId = `${containerId}-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
  vfDiv.id = vfDivId;
  wrap.appendChild(vfDiv);
  root.appendChild(wrap);

  const vexRoot = window.Vex?.Flow ? window.Vex.Flow : window.Vex;
  if (!vexRoot?.Factory) {
    throw new Error('VexFlow failed to load. Staff rendering is unavailable.');
  }
  const { Factory } = vexRoot;
  const factory = new Factory({ renderer: { elementId: vfDivId, width: 920, height: 180 } });
  const score = factory.EasyScore();
  const system = factory.System({ x: 10, y: 20, width: 880 });

  const displayNotes = notes.slice(0, 32);
  const staveNotes = buildVexNotes(displayNotes, timeSignature);
  if (!staveNotes.length) {
    const emptyState = document.createElement('div');
    emptyState.textContent = 'No notes available for this staff.';
    emptyState.className = 'staff-empty-state';
    wrap.appendChild(emptyState);
    return;
  }
  const { display } = parseTimeSignature(timeSignature);
  system.addStave({ voices: [score.voice(score.notes(staveNotes.join(', ')), { time: display })] }).addClef('treble').addTimeSignature(display);
  factory.draw();

  const lyricLine = document.createElement('div');
  lyricLine.style.fontFamily = 'monospace';
  lyricLine.style.fontSize = '12px';
  lyricLine.style.marginTop = '6px';
  const lyricTokens = [];
  displayNotes.forEach((note, idx) => {
    if (idx > 0) {
      const previousSection = displayNotes[idx - 1]?.section_id;
      const currentSection = note.section_id;
      if (previousSection && currentSection && previousSection !== currentSection) {
        lyricTokens.push('‖');
      }
    }
    lyricTokens.push(note.lyric ? `${note.lyric}(${note.lyric_mode || 'single'})` : '—');
  });
  lyricLine.textContent = lyricTokens.join(' | ');
  wrap.appendChild(lyricLine);
}

async function post(url, payload) {
  let res;
  try {
    res = await fetch(apiUrl(url), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
  } catch (_) {
    throw new Error('Failed to fetch. Confirm the API server is running and reachable, then refresh and try again.');
  }
  if (!res.ok) {
    const text = await res.text();
    let data = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch (_) {
      data = null;
    }

    const requestIdFromHeader = res.headers.get('X-Request-ID');
    const requestIdFromBody = typeof data === 'object' && data !== null ? data.request_id : null;
    const requestId = requestIdFromHeader || requestIdFromBody || null;

    let message = 'Request failed.';
    if (data && typeof data === 'object') {
      if (typeof data.message === 'string' && data.message.trim()) {
        message = data.message;
      } else if (typeof data.detail === 'string' && data.detail.trim()) {
        message = data.detail;
      } else if (typeof data.error === 'string' && data.error.trim()) {
        message = data.error;
      } else if (Array.isArray(data.detail)) {
        message = data.detail
          .map((d) => {
            if (typeof d === 'string') return d;
            if (!d || typeof d !== 'object') return JSON.stringify(d);
            const loc = Array.isArray(d.loc) ? d.loc.join('.') : 'request';
            const detailMessage = typeof d.msg === 'string' ? d.msg : JSON.stringify(d);
            return `${loc}: ${detailMessage}`;
          })
          .join(' | ');
      } else {
        try {
          message = JSON.stringify(data);
        } catch (_) {
          message = String(data);
        }
      }
    } else if (text?.trim()) {
      message = text;
    }

    const fullMessage = requestId ? `${message} (request_id: ${requestId})` : message;
    const error = new Error(fullMessage);
    error.response = { data, status: res.status, headers: res.headers };
    error.request_id = requestId;
    throw error;
  }
  return res;
}


function setButtonEnabled(button, enabled, disabledReason = '') {
  button.disabled = !enabled;
  button.setAttribute('aria-disabled', String(!enabled));
  if (!enabled && disabledReason) {
    button.title = disabledReason;
  } else {
    button.removeAttribute('title');
  }
}

function updateWorkflowStatus() {
  if (satbScore) {
    workflowStageLabelEl.textContent = 'Current stage: 4) Export';
    workflowStageHintEl.textContent = 'SATB is ready. You can preview and export PDF/MusicXML now.';
    return;
  }

  if (melodyScore) {
    workflowStageLabelEl.textContent = 'Current stage: 3) SATB Harmonization';
    workflowStageHintEl.textContent = 'Melody is ready. Generate SATB next to unlock export.';
    return;
  }

  workflowStageLabelEl.textContent = 'Current stage: 1) Lyrics + Structure';
  workflowStageHintEl.textContent = 'Write lyrics, define section labels, and arrange Verse/non-verse items before generating a melody.';
}

function updateActionAvailability() {
  const hasMelody = Boolean(melodyScore);
  const hasSatb = Boolean(satbScore);

  setButtonEnabled(generateMelodyBtn, true);
  setButtonEnabled(refineBtn, hasMelody, 'Generate a melody first.');
  setButtonEnabled(regenerateBtn, hasMelody, 'Generate a melody first.');
  setButtonEnabled(startMelodyBtn, hasMelody, 'Generate a melody first.');
  setButtonEnabled(pauseMelodyBtn, hasMelody, 'Generate a melody first.');
  setButtonEnabled(stopMelodyBtn, hasMelody, 'Generate a melody first.');
  setButtonEnabled(generateSATBBtn, hasMelody, 'Generate a melody first.');
  setButtonEnabled(refineSatbBtn, hasSatb, 'Generate SATB first.');
  setButtonEnabled(regenerateSatbBtn, hasSatb, 'Generate SATB first.');
  setButtonEnabled(startSATBBtn, hasSatb, 'Generate SATB first.');
  setButtonEnabled(pauseSATBBtn, hasSatb, 'Generate SATB first.');
  setButtonEnabled(stopSATBBtn, hasSatb, 'Generate SATB first.');
  setButtonEnabled(exportPDFBtn, hasSatb, 'Generate SATB first.');
  setButtonEnabled(exportMusicXMLBtn, hasSatb, 'Generate SATB first.');

  updateWorkflowStatus();
}

function safeRenderMelody(score, heading) {
  try {
    renderMelody(score, heading);
    return true;
  } catch (_) {
    try {
      renderMelody(normalizeScoreForRendering(score), heading);
      return true;
    } catch (_) {
      showErrors(['Melody generated, but sheet rendering is temporarily unavailable. Playback/export still work.']);
      return false;
    }
  }
}

function resetSatbStage({ clearHistory = false } = {}) {
  stopPlayback('satb');
  satbScore = null;
  activeSatbDraftVersionId = null;
  if (clearHistory) {
    satbDraftVersionsByMelodyVersion = new Map();
  }
  satbMeta.textContent = '';
  document.getElementById('satbChords').textContent = formatChordLine(null);
  document.getElementById('satbSheet').innerHTML = '';
  clearPreview('satb');
  updateSatbDraftVersionOptions();
  updateActionAvailability();
}

function syncSatbStageForActiveMelody() {
  const satbVersions = activeSatbDraftVersions();
  if (!satbVersions.length) {
    resetSatbStage();
    return;
  }
  const preferred = satbVersions.find((version) => version.id === activeSatbDraftVersionId) || satbVersions[satbVersions.length - 1];
  activeSatbDraftVersionId = preferred.id;
  safeRenderSatb(preferred.score, preferred.harmonizationNotes, preferred.label);
  updateSatbDraftVersionOptions();
  updateActionAvailability();
}

document.getElementById('addSection').onclick = () => addSectionRow();
document.getElementById('addArrangementItem').onclick = () => addArrangementItem(arrangementSectionSelectEl.value);
document.getElementById('time').addEventListener('input', () => {
  [...arrangementListEl.querySelectorAll('.arrangement-item')].forEach((item) => refreshArrangementAnacrusisUI(item));
});
if (loadTestDataBtn) loadTestDataBtn.onclick = loadHymnTestData;
if (typeof window !== 'undefined') {
  window.loadHymnTestData = loadHymnTestData;
}

loadHymnTestData();

generateMelodyBtn.onclick = async () => {
  let res;
  let payload;
  try {
    payload = collectPayload();
    res = await post('/api/generate-melody', payload);
  } catch (error) {
    if (error?.isValidationError) return;
    showErrors([formatApiErrorMessage(error)]);
    return;
  }
  const score = (await res.json()).score;
  const sectionClusterMap = buildSectionClusterMap(payload);
  melodyDraftVersions = [];
  activeDraftVersionId = null;
  appendDraftVersion(score, sectionClusterMap, 'Melody');
  resetSatbStage({ clearHistory: true });
  updateActionAvailability();
};

refineBtn.onclick = async () => refineActiveMelody({ regenerate: false });

regenerateBtn.onclick = async () => refineActiveMelody({ regenerate: true });

draftVersionSelectEl.onchange = () => {
  const selectedId = draftVersionSelectEl.value;
  const version = melodyDraftVersions.find((item) => item.id === selectedId);
  if (!version) return;
  activeDraftVersionId = version.id;
  stopPlayback('melody');
  safeRenderMelody(version.score, version.label);
  syncSatbStageForActiveMelody();
};

function buildTimedPlaybackEvents(events, pauseSeconds) {
  let cursor = 0;
  let previousSectionId = null;
  const timedEvents = events.map((event) => {
    const currentSectionId = event.sectionId && event.sectionId !== 'padding' ? event.sectionId : previousSectionId;
    if (previousSectionId && currentSectionId && currentSectionId !== previousSectionId) {
      cursor += pauseSeconds;
    }
    const current = { pitches: event.pitches, seconds: event.seconds, time: cursor };
    cursor += event.seconds;
    previousSectionId = currentSectionId || previousSectionId;
    return current;
  });
  return { timedEvents, totalSeconds: cursor };
}

function arrangementPauseSeconds(score) {
  void score;
  return 1;
}

startMelodyBtn.onclick = async () => {
  if (!melodyScore) {
    showErrors(['Generate a melody before playback.']);
    return;
  }
  const events = flattenVoice(melodyScore, 'soprano').map((n) => ({
    pitches: [n.pitch],
    seconds: (60 / melodyScore.meta.tempo_bpm) * n.beats,
    sectionId: n.section_id,
  }));
  const { timedEvents, totalSeconds } = buildTimedPlaybackEvents(events, arrangementPauseSeconds(melodyScore));
  await startPlayback({
    id: `melody:${activeDraftVersionId || 'current'}:${fingerprintNotes(events)}:${melodyScore.meta.tempo_bpm}:gap1bar`,
    type: 'melody',
    poly: false,
    events: timedEvents,
    totalSeconds,
  });
};

pauseMelodyBtn.onclick = () => pausePlayback('melody');
stopMelodyBtn.onclick = () => stopPlayback('melody');

generateSATBBtn.onclick = async () => {
  if (!melodyScore) {
    showErrors(['Generate a melody before SATB harmonization.']);
    return;
  }
  const res = await post('/api/generate-satb', { score: melodyScore });
  const payload = await res.json();
  appendSatbDraftVersion(payload.score, payload.harmonization_notes, 'SATB');
  updateActionAvailability();
};

refineSatbBtn.onclick = async () => refineActiveSatb({ regenerate: false });
regenerateSatbBtn.onclick = async () => refineActiveSatb({ regenerate: true });

satbDraftVersionSelectEl.onchange = () => {
  const selectedId = satbDraftVersionSelectEl.value;
  const version = activeSatbDraftVersions().find((item) => item.id === selectedId);
  if (!version) return;
  activeSatbDraftVersionId = version.id;
  stopPlayback('satb');
  safeRenderSatb(version.score, version.harmonizationNotes, version.label);
  updateActionAvailability();
};

startSATBBtn.onclick = async () => {
  if (!satbScore) {
    showErrors(['Generate SATB before playback.']);
    return;
  }
  const soprano = flattenVoice(satbScore, 'soprano');
  const alto = flattenVoice(satbScore, 'alto');
  const tenor = flattenVoice(satbScore, 'tenor');
  const bass = flattenVoice(satbScore, 'bass');
  const chordEvents = soprano.map((sn, i) => ({
    pitches: [sn.pitch, alto[i]?.pitch, tenor[i]?.pitch, bass[i]?.pitch].filter(Boolean),
    seconds: (60 / satbScore.meta.tempo_bpm) * sn.beats,
    sectionId: sn.section_id,
  }));
  const { timedEvents, totalSeconds } = buildTimedPlaybackEvents(chordEvents, arrangementPauseSeconds(satbScore));
  await startPlayback({
    id: `satb:${activeSatbDraftVersionId || 'current'}:${fingerprintNotes(chordEvents)}:${satbScore.meta.tempo_bpm}:gap1bar`,
    type: 'satb',
    poly: true,
    events: timedEvents,
    totalSeconds,
  });
};

pauseSATBBtn.onclick = () => pausePlayback('satb');
stopSATBBtn.onclick = () => stopPlayback('satb');

exportPDFBtn.onclick = async () => {
  if (!satbScore) {
    showErrors(['Generate SATB before exporting PDF.']);
    return;
  }
  const res = await post('/api/export-pdf', { score: satbScore });
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'choir-score.pdf';
  a.click();
  URL.revokeObjectURL(url);
};


exportMusicXMLBtn.onclick = async () => {
  if (!satbScore) {
    showErrors(['Generate SATB before exporting MusicXML.']);
    return;
  }
  const res = await post('/api/export-musicxml', { score: satbScore });
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'choir-score.musicxml';
  a.click();
  URL.revokeObjectURL(url);
};

if (refreshMelodyPreviewBtn) refreshMelodyPreviewBtn.onclick = () => refreshPreview('melody');
if (refreshSatbPreviewBtn) refreshSatbPreviewBtn.onclick = () => refreshPreview('satb');
if (melodyPreviewZoomEl) melodyPreviewZoomEl.oninput = () => { melodyPreviewScale = Number(melodyPreviewZoomEl.value) / 100; applyPreviewScale('melody'); };
if (satbPreviewZoomEl) satbPreviewZoomEl.oninput = () => { satbPreviewScale = Number(satbPreviewZoomEl.value) / 100; applyPreviewScale('satb'); };
