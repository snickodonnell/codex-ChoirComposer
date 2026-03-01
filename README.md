# Choir Composer

AI-assisted web application that composes melody-first choir arrangements from user lyrics and optional style/theory preferences, then generates SATB, in-app audio preview, printable PDF, and MusicXML export.

## Stack
- **Backend:** FastAPI-style API app (Python)
- **Frontend:** Vanilla JS + HTML/CSS
- **Canonical score model:** Strict `CanonicalScore` JSON schema
- **Music notation rendering:** VexFlow (in-app)
- **Audio preview:** Tone.js synth playback in app
- **Export:** Verovio SVG engraving + browser-side PDF assembly (canvg + jsPDF) + MusicXML

## Core architecture
All generation produces one canonical `CanonicalScore` first. Then every downstream feature consumes the same model:
- UI rendering
- Playback
- SATB harmonization
- PDF export (browser-side SVG -> PDF)
- MusicXML export

This prevents format drift and keeps lyric/meter/voice consistency checks centralized.

## Validation guarantees
Every generated/regenerated score runs validation checks for:
1. **Measure timing**: each voice sums exactly to the measure capacity from time signature
2. **Voice separation**: SATB order and practical spacing
3. **Lyric mapping**: soprano lyric indices map exactly to section syllables

Validation endpoint: `POST /api/validate-score`

## Features implemented
1. **Lyrics + structure input** (verse, chorus, bridge, etc.)
2. **Lyric prosody mapping engine** (deterministic, policy-driven rhythm spans with punctuation/section/preset controls)
3. **Optional key/time/tempo** with auto-selection defaults
4. **Melody-first workflow** (generate + regenerate with draft history)
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

### Default UI behavior
On first load, the UI is pre-seeded with a known-good hymn dataset (sections, arrangement, and musical preferences) so clicking **Generate Melody** works immediately without manual setup.

If you want to clear and reseed from that baseline at any time, click **Load Hymn Test Data**.

## PDF export dependencies
Install Python PDF dependencies with extras:

```bash
pip install .[pdf]
```

Install Cairo system library on the **server runtime** (the machine/container running `uvicorn`):
- macOS: `brew install cairo`
- Ubuntu/Debian: `sudo apt-get install -y libcairo2`
- Windows: use Docker (recommended) or install Cairo via MSYS2.

After installing Cairo, restart the API server.

If Cairo is missing at runtime, `POST /api/export-pdf` now returns HTTP `422` with a user-facing message:

> PDF export requires the system library Cairo (libcairo). Install it and restart the server.

SVG engraving preview still works without Cairo, but PDF export will be unavailable until Cairo is installed.


## Manual verification note (frontend PDF export)
1. Run the app and generate Melody or SATB.
2. Click **Download PDF**.
3. Confirm status updates show progress (`Building PDF page X/N…`).
4. Confirm downloaded file name format is `ChoirComposer-<title>-<stage>.pdf` (or `ChoirComposer-<stage>.pdf` when title missing).
5. Open the PDF and compare each page to the engraving preview; pages should match visual layout/page count.

## Tests
```bash
pytest
```

For full browser-workflow regression coverage in container environments, install Playwright browser dependencies first:

```bash
python -m playwright install chromium
python -m playwright install-deps chromium
pytest -q tests/test_frontend_workflow_regression.py
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


## Developer note: avoid import shadowing
When adding new Python modules, avoid naming files after common third-party packages (for example: `pydantic.py`, `fastapi.py`, or `requests.py`).
These names can shadow installed dependencies on Python import paths and cause confusing runtime import failures.

