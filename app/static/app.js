let melodyScore = null;
let satbScore = null;

const sectionsEl = document.getElementById('sections');
const melodyMeta = document.getElementById('melodyMeta');
const satbMeta = document.getElementById('satbMeta');

function addSectionRow(defaultLabel = 'verse', title = '', text = '') {
  const row = document.createElement('div');
  row.className = 'section-row';
  row.innerHTML = `
    <label>Type
      <select class="section-label">
        <option value="verse">Verse</option>
        <option value="chorus">Chorus/Refrain</option>
        <option value="bridge">Bridge</option>
        <option value="pre-chorus">Pre-Chorus</option>
        <option value="intro">Intro</option>
        <option value="outro">Outro</option>
        <option value="custom">Custom</option>
      </select>
    </label>
    <label>Title <input class="section-title" value="${title}" placeholder="Verse 1" /></label>
    <label>Lyrics <textarea class="section-text" placeholder="Enter lyrics here">${text}</textarea></label>
  `;
  row.querySelector('.section-label').value = defaultLabel;
  sectionsEl.appendChild(row);
}

function collectPayload() {
  const sections = [...document.querySelectorAll('.section-row')].map((row, i) => ({
    label: row.querySelector('.section-label').value,
    title: row.querySelector('.section-title').value || `Section ${i + 1}`,
    text: row.querySelector('.section-text').value
  })).filter(s => s.text.trim().length > 0);

  return {
    sections,
    preferences: {
      key: document.getElementById('key').value || null,
      time_signature: document.getElementById('time').value || null,
      tempo_bpm: document.getElementById('tempo').value ? Number(document.getElementById('tempo').value) : null,
      style: document.getElementById('style').value,
      mood: document.getElementById('mood').value,
      lyric_rhythm_preset: document.getElementById('lyricPreset').value
    }
  };
}

function flattenVoice(score, voice) {
  return score.measures.flatMap(m => m.voices[voice]).filter(n => !n.is_rest);
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
  if (!res.ok) throw new Error(await res.text());
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
  const res = await post('/api/generate-melody', collectPayload());
  melodyScore = (await res.json()).score;
  document.getElementById('melodySheet').innerHTML = '';
  const notes = flattenVoice(melodyScore, 'soprano');
  melodyMeta.textContent = JSON.stringify(melodyScore.meta, null, 2);
  drawStaff('melodySheet', 'Melody', notes, melodyScore.meta.time_signature);
};

document.getElementById('refine').onclick = async () => {
  if (!melodyScore) return;
  const instruction = document.getElementById('instruction').value || 'smooth out leaps';
  const res = await post('/api/refine-melody', { score: melodyScore, instruction, regenerate: false });
  melodyScore = (await res.json()).score;
  document.getElementById('melodySheet').innerHTML = '';
  drawStaff('melodySheet', 'Melody (refined)', flattenVoice(melodyScore, 'soprano'), melodyScore.meta.time_signature);
};

document.getElementById('regenerate').onclick = async () => {
  if (!melodyScore) return;
  const instruction = document.getElementById('instruction').value || 'fresh melodic idea';
  const res = await post('/api/refine-melody', { score: melodyScore, instruction, regenerate: true });
  melodyScore = (await res.json()).score;
  document.getElementById('melodySheet').innerHTML = '';
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
