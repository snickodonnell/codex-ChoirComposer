let melodyScore = null;
let satbScore = null;

const sectionsEl = document.getElementById('sections');
const melodyMeta = document.getElementById('melodyMeta');
const satbMeta = document.getElementById('satbMeta');

const formErrorsEl = document.getElementById('formErrors');
const VALID_TONICS = new Set(['C','C#','Db','D','D#','Eb','E','F','F#','Gb','G','G#','Ab','A','A#','Bb','B']);
const VALID_MODES = new Set(['ionian','dorian','phrygian','lydian','mixolydian','aeolian','locrian','major','minor','natural minor']);

function normalizeMode(mode) {
  const cleaned = (mode || '').trim().toLowerCase();
  if (cleaned === 'major') return 'ionian';
  if (cleaned === 'minor' || cleaned === 'natural minor') return 'aeolian';
  return cleaned;
}

function validatePreferences() {
  const errors = [];
  const keyRaw = document.getElementById('key').value?.trim() || '';
  const modeRaw = document.getElementById('primaryMode').value?.trim() || '';
  const timeRaw = document.getElementById('time').value?.trim() || '';
  const tempoRaw = document.getElementById('tempo').value?.trim() || '';

  if (keyRaw) {
    const m = keyRaw.match(/^([A-Ga-g])([#b]?)(m?)$/);
    if (!m) {
      errors.push('Key must look like C, F#, Bb, or Am.');
    } else {
      const tonic = `${m[1].toUpperCase()}${m[2]}`;
      if (!VALID_TONICS.has(tonic)) {
        errors.push('Key tonic must be A–G with optional # or b accidental.');
      }
      if (m[3] && modeRaw) {
        errors.push('Use either minor suffix in key (e.g., Am) OR Primary Mode (e.g., A + aeolian), not both.');
      }
    }
  }

  if (modeRaw) {
    if (!VALID_MODES.has(modeRaw.toLowerCase())) {
      errors.push('Primary Mode must be one of: ionian, dorian, phrygian, lydian, mixolydian, aeolian, locrian (or major/minor).');
    }
  }

  if (timeRaw) {
    const m = timeRaw.match(/^(\d{1,2})\s*\/\s*(\d{1,2})$/);
    if (!m) {
      errors.push('Time signature must be formatted like 4/4, 3/4, or 6/8.');
    } else {
      const top = Number(m[1]);
      const bottom = Number(m[2]);
      if (top < 1 || top > 16) errors.push('Time-signature numerator must be between 1 and 16.');
      if (![1, 2, 4, 8, 16, 32].includes(bottom)) errors.push('Time-signature denominator must be 1, 2, 4, 8, 16, or 32.');
    }
  }

  if (tempoRaw) {
    const tempo = Number(tempoRaw);
    if (!Number.isFinite(tempo) || tempo < 40 || tempo > 240) {
      errors.push('Tempo must be between 40 and 240 BPM.');
    }
  }

  return errors;
}

function showErrors(errors) {
  if (!errors.length) {
    formErrorsEl.textContent = '';
    formErrorsEl.style.display = 'none';
    return;
  }
  formErrorsEl.innerHTML = errors.map(e => `• ${e}`).join('<br/>');
  formErrorsEl.style.display = 'block';
}

function setSectionMode(row, isSaved) {
  row.dataset.mode = isSaved ? 'saved' : 'edit';
  const lockable = ['.section-label', '.section-title', '.section-text'];
  for (const selector of lockable) {
    const el = row.querySelector(selector);
    if (el) el.readOnly = isSaved;
  }

  const toggleBtn = row.querySelector('.toggle-section-mode');
  if (toggleBtn) {
    toggleBtn.textContent = isSaved ? 'Edit section' : 'Save section';
  }
}

function addSectionRow(defaultLabel = 'verse', title = '', text = '') {
  const row = document.createElement('div');
  row.className = 'section-row';
  row.innerHTML = `
    <div class="section-row-controls">
      <button type="button" class="move-section-up">↑</button>
      <button type="button" class="move-section-down">↓</button>
      <button type="button" class="toggle-section-mode">Save section</button>
    </div>
    <label>Section Label <input class="section-label" value="${defaultLabel}" placeholder="e.g. Verse, Chorus, Tag" /></label>
    <label>Title <input class="section-title" value="${title}" placeholder="Verse 1" /></label>
    <label>Pause after section (beats) <input class="section-pause-beats" type="number" min="0" max="4" step="0.5" value="0" /></label>
    <label>Lyrics <textarea class="section-text" placeholder="Enter lyrics here">${text}</textarea></label>
  `;
  setSectionMode(row, false);
  sectionsEl.appendChild(row);
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
    }
  }

  if (target.classList.contains('move-section-down')) {
    const next = row.nextElementSibling;
    if (next) {
      sectionsEl.insertBefore(next, row);
    }
  }

  if (target.classList.contains('toggle-section-mode')) {
    setSectionMode(row, row.dataset.mode !== 'saved');
  }
});

function collectPayload() {
  const errors = validatePreferences();
  if (errors.length) {
    showErrors(errors);
    throw new Error('Please fix the highlighted input validation errors.');
  }
  showErrors([]);

  const sections = [...document.querySelectorAll('.section-row')].map((row, i) => ({
    label: row.querySelector('.section-label').value,
    title: row.querySelector('.section-title').value || `Section ${i + 1}`,
    pause_beats: Number(row.querySelector('.section-pause-beats').value) || 0,
    text: row.querySelector('.section-text').value
  })).filter(s => s.text.trim().length > 0);

  return {
    sections,
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

function flattenVoice(score, voice) {
  return score.measures.flatMap(m => m.voices[voice]).filter(n => !n.is_rest);
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

function drawStaff(containerId, title, notes, timeSignature) {
  const root = document.getElementById(containerId);
  const wrap = document.createElement('div');
  wrap.className = 'staff-wrap';
  const heading = document.createElement('h4');
  heading.textContent = title;
  wrap.appendChild(heading);

  const vfDiv = document.createElement('div');
  wrap.appendChild(vfDiv);
  root.appendChild(wrap);

  const { Factory } = Vex.Flow;
  const factory = new Factory({ renderer: { elementId: vfDiv, width: 920, height: 180 } });
  const score = factory.EasyScore();
  const system = factory.System({ x: 10, y: 20, width: 880 });

  const staveNotes = notes.slice(0, 16).map(n => `${noteToVexKey(n.pitch)}/${n.beats >= 2 ? 'h' : 'q'}`);
  system.addStave({ voices: [score.voice(score.notes(staveNotes.join(', ')))] }).addClef('treble').addTimeSignature(timeSignature || '4/4');
  factory.draw();

  const lyricLine = document.createElement('div');
  lyricLine.style.fontFamily = 'monospace';
  lyricLine.style.fontSize = '12px';
  lyricLine.style.marginTop = '6px';
  lyricLine.textContent = notes.slice(0, 16).map(n => n.lyric ? `${n.lyric}(${n.lyric_mode || 'single'})` : '—').join(' | ');
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

addSectionRow('verse', 'Verse 1', 'Light in the morning fills every heart');
addSectionRow('chorus', 'Chorus', 'Sing together, hope forever');

document.getElementById('generateMelody').onclick = async () => {
  let res;
  try {
    res = await post('/api/generate-melody', collectPayload());
  } catch (error) {
    showErrors([String(error.message || error)]);
    return;
  }
  melodyScore = (await res.json()).score;
  document.getElementById('melodySheet').innerHTML = '';
  const notes = flattenVoice(melodyScore, 'soprano');
  melodyMeta.textContent = JSON.stringify(melodyScore.meta, null, 2);
  document.getElementById('melodyChords').textContent = formatChordLine(melodyScore);
  drawStaff('melodySheet', 'Melody', notes, melodyScore.meta.time_signature);
};

document.getElementById('refine').onclick = async () => {
  if (!melodyScore) return;
  const instruction = document.getElementById('instruction').value || 'smooth out leaps';
  const res = await post('/api/refine-melody', { score: melodyScore, instruction, regenerate: false });
  melodyScore = (await res.json()).score;
  document.getElementById('melodySheet').innerHTML = '';
  document.getElementById('melodyChords').textContent = formatChordLine(melodyScore);
  drawStaff('melodySheet', 'Melody (refined)', flattenVoice(melodyScore, 'soprano'), melodyScore.meta.time_signature);
};

document.getElementById('regenerate').onclick = async () => {
  if (!melodyScore) return;
  const instruction = document.getElementById('instruction').value || 'fresh melodic idea';
  const res = await post('/api/refine-melody', { score: melodyScore, instruction, regenerate: true });
  melodyScore = (await res.json()).score;
  document.getElementById('melodySheet').innerHTML = '';
  document.getElementById('melodyChords').textContent = formatChordLine(melodyScore);
  drawStaff('melodySheet', 'Melody (regenerated)', flattenVoice(melodyScore, 'soprano'), melodyScore.meta.time_signature);
};

document.getElementById('playMelody').onclick = async () => {
  if (!melodyScore) return;
  const notes = flattenVoice(melodyScore, 'soprano').map(n => ({ pitch: n.pitch, seconds: (60 / melodyScore.meta.tempo_bpm) * n.beats }));
  await playNotes(notes, false);
};

document.getElementById('generateSATB').onclick = async () => {
  if (!melodyScore) return;
  const res = await post('/api/generate-satb', { score: melodyScore });
  const payload = await res.json();
  satbScore = payload.score;
  satbMeta.textContent = JSON.stringify({ ...satbScore.meta, harmonization: payload.harmonization_notes }, null, 2);
  document.getElementById('satbChords').textContent = formatChordLine(satbScore);
  document.getElementById('satbSheet').innerHTML = '';
  ['soprano', 'alto', 'tenor', 'bass'].forEach(v => drawStaff('satbSheet', v.toUpperCase(), flattenVoice(satbScore, v), satbScore.meta.time_signature));
};

document.getElementById('playSATB').onclick = async () => {
  if (!satbScore) return;
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

document.getElementById('exportPDF').onclick = async () => {
  if (!satbScore) return;
  const res = await post('/api/export-pdf', { score: satbScore });
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'choir-score.pdf';
  a.click();
  URL.revokeObjectURL(url);
};


document.getElementById('exportMusicXML').onclick = async () => {
  if (!satbScore) return;
  const res = await post('/api/export-musicxml', { score: satbScore });
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'choir-score.musicxml';
  a.click();
  URL.revokeObjectURL(url);
};
