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

const generateMelodyBtn = document.getElementById('generateMelody');
const refineBtn = document.getElementById('refine');
const regenerateBtn = document.getElementById('regenerate');
const playMelodyBtn = document.getElementById('playMelody');
const generateSATBBtn = document.getElementById('generateSATB');
const playSATBBtn = document.getElementById('playSATB');
const exportPDFBtn = document.getElementById('exportPDF');
const exportMusicXMLBtn = document.getElementById('exportMusicXML');

const formErrorsEl = document.getElementById('formErrors');
const VALID_TONICS = new Set(['C','C#','Db','D','D#','Eb','E','F','F#','Gb','G','G#','Ab','A','A#','Bb','B']);
const VALID_MODES = new Set(['ionian','dorian','phrygian','lydian','mixolydian','aeolian','locrian','major','minor','natural minor']);
let sectionIdCounter = 0;
const MAX_DRAFT_VERSIONS = 20;
let melodyDraftVersions = [];
let activeDraftVersionId = null;

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
  const keyRaw = keyEl.value?.trim() || '';
  const modeRaw = modeEl.value?.trim() || '';
  const timeRaw = timeEl.value?.trim() || '';
  const tempoRaw = tempoEl.value?.trim() || '';

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
    if (!Number.isFinite(tempo) || tempo < 40 || tempo > 240) {
      errors.push(createValidationIssue('Tempo must be between 40 and 240 BPM.', tempoEl));
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

function getSectionRows() {
  return [...sectionsEl.querySelectorAll('.section-row')];
}

function getSectionLibrary() {
  return getSectionRows().map((row) => ({
    id: row.dataset.sectionId,
    label: row.querySelector('.section-label').value.trim(),
    text: row.querySelector('.section-text').value,
    progression_cluster: row.querySelector('.section-progression-cluster').value.trim() || null,
  }));
}

function getArrangementClusters() {
  const sectionById = new Map(getSectionLibrary().map((s) => [s.id, s]));
  const clusters = [];
  [...arrangementListEl.querySelectorAll('.arrangement-item')].forEach((item) => {
    const section = sectionById.get(item.dataset.sectionId);
    if (!section) return;
    const cluster = section.progression_cluster || section.label || 'default';
    if (!clusters.includes(cluster)) clusters.push(cluster);
  });
  return clusters;
}

function refreshRegenerateClusterOptions() {
  const previousOptions = [...regenerateClustersEl.options].map((o) => o.value);
  const selected = new Set([...regenerateClustersEl.selectedOptions].map((o) => o.value));
  const clusters = getArrangementClusters();
  const shouldDefaultSelectAll = selected.size === 0 || (previousOptions.length > 0 && selected.size === previousOptions.length);

  regenerateClustersEl.innerHTML = clusters.map((cluster) => `<option value="${cluster}">${cluster}</option>`).join('');
  [...regenerateClustersEl.options].forEach((option) => {
    option.selected = shouldDefaultSelectAll ? true : selected.has(option.value);
  });
}

function buildSectionClusterMap(payload) {
  const sectionById = new Map(payload.sections.map((section) => [section.id, section]));
  const arranged = payload.arrangement.length
    ? payload.arrangement.map((item) => sectionById.get(item.section_id)).filter(Boolean)
    : payload.sections;
  const mapping = {};
  arranged.forEach((section, idx) => {
    mapping[`sec-${idx + 1}`] = section.progression_cluster || section.label || 'default';
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
  drawStaff('melodySheet', heading, flattenVoice(melodyScore, 'soprano', { includeRests: true }), melodyScore.meta.time_signature, buildSectionBoundaryMap(melodyScore));
}

function upsertActiveVersion(score, label) {
  const current = activeDraftVersion();
  if (!current) return;
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
  safeRenderMelody(score, label);
  updateDraftVersionOptions();
}

function describeSection(sectionId) {
  const match = getSectionLibrary().find((s) => s.id === sectionId);
  if (!match) return `Missing section (${sectionId})`;
  return `${match.label || 'Untitled Label'} (${sectionId})`;
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

function addArrangementItem(sectionId, pauseBeats = null) {
  if (!sectionId) return;
  const normalizedPause = pauseBeats ?? 0;
  const item = document.createElement('div');
  item.className = 'arrangement-item';
  item.dataset.sectionId = sectionId;
  item.innerHTML = `
    <div class="arrangement-item-main">
      <div class="arrangement-item-meta"></div>
      <label>Pause after section (beats)
        <input class="arrangement-pause-beats" type="number" min="0" max="4" step="0.5" value="${normalizedPause}" />
      </label>
    </div>
    <div class="arrangement-item-controls">
      <button type="button" class="arrangement-up">↑</button>
      <button type="button" class="arrangement-down">↓</button>
      <button type="button" class="arrangement-remove">Remove</button>
    </div>
  `;
  arrangementListEl.appendChild(item);
  refreshArrangementLabels();
}

function refreshArrangementLabels() {
  [...arrangementListEl.querySelectorAll('.arrangement-item')].forEach((item, idx) => {
    const meta = item.querySelector('.arrangement-item-meta');
    if (!meta) return;
    meta.textContent = `${idx + 1}. ${describeSection(item.dataset.sectionId)}`;
  });
  refreshRegenerateClusterOptions();
}

function setSectionMode(row, isSaved) {
  row.dataset.mode = isSaved ? 'saved' : 'edit';
  const lockable = ['.section-label', '.section-progression-cluster', '.section-text'];
  for (const selector of lockable) {
    const el = row.querySelector(selector);
    if (el) el.readOnly = isSaved;
  }

  const toggleBtn = row.querySelector('.toggle-section-mode');
  if (toggleBtn) {
    toggleBtn.textContent = isSaved ? 'Edit section' : 'Save section';
  }
}

function addSectionRow(defaultLabel = 'verse', text = '') {
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
    <label>Progression Cluster <input class="section-progression-cluster" value="${defaultLabel}" placeholder="e.g. Verse cluster, Chorus cluster" /></label>
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
  if (target.classList.contains('section-label')) {
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

function collectPayload() {
  const errors = validatePreferences();
  const sectionLibrary = getSectionLibrary().filter((s) => s.text.trim().length > 0);
  const sectionById = new Map(sectionLibrary.map((s) => [s.id, s]));
  const sectionsCard = sectionsEl.closest('.card');
  const arrangementCard = arrangementListEl.closest('.card');
  const arrangement = [...arrangementListEl.querySelectorAll('.arrangement-item')].map((item) => ({
    section_id: item.dataset.sectionId,
    pause_beats: Number(item.querySelector('.arrangement-pause-beats')?.value) || 0,
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
      lyric_rhythm_preset: document.getElementById('lyricPreset').value
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

function buildSectionBoundaryMap(score) {
  return new Map((score?.sections || []).map((section) => [section.id, Number(section.pause_beats) > 0]));
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
    { beats: 3, duration: 'hd' },
    { beats: 2, duration: 'h' },
    { beats: 1.5, duration: 'qd' },
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

  notes.forEach((note) => {
    splitBeatsIntoDurations(note.beats).forEach((duration) => {
      if (beatCursor >= beatsPerMeasure - 0.001) beatCursor = 0;
      const key = note.is_rest ? 'b/4' : noteToVexKey(note.pitch);
      staveNotes.push(`${key}/${duration}${note.is_rest ? 'r' : ''}`);
      const durationBeats = duration === 'w' ? 4 : duration === 'hd' ? 3 : duration === 'h' ? 2 : duration === 'qd' ? 1.5 : duration === 'q' ? 1 : 0.5;
      beatCursor += durationBeats;
    });
  });

  if (beatCursor > 0.001 && beatCursor < beatsPerMeasure - 0.001) {
    splitBeatsIntoDurations(beatsPerMeasure - beatCursor).forEach((duration) => {
      staveNotes.push(`b/4/${duration}r`);
    });
  }

  return staveNotes;
}

function drawStaff(containerId, title, notes, timeSignature, boundaryMap = new Map()) {
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
      if (previousSection && currentSection && previousSection !== currentSection && boundaryMap.get(previousSection)) {
        lyricTokens.push('‖');
      }
    }
    lyricTokens.push(note.lyric ? `${note.lyric}(${note.lyric_mode || 'single'})` : '—');
  });
  lyricLine.textContent = lyricTokens.join(' | ');
  wrap.appendChild(lyricLine);
}

async function post(url, payload) {
  const res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
  if (!res.ok) {
    const text = await res.text();
    let message = text;
    try {
      const parsed = JSON.parse(text);
      if (Array.isArray(parsed.detail)) {
        message = parsed.detail.map(d => `${d.loc?.join('.') || 'request'}: ${d.msg}`).join(' | ');
      } else if (parsed.detail) {
        message = parsed.detail;
      }
    } catch (_) {
      // keep raw text
    }
    throw new Error(message);
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

  workflowStageLabelEl.textContent = 'Current stage: 2) Melody Draft';
  workflowStageHintEl.textContent = 'Start by generating a melody from your lyrics and arrangement.';
}

function updateActionAvailability() {
  const hasMelody = Boolean(melodyScore);
  const hasSatb = Boolean(satbScore);

  setButtonEnabled(generateMelodyBtn, true);
  setButtonEnabled(refineBtn, hasMelody, 'Generate a melody first.');
  setButtonEnabled(regenerateBtn, hasMelody, 'Generate a melody first.');
  setButtonEnabled(playMelodyBtn, hasMelody, 'Generate a melody first.');
  setButtonEnabled(generateSATBBtn, hasMelody, 'Generate a melody first.');
  setButtonEnabled(playSATBBtn, hasSatb, 'Generate SATB first.');
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

function resetSatbStage() {
  satbScore = null;
  satbMeta.textContent = '';
  document.getElementById('satbChords').textContent = formatChordLine(null);
  document.getElementById('satbSheet').innerHTML = '';
  updateActionAvailability();
}

async function playNotes(noteObjects, poly = false) {
  await Tone.start();
  const now = Tone.now() + 0.2;
  if (poly) {
    const synth = new Tone.PolySynth(Tone.Synth).toDestination();
    let t = now;
    for (const chord of noteObjects) {
      synth.triggerAttackRelease(chord.pitches, chord.seconds, t);
      t += chord.seconds;
    }
  } else {
    const synth = new Tone.Synth().toDestination();
    let t = now;
    for (const n of noteObjects) {
      synth.triggerAttackRelease(n.pitch, n.seconds, t);
      t += n.seconds;
    }
  }
}

document.getElementById('addSection').onclick = () => addSectionRow();
document.getElementById('addArrangementItem').onclick = () => addArrangementItem(arrangementSectionSelectEl.value);

addSectionRow('verse', 'Light in the morning fills every heart');
addSectionRow('chorus', 'Sing together, hope forever');
refreshArrangementLibrarySelect();
addArrangementItem(getSectionRows()[0]?.dataset.sectionId, 0);
addArrangementItem(getSectionRows()[1]?.dataset.sectionId, 0);
addArrangementItem(getSectionRows()[1]?.dataset.sectionId, 0);
refreshRegenerateClusterOptions();
updateDraftVersionOptions();
updateActionAvailability();

generateMelodyBtn.onclick = async () => {
  let res;
  let payload;
  try {
    payload = collectPayload();
    res = await post('/api/generate-melody', payload);
  } catch (error) {
    if (error?.isValidationError) return;
    showErrors([String(error.message || error)]);
    return;
  }
  const score = (await res.json()).score;
  const sectionClusterMap = buildSectionClusterMap(payload);
  melodyDraftVersions = [];
  activeDraftVersionId = null;
  appendDraftVersion(score, sectionClusterMap, 'Melody');
  resetSatbStage();
  updateActionAvailability();
};

refineBtn.onclick = async () => {
  if (!melodyScore) {
    showErrors(['Generate a melody before refining.']);
    return;
  }
  const instruction = document.getElementById('instruction').value || 'smooth out leaps';
  const res = await post('/api/refine-melody', { score: melodyScore, instruction, regenerate: false });
  const score = (await res.json()).score;
  upsertActiveVersion(score, 'Melody (refined)');
  resetSatbStage();
  updateActionAvailability();
};

regenerateBtn.onclick = async () => {
  if (!melodyScore) {
    showErrors(['Generate a melody before regenerating.']);
    return;
  }
  const instruction = document.getElementById('instruction').value || 'fresh melodic idea';
  const selectedClusters = [...regenerateClustersEl.selectedOptions].map((o) => o.value);
  const currentVersion = activeDraftVersion();
  const res = await post('/api/refine-melody', {
    score: melodyScore,
    instruction,
    regenerate: true,
    selected_clusters: selectedClusters,
    section_clusters: currentVersion?.sectionClusterMap || {},
  });
  const score = (await res.json()).score;
  appendDraftVersion(score, currentVersion?.sectionClusterMap || {}, 'Melody (regenerated)');
  resetSatbStage();
  updateActionAvailability();
};

draftVersionSelectEl.onchange = () => {
  const selectedId = draftVersionSelectEl.value;
  const version = melodyDraftVersions.find((item) => item.id === selectedId);
  if (!version) return;
  activeDraftVersionId = version.id;
  safeRenderMelody(version.score, version.label);
  resetSatbStage();
  updateActionAvailability();
};

playMelodyBtn.onclick = async () => {
  if (!melodyScore) {
    showErrors(['Generate a melody before playback.']);
    return;
  }
  const notes = flattenVoice(melodyScore, 'soprano').map(n => ({ pitch: n.pitch, seconds: (60 / melodyScore.meta.tempo_bpm) * n.beats }));
  await playNotes(notes, false);
};

generateSATBBtn.onclick = async () => {
  if (!melodyScore) {
    showErrors(['Generate a melody before SATB harmonization.']);
    return;
  }
  const res = await post('/api/generate-satb', { score: melodyScore });
  const payload = await res.json();
  satbScore = normalizeScoreForRendering(payload.score);
  satbMeta.textContent = JSON.stringify({ ...satbScore.meta, harmonization: payload.harmonization_notes }, null, 2);
  document.getElementById('satbChords').textContent = formatChordLine(satbScore);
  updateActionAvailability();
  document.getElementById('satbSheet').innerHTML = '';
  try {
    const boundaryMap = buildSectionBoundaryMap(satbScore);
    ['soprano', 'alto', 'tenor', 'bass'].forEach(v => drawStaff('satbSheet', v.toUpperCase(), flattenVoice(satbScore, v, { includeRests: true }), satbScore.meta.time_signature, boundaryMap));
  } catch (_) {
    try {
      satbScore = normalizeScoreForRendering(satbScore);
      const boundaryMap = buildSectionBoundaryMap(satbScore);
      ['soprano', 'alto', 'tenor', 'bass'].forEach(v => drawStaff('satbSheet', v.toUpperCase(), flattenVoice(satbScore, v, { includeRests: true }), satbScore.meta.time_signature, boundaryMap));
    } catch (_) {
      showErrors(['SATB generated, but score rendering is temporarily unavailable. Playback/export still work.']);
    }
  }
};

playSATBBtn.onclick = async () => {
  if (!satbScore) {
    showErrors(['Generate SATB before playback.']);
    return;
  }
  const soprano = flattenVoice(satbScore, 'soprano');
  const alto = flattenVoice(satbScore, 'alto');
  const tenor = flattenVoice(satbScore, 'tenor');
  const bass = flattenVoice(satbScore, 'bass');
  const chords = soprano.map((sn, i) => ({
    pitches: [sn.pitch, alto[i]?.pitch, tenor[i]?.pitch, bass[i]?.pitch].filter(Boolean),
    seconds: (60 / satbScore.meta.tempo_bpm) * sn.beats,
  }));
  await playNotes(chords, true);
};

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
