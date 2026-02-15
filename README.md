# Choir Composer

AI-assisted web application that composes melody-first choir arrangements from user lyrics and optional style/theory preferences, then generates SATB, in-app audio preview, printable PDF, and MusicXML export.

## Stack
- **Backend:** FastAPI-style API app (Python)
- **Frontend:** Vanilla JS + HTML/CSS
- **Canonical score model:** Strict `CanonicalScore` JSON schema
- **Music notation rendering:** VexFlow (in-app)
- **Audio preview:** Tone.js synth playback in app
- **Export:** ReportLab PDF + MusicXML

## Core architecture
All generation produces one canonical `CanonicalScore` first. Then every downstream feature consumes the same model:
- UI rendering
- Playback
- SATB harmonization
- PDF export
- MusicXML export

This prevents format drift and keeps lyric/meter/voice consistency checks centralized.

## Validation guarantees
Every generated/refined score runs validation checks for:
1. **Measure timing**: each voice sums exactly to the measure capacity from time signature
2. **Voice separation**: SATB order and practical spacing
3. **Lyric mapping**: soprano lyric indices map exactly to section syllables

Validation endpoint: `POST /api/validate-score`

## Features implemented
1. **Lyrics + structure input** (verse, chorus, bridge, etc.)
2. **Lyric prosody mapping engine** (deterministic, policy-driven rhythm spans with punctuation/section/preset controls)
3. **Optional key/time/tempo** with auto-selection defaults
4. **Melody-first workflow** (generate + refine/regenerate)
5. **SATB harmonization** with range constraints
6. **In-app preview** (notation + lyric alignment lane + synth playback)
7. **Export** to US Letter PDF and MusicXML

## Run locally
```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
uvicorn app.main:app --reload
```

Open: `http://127.0.0.1:8000`

## Tests
```bash
pytest
```


## Singability constraints (deterministic MVP)
- Hard SATB ranges enforced: S C4–A5, A G3–D5, T C3–G4, B E2–C4.
- Melodic motion constrained to reduce large leaps and avoid extreme tessitura.
- SATB integrity checks: voice ordering, upper-voice spacing (S-A / A-T within octave), and detection/auto-fix pass for obvious parallel 5ths/8ves where detectable.


## Lyric-to-rhythm mapping layer
- Parses lyrics into section-scoped syllables while preserving word boundaries and hyphenation metadata.
- Maps each syllable to rhythmic spans (single note, tied sustain, melisma, subdivision) deterministically.
- Validates full coverage (no dropped syllables), prevents orphan non-rest lyric notes, and preserves explicit continuation semantics via `lyric_mode`.
- Designed as a modular engine (`app/services/lyric_mapping.py`) so advanced prosody can be added without rewriting harmonization.


## Lyric rhythm policy controls
`plan_syllable_rhythm` uses a deterministic `RhythmPolicyConfig` (`melismaRate`, `subdivisionRate`, `phraseEndHoldBeats`, `preferStrongBeatForStress`) derived from section type and a user preset (`syllabic`, `mixed`, `melismatic`).

## Playwright screenshots in containers / Codespaces
For reliable screenshots in containerized environments (where Chromium can crash due to sandbox or `/dev/shm` constraints), use the fallback screenshot helper:

```bash
python scripts/capture_screenshot.py \
  --url http://127.0.0.1:8000/ \
  --output artifacts/ui-home.png
```

The helper:
- launches Chromium with container-safe flags (`--no-sandbox`, `--disable-dev-shm-usage`, etc.),
- falls back to Firefox/WebKit if Chromium fails,
- writes a JSON capture log at `artifacts/screenshot-capture-log.json`.

In CI, `.github/workflows/screenshot-smoke.yml` installs Playwright OS dependencies and all browser engines, captures a screenshot, and always uploads artifacts (including logs) even when capture fails.
