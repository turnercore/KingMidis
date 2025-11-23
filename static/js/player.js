let currentPart = null;
let currentSynth = null;
let currentButton = null;
let isPlaying = false;
let midiDuration = 0;
let progressLoopId = null;
let isScrubbing = false;

const playerBar = document.getElementById("playerBar");
const playPauseBtn = document.getElementById("playPauseBtn");
const progressSlider = document.getElementById("progressSlider");
const currentTimeEl = document.getElementById("currentTime");
const totalTimeEl = document.getElementById("totalTime");
const titleEl = document.getElementById("playerTitle");
const statusEl = document.getElementById("status");
const buttons = document.querySelectorAll(".play-btn");
const SLIDER_MAX = progressSlider ? Number(progressSlider.max) || 1000 : 1000;
const volumeSlider = document.getElementById("volumeSlider");
const volumeLabel = document.getElementById("volumeLabel");
const instrumentButtons = document.querySelectorAll(".instrument-btn[data-instrument]");
const moreToggleBtn = document.querySelector(".instrument-btn.more-toggle");
const instrumentDropdown = document.querySelector(".instrument-dropdown");
const VOLUME_STORAGE_KEY = "km-volume";
const INSTRUMENT_STORAGE_KEY = "km-instrument";
const DEFAULT_VOLUME = 80;
const INSTRUMENT_PRESETS = {
    piano: {
        label: "Piano",
        create: () =>
            new Tone.PolySynth(Tone.Synth, {
                oscillator: { type: "triangle" },
                envelope: { attack: 0.005, decay: 1, sustain: 0.4, release: 1.2 },
            }).toDestination(),
    },
    digital: {
        label: "Digital",
        create: () =>
            new Tone.PolySynth(Tone.AMSynth, {
                harmonicity: 2.5,
                oscillator: { type: "sawtooth" },
                envelope: { attack: 0.02, decay: 0.3, sustain: 0.3, release: 0.8 },
            }).toDestination(),
    },
    strings: {
        label: "Strings",
        create: () =>
            new Tone.PolySynth(Tone.Synth, {
                oscillator: { type: "sawtooth" },
                envelope: { attack: 0.2, decay: 0.6, sustain: 0.7, release: 2.5 },
                filter: { type: "lowpass", Q: 3 },
            }).toDestination(),
    },
    guitar: {
        label: "Guitar",
        create: () =>
            new Tone.PolySynth(Tone.FMSynth, {
                harmonicity: 3,
                modulationIndex: 10,
                envelope: { attack: 0.01, decay: 0.4, sustain: 0.3, release: 1.2 },
            }).toDestination(),
    },
    harp: {
        label: "Harp",
        create: () =>
            new Tone.PolySynth(Tone.PluckSynth, {
                dampening: 3200,
                resonance: 0.8,
            }).toDestination(),
    },
    brass: {
        label: "Brass",
        create: () =>
            new Tone.PolySynth(Tone.FMSynth, {
                harmonicity: 2,
                modulationIndex: 20,
                envelope: { attack: 0.05, decay: 0.7, sustain: 0.6, release: 1.5 },
                modulation: { type: "square" },
            }).toDestination(),
    },
    voice: {
        label: "Choir",
        create: () =>
            new Tone.PolySynth(Tone.Synth, {
                oscillator: { type: "sine" },
                envelope: { attack: 0.3, decay: 0.4, sustain: 0.8, release: 2.5 },
                filterEnvelope: {
                    attack: 0.2,
                    decay: 0.4,
                    sustain: 0.7,
                    release: 2,
                    baseFrequency: 200,
                    octaves: 3,
                },
            }).toDestination(),
    },
    organ: {
        label: "Organ",
        create: () =>
            new Tone.PolySynth(Tone.Synth, {
                oscillator: { type: "square" },
                envelope: { attack: 0.05, decay: 0.3, sustain: 0.8, release: 1.5 },
                filter: { type: "lowpass", frequency: 6000 },
            }).toDestination(),
    },
    woodwind: {
        label: "Woodwinds",
        create: () =>
            new Tone.PolySynth(Tone.Synth, {
                oscillator: { type: "triangle" },
                envelope: { attack: 0.08, decay: 0.4, sustain: 0.6, release: 1.4 },
                vibratoAmount: 0.3,
            }).toDestination(),
    },
    percussion: {
        label: "Percussion",
        create: () =>
            new Tone.PolySynth(Tone.Synth, {
                oscillator: { type: "square" },
                envelope: { attack: 0.001, decay: 0.2, sustain: 0.1, release: 0.2 },
                filter: { type: "highpass", frequency: 200 },
            }).toDestination(),
    },
    pad: {
        label: "Ambient Pad",
        create: () =>
            new Tone.PolySynth(Tone.Synth, {
                oscillator: { type: "sawtooth" },
                envelope: { attack: 0.8, decay: 1.2, sustain: 0.9, release: 4 },
                filter: { type: "lowpass", frequency: 8000 },
            }).toDestination(),
    },
};
let currentInstrumentId = null;

const setLogoPlaying = (flag) => {
    if (typeof window.kmLogoSetPlaying === "function") {
        window.kmLogoSetPlaying(flag);
    }
};

const setLogoThinking = (flag) => {
    if (typeof window.kmLogoSetThinking === "function") {
        window.kmLogoSetThinking(flag);
    }
};

function showPlayerBar() {
    if (playerBar) {
        playerBar.classList.remove("hidden");
    }
}

function setStatus(text) {
    if (statusEl) statusEl.textContent = text;
}

function formatTime(seconds) {
    if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60)
        .toString()
        .padStart(2, "0");
    return `${mins}:${secs}`;
}

async function copyTextToClipboard(text) {
    if (!text) return false;

    if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(text);
        return true;
    }

    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.left = "-9999px";
    document.body.appendChild(textarea);
    textarea.select();

    let successful = false;
    try {
        successful = document.execCommand("copy");
    } catch (error) {
        successful = false;
    } finally {
        document.body.removeChild(textarea);
    }

    return successful;
}

function readStoredVolume() {
    try {
        const stored = localStorage.getItem(VOLUME_STORAGE_KEY);
        const parsed = stored !== null ? Number(stored) : DEFAULT_VOLUME;
        if (Number.isFinite(parsed)) {
            return Math.min(Math.max(parsed, 0), 100);
        }
    } catch (error) {
        console.warn("Volume storage read failed", error);
    }
    return DEFAULT_VOLUME;
}

function persistVolume(value) {
    try {
        localStorage.setItem(VOLUME_STORAGE_KEY, String(value));
    } catch (error) {
        console.warn("Volume storage write failed", error);
    }
}

let lastNonZeroVolume = null;

function volumeToDb(value) {
    const minDb = -48;
    const maxDb = 0;
    if (value <= 0) return -60;
    const ratio = value / 100;
    return minDb + (maxDb - minDb) * ratio;
}

function applyVolume(value, { persist = true, updateSlider = true } = {}) {
    const clamped = Math.min(Math.max(value, 0), 100);
    if (clamped <= 0) {
        Tone.Destination.mute = true;
    } else {
        Tone.Destination.mute = false;
        const db = volumeToDb(clamped);
        Tone.Destination.volume.rampTo(db, 0.05);
        lastNonZeroVolume = clamped;
    }

    if (updateSlider && volumeSlider) {
        volumeSlider.value = String(clamped);
    }

    if (volumeLabel) {
        volumeLabel.textContent = `${Math.round(clamped)}%`;
    }

    if (persist) {
        persistVolume(clamped);
    }
}

function initVolumeControl() {
    const initialVolume = readStoredVolume();
    lastNonZeroVolume = initialVolume || DEFAULT_VOLUME;
    applyVolume(initialVolume, { persist: false, updateSlider: true });

    if (!volumeSlider) return;

    const handle = (evt) => {
        evt.preventDefault();
        const value = Number(volumeSlider.value);
        applyVolume(value, { persist: true, updateSlider: false });
    };

    volumeSlider.addEventListener("input", handle);

    const volumeIcon = document.querySelector(".volume-icon");
    if (volumeIcon) {
        volumeIcon.dataset.muted = initialVolume <= 0 ? "true" : "false";
        volumeIcon.addEventListener("click", () => {
            const current = Number(volumeSlider.value);
            if (current > 0) {
                applyVolume(0, { persist: true });
                volumeIcon.dataset.muted = "true";
            } else {
                const restore = lastNonZeroVolume && lastNonZeroVolume > 0 ? lastNonZeroVolume : DEFAULT_VOLUME;
                applyVolume(restore, { persist: true });
                volumeIcon.dataset.muted = "false";
            }
        });

        volumeSlider.addEventListener("input", () => {
            const current = Number(volumeSlider.value);
            volumeIcon.dataset.muted = current <= 0 ? "true" : "false";
        });
    }
}

function initMetaCopyButtons() {
    const metaButtons = document.querySelectorAll(".copy-meta-btn");
    metaButtons.forEach((btn) => {
        btn.addEventListener("click", async () => {
            const text = btn.dataset.attribution;
            if (!text) return;
            const original = btn.textContent;
            try {
                const success = await copyTextToClipboard(text);
                btn.textContent = success ? "Copied!" : "Copy unavailable";
            } catch (error) {
                console.warn("Copy failed", error);
                btn.textContent = "Copy failed";
            }

            setTimeout(() => {
                btn.textContent = original;
            }, 2000);
        });
    });
}

function readStoredInstrument() {
    try {
        const stored = localStorage.getItem(INSTRUMENT_STORAGE_KEY);
        if (stored && INSTRUMENT_PRESETS[stored]) {
            return stored;
        }
    } catch (error) {
        console.warn("Instrument storage read failed", error);
    }
    return "piano";
}

function persistInstrument(value) {
    try {
        localStorage.setItem(INSTRUMENT_STORAGE_KEY, value);
    } catch (error) {
        console.warn("Instrument storage write failed", error);
    }
}

function createSynthForPreset(presetId) {
    const preset = INSTRUMENT_PRESETS[presetId] || INSTRUMENT_PRESETS.piano;
    return preset.create();
}

function applyInstrumentSelection(presetId, { persist = true } = {}) {
    if (!INSTRUMENT_PRESETS[presetId]) {
        presetId = "piano";
    }

    if (currentInstrumentId === presetId) return;

    currentInstrumentId = presetId;
    if (persist) persistInstrument(presetId);

    instrumentButtons.forEach((btn) => {
        btn.classList.toggle("active", btn.dataset.instrument === presetId);
    });

    if (currentSynth) {
        const newSynth = createSynthForPreset(presetId);
        currentSynth.dispose();
        currentSynth = newSynth;
    }
}

function initInstrumentControls() {
    if (!instrumentButtons.length) return;

    let closeDropdown = () => {};
    const initial = readStoredInstrument();
    currentInstrumentId = initial;
    instrumentButtons.forEach((btn) => {
        btn.classList.toggle("active", btn.dataset.instrument === initial);
        btn.addEventListener("click", () => {
            applyInstrumentSelection(btn.dataset.instrument);
            if (instrumentDropdown && !instrumentDropdown.classList.contains("hidden")) {
                closeDropdown();
            }
        });
    });

    if (moreToggleBtn && instrumentDropdown) {
        let outsideHandler = null;
        closeDropdown = () => {
            instrumentDropdown.classList.add("hidden");
            moreToggleBtn.setAttribute("aria-expanded", "false");
            if (outsideHandler) {
                document.removeEventListener("click", outsideHandler);
                outsideHandler = null;
            }
        };

        moreToggleBtn.addEventListener("click", (event) => {
            event.stopPropagation();
            const isOpen = !instrumentDropdown.classList.contains("hidden");
            instrumentDropdown.classList.toggle("hidden", isOpen);
            moreToggleBtn.setAttribute("aria-expanded", String(!isOpen));
            if (!isOpen) {
                outsideHandler = (evt) => {
                    if (
                        instrumentDropdown.contains(evt.target) ||
                        evt.target === moreToggleBtn
                    ) {
                        return;
                    }
                    closeDropdown();
                };
                document.addEventListener("click", outsideHandler);
            } else {
                closeDropdown();
            }
        });
    }
}

function initAdminEditor() {
    const modal = document.getElementById("adminModal");
    const form = document.getElementById("adminForm");
    if (!modal || !form) return;

    const closeBtn = document.getElementById("adminClose");
    const cancelBtn = document.getElementById("adminCancel");
    const quickSaveBtn = document.getElementById("adminQuickSave");
    const aiBtn = document.getElementById("adminAI");
    const statusEl = document.getElementById("adminStatus");
    const saveBtn = form.querySelector('button[type="submit"]');
    const deleteBtn = document.getElementById("adminDelete");

    const relInput = document.getElementById("entryRelPath");
    const slugInput = document.getElementById("entrySlug");
    const fields = {
        name: document.getElementById("entryName"),
        composer: document.getElementById("entryComposer"),
        contributor: document.getElementById("entryContributor"),
        modified_by: document.getElementById("entryModifier"),
        source: document.getElementById("entrySource"),
        license: document.getElementById("entryLicense"),
        instrument: document.getElementById("entryInstrument"),
        attachments: document.getElementById("entryAttachments"),
        genre: document.getElementById("entryGenre"),
        tags: document.getElementById("entryTags"),
        bpm: document.getElementById("entryBpm"),
    };

    const setAiState = (running) => {
        setLogoThinking(running);
        if (aiBtn) {
            aiBtn.disabled = running;
            aiBtn.classList.toggle("loading", running);
            aiBtn.textContent = running ? "🧠" : "🤖";
        }
        if (quickSaveBtn) quickSaveBtn.disabled = running;
        if (saveBtn) saveBtn.disabled = running;
        if (deleteBtn) deleteBtn.disabled = running;
    };

    const collectMetadata = () => ({
        name: fields.name.value,
        composer: fields.composer.value,
        contributor: fields.contributor.value,
        modified_by: fields.modified_by.value,
        source: fields.source.value,
        license: fields.license.value,
        instrument: fields.instrument.value,
        attachments: fields.attachments.value,
        bpm: fields.bpm.value,
        genre: fields.genre.value,
        tags: fields.tags.value,
    });

    const hideModal = () => {
        modal.classList.add("hidden");
        statusEl.textContent = "";
        setAiState(false);
    };

    const showModal = (button) => {
        const metaDefaults = (() => {
            try {
                return button.dataset.meta ? JSON.parse(button.dataset.meta) : {};
            } catch (error) {
                console.warn("Failed to parse meta defaults", error);
                return {};
            }
        })();

        relInput.value = button.dataset.relPath || "";
        slugInput.value = button.dataset.slug || "";
        fields.name.value = metaDefaults.name || "";
        fields.composer.value = metaDefaults.composer || "";
        fields.contributor.value = metaDefaults.contributor || "";
        fields.modified_by.value = metaDefaults.modified_by || "";
        fields.source.value = metaDefaults.source || "";
        fields.license.value = metaDefaults.license || "Public Domain";
        fields.instrument.value = button.dataset.instrument || metaDefaults.instrument || "piano";
        fields.bpm.value = metaDefaults.bpm || "";
        fields.attachments.value = (metaDefaults.attachments || []).join(", ");
        fields.genre.value = metaDefaults.genre || "";
        fields.tags.value = metaDefaults.tags || "";

        statusEl.textContent = "";
        setAiState(false);
        modal.classList.remove("hidden");
    };

    document.querySelectorAll(".edit-btn").forEach((btn) => {
        btn.addEventListener("click", () => showModal(btn));
    });

    [closeBtn, cancelBtn].forEach((btn) => {
        if (!btn) return;
        btn.addEventListener("click", hideModal);
    });

    if (quickSaveBtn) {
        quickSaveBtn.addEventListener("click", () => {
            form.requestSubmit();
        });
    }

    modal.addEventListener("click", (event) => {
        if (event.target === modal) {
            hideModal();
        }
    });

    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        statusEl.textContent = "Saving…";

        const payload = {
            rel_path: relInput.value,
            new_slug: slugInput.value,
            metadata: collectMetadata(),
        };

        try {
            const response = await fetch("/api/entry/update", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                },
                body: JSON.stringify(payload),
            });

            if (!response.ok) {
                const errorBody = await response.json().catch(() => ({}));
                throw new Error(errorBody.error || "Failed to save changes");
            }

            statusEl.textContent = "Saved — refreshing…";
            setTimeout(() => window.location.reload(), 600);
        } catch (error) {
            console.error(error);
            statusEl.textContent = error.message || "Unable to save changes";
        }
    });

    if (aiBtn) {
        aiBtn.addEventListener("click", async () => {
            if (!relInput.value) return;
            setAiState(true);
            statusEl.textContent = "Generating suggestions…";

            const payload = {
                rel_path: relInput.value,
                metadata: collectMetadata(),
            };

            try {
                const response = await fetch("/api/entry/ai-suggest", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                    },
                    body: JSON.stringify(payload),
                });
                const body = await response.json().catch(() => ({}));
                if (!response.ok) {
                    throw new Error(body.error || "AI request failed");
                }

                const suggestion = body.suggestion || {};
                if (suggestion.name) fields.name.value = suggestion.name;
                if (suggestion.composer) fields.composer.value = suggestion.composer;
                if (typeof suggestion.bpm === "number" && suggestion.bpm > 0) {
                    fields.bpm.value = Math.round(suggestion.bpm);
                }
                if (suggestion.genre !== undefined) {
                    fields.genre.value = suggestion.genre || "";
                }
                if (suggestion.tags) {
                    if (Array.isArray(suggestion.tags)) {
                        fields.tags.value = suggestion.tags.join(", ");
                    } else if (typeof suggestion.tags === "string") {
                        fields.tags.value = suggestion.tags;
                    }
                }
                statusEl.textContent = "AI suggestions applied — review before saving.";
            } catch (error) {
                console.error(error);
                statusEl.textContent = error.message || "AI request failed";
            } finally {
                setAiState(false);
            }
        });
    }

    if (deleteBtn) {
        deleteBtn.addEventListener("click", async () => {
            if (!relInput.value) return;
            if (!window.confirm("Delete this MIDI and all attachments?")) {
                return;
            }
            setAiState(true);
            statusEl.textContent = "Deleting entry…";
            try {
                const response = await fetch("/api/entry/delete", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                    },
                    body: JSON.stringify({
                        rel_path: relInput.value,
                        attachments: fields.attachments.value,
                    }),
                });
                if (!response.ok) {
                    const body = await response.json().catch(() => ({}));
                    throw new Error(body.error || "Delete failed");
                }
                statusEl.textContent = "Entry deleted — refreshing…";
                setTimeout(() => window.location.reload(), 600);
            } catch (error) {
                console.error(error);
                statusEl.textContent = error.message || "Unable to delete entry";
                setAiState(false);
            }
        });
    }
}


function resetButtons() {
    buttons.forEach((btn) => {
        btn.innerHTML = "▶";
        btn.dataset.playing = "false";
        btn.classList.remove("is-active");
    });
}

function updatePlayPauseButton() {
    if (!playPauseBtn) return;
    playPauseBtn.textContent = isPlaying ? "⏸" : "▶";
}

function stopProgressLoop() {
    if (progressLoopId) {
        cancelAnimationFrame(progressLoopId);
        progressLoopId = null;
    }
}

function startProgressLoop() {
    stopProgressLoop();

    const step = () => {
        if (!isPlaying || !midiDuration) return;

        const currentSeconds = Math.min(Tone.Transport.seconds, midiDuration);
        const ratio = midiDuration ? currentSeconds / midiDuration : 0;
        if (currentTimeEl) {
            currentTimeEl.textContent = formatTime(currentSeconds);
        }

        if (progressSlider && !isScrubbing) {
            progressSlider.value = String(Math.round(ratio * SLIDER_MAX));
        }

        if (currentSeconds >= midiDuration) {
            handlePlaybackFinished();
            return;
        }

        progressLoopId = requestAnimationFrame(step);
    };

    progressLoopId = requestAnimationFrame(step);
}

function handlePlaybackFinished() {
    stopProgressLoop();
    Tone.Transport.stop();
    Tone.Transport.position = 0;
    isPlaying = false;
    setLogoPlaying(false);
    updatePlayPauseButton();
    setStatus("Finished");

    if (progressSlider) {
        progressSlider.value = String(SLIDER_MAX);
    }

    if (currentTimeEl) {
        currentTimeEl.textContent = formatTime(midiDuration);
    }

    resetButtons();
    currentButton = null;
}

function teardownPlayback() {
    stopProgressLoop();
    Tone.Transport.stop();
    Tone.Transport.cancel(0);

    if (currentPart) {
        currentPart.dispose();
        currentPart = null;
    }

    if (currentSynth) {
        currentSynth.dispose();
        currentSynth = null;
    }

    isPlaying = false;
    setLogoPlaying(false);
    currentButton = null;
}

function pauseTransportPlayback() {
    if (!currentPart || !isPlaying) return;
    Tone.Transport.pause();
    isPlaying = false;
    setLogoPlaying(false);
    stopProgressLoop();
    setStatus("Paused");
    updatePlayPauseButton();
    if (currentButton) {
        currentButton.innerHTML = "▶";
    }
}

function resumeTransportPlayback() {
    if (!currentPart || isPlaying) return;
    Tone.Transport.start();
    isPlaying = true;
    setLogoPlaying(true);
    startProgressLoop();
    setStatus("Playing");
    updatePlayPauseButton();
    if (currentButton) {
        currentButton.innerHTML = "⏸";
    }
}

function resetPlayerUi() {
    if (progressSlider) {
        progressSlider.value = "0";
        progressSlider.disabled = true;
    }

    if (currentTimeEl) currentTimeEl.textContent = "0:00";
    if (totalTimeEl) totalTimeEl.textContent = "0:00";

    if (playPauseBtn) {
        playPauseBtn.disabled = true;
        playPauseBtn.textContent = "▶";
    }

}

function enablePlayerControls() {
    if (playPauseBtn) playPauseBtn.disabled = false;
    if (progressSlider) progressSlider.disabled = midiDuration <= 0;
}

function collectEvents(midi, timeScale = 1) {
    const events = [];
    midi.tracks.forEach((track) => {
        track.notes.forEach((note) =>
            events.push({
                time: note.time * timeScale,
                duration: note.duration * timeScale,
                name: note.name,
                velocity: note.velocity,
            })
        );
    });
    return events;
}

function calcMidiDuration(midi, timeScale = 1) {
    if (Number.isFinite(midi.duration) && midi.duration > 0) {
        return midi.duration * timeScale;
    }

    let maxTime = 0;
    midi.tracks.forEach((track) => {
        track.notes.forEach((note) => {
            maxTime = Math.max(maxTime, note.time + note.duration);
        });
    });

    return maxTime * timeScale;
}

async function loadAndPlayMidi(url, name, btn) {
    try {
        await Tone.start();

        showPlayerBar();
        teardownPlayback();
        resetButtons();
        resetPlayerUi();
        setLogoPlaying(false);

        if (titleEl) titleEl.textContent = name;
        setStatus("Loading MIDI...");

        currentButton = btn;

        if (btn) {
            btn.classList.add("is-active");
            btn.innerHTML = "⏳";
        }

        const midi = await Midi.fromUrl(url);
        const tempos = midi.header && midi.header.tempos ? midi.header.tempos : [];
        const tempoEvent = tempos.length ? tempos[0] : null;
        let bpmOverride = null;
        if (btn && btn.dataset.midiBpm) {
            const parsed = Number(btn.dataset.midiBpm);
            if (Number.isFinite(parsed) && parsed > 0) {
                bpmOverride = parsed;
            }
        }
        const baseBpm = tempoEvent && tempoEvent.bpm ? tempoEvent.bpm : 120;
        const targetBpm = bpmOverride || baseBpm;
        const timeScale = baseBpm && bpmOverride ? baseBpm / bpmOverride : 1;
        Tone.Transport.bpm.value = targetBpm;

        midiDuration = calcMidiDuration(midi, timeScale);
        if (totalTimeEl) totalTimeEl.textContent = formatTime(midiDuration);

        const desiredInstrument =
            (btn && btn.dataset.instrumentDefault) || currentInstrumentId || readStoredInstrument();
        applyInstrumentSelection(desiredInstrument, { persist: false });

        if (!currentInstrumentId) {
            currentInstrumentId = desiredInstrument;
        }

        currentSynth = createSynthForPreset(currentInstrumentId);
        const events = collectEvents(midi, timeScale);

        currentPart = new Tone.Part((time, note) => {
            currentSynth.triggerAttackRelease(note.name, note.duration, time, note.velocity);
        }, events).start(0);

        Tone.Transport.position = 0;
        Tone.Transport.start();
        isPlaying = true;
        setLogoPlaying(true);
        updatePlayPauseButton();
        enablePlayerControls();
        startProgressLoop();

        if (btn) {
            btn.dataset.playing = "true";
            btn.innerHTML = "⏸";
        }

        setStatus("Playing");
    } catch (error) {
        console.error(error);
        setStatus("Playback error — check console");
        resetButtons();
        resetPlayerUi();
        currentButton = null;
        setLogoPlaying(false);
    }
}

buttons.forEach((btn) => {
    btn.dataset.playing = "false";
    btn.addEventListener("click", () => {
        const midiUrl = btn.dataset.midiUrl;
        const name = btn.dataset.midiName || "MIDI file";

        if (!midiUrl) return;
        const isCurrent = currentButton === btn;

        if (isCurrent && isPlaying) {
            pauseTransportPlayback();
            return;
        }

        if (isCurrent && !isPlaying && currentPart) {
            resumeTransportPlayback();
            return;
        }

        loadAndPlayMidi(midiUrl, name, btn);
    });
});

if (playPauseBtn) {
    playPauseBtn.addEventListener("click", () => {
        if (!currentPart) return;

        if (isPlaying) {
            pauseTransportPlayback();
        } else {
            resumeTransportPlayback();
        }
    });
}

if (progressSlider) {
    const valueToSeconds = () => {
        const ratio = Number(progressSlider.value) / SLIDER_MAX;
        return midiDuration * Math.min(Math.max(ratio, 0), 1);
    };

    const commitScrub = () => {
        if (!midiDuration || !currentPart) {
            isScrubbing = false;
            return;
        }

        const targetSeconds = valueToSeconds();
        Tone.Transport.seconds = Math.min(targetSeconds, midiDuration);

        if (!isPlaying) {
            if (currentTimeEl) currentTimeEl.textContent = formatTime(targetSeconds);
        } else {
            startProgressLoop();
        }

        isScrubbing = false;
    };

    const beginScrub = () => {
        if (progressSlider.disabled) return;
        if (!isScrubbing) {
            isScrubbing = true;
            stopProgressLoop();
        }
    };

    progressSlider.addEventListener("mousedown", beginScrub);
    progressSlider.addEventListener("touchstart", beginScrub);

    ["mouseup", "touchend", "touchcancel"].forEach((evt) => {
        progressSlider.addEventListener(evt, () => {
            if (!isScrubbing) return;
            commitScrub();
        });
    });

    progressSlider.addEventListener("input", () => {
        if (!midiDuration) return;
        const targetSeconds = valueToSeconds();
        if (currentTimeEl) currentTimeEl.textContent = formatTime(targetSeconds);

        if (!isScrubbing && currentPart) {
            Tone.Transport.seconds = Math.min(targetSeconds, midiDuration);
            if (isPlaying) {
                startProgressLoop();
            }
        }
    });
}

window.addEventListener("beforeunload", () => {
    teardownPlayback();
});

initVolumeControl();
initMetaCopyButtons();
initInstrumentControls();
initAdminEditor();
