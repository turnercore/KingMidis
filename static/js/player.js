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
const VOLUME_STORAGE_KEY = "km-volume";
const DEFAULT_VOLUME = 80;

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

function volumeToDb(value) {
    const minDb = -48;
    const maxDb = 0;
    if (value <= 0) return -60;
    const ratio = value / 100;
    return minDb + (maxDb - minDb) * ratio;
}

function applyVolume(value, { persist = true, updateSlider = true } = {}) {
    const clamped = Math.min(Math.max(value, 0), 100);
    const db = volumeToDb(clamped);
    Tone.Destination.volume.rampTo(db, 0.05);

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
    applyVolume(initialVolume, { persist: false, updateSlider: true });

    if (!volumeSlider) return;

    volumeSlider.addEventListener("input", () => {
        const value = Number(volumeSlider.value);
        applyVolume(value, { persist: true, updateSlider: false });
    });
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

function initAdminEditor() {
    const modal = document.getElementById("adminModal");
    const form = document.getElementById("adminForm");
    if (!modal || !form) return;

    const closeBtn = document.getElementById("adminClose");
    const cancelBtn = document.getElementById("adminCancel");
    const statusEl = document.getElementById("adminStatus");

    const relInput = document.getElementById("entryRelPath");
    const slugInput = document.getElementById("entrySlug");
    const fields = {
        name: document.getElementById("entryName"),
        composer: document.getElementById("entryComposer"),
        editor: document.getElementById("entryEditor"),
        modified_by: document.getElementById("entryModifier"),
        source: document.getElementById("entrySource"),
        license: document.getElementById("entryLicense"),
        instruments: document.getElementById("entryInstruments"),
    };

    const hideModal = () => {
        modal.classList.add("hidden");
        statusEl.textContent = "";
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
        fields.editor.value = metaDefaults.editor || "";
        fields.modified_by.value = metaDefaults.modified_by || "";
        fields.source.value = metaDefaults.source || "";
        fields.license.value = metaDefaults.license || "Public Domain";
        fields.instruments.value = metaDefaults.instruments || "";

        statusEl.textContent = "";
        modal.classList.remove("hidden");
    };

    document.querySelectorAll(".edit-btn").forEach((btn) => {
        btn.addEventListener("click", () => showModal(btn));
    });

    [closeBtn, cancelBtn].forEach((btn) => {
        if (!btn) return;
        btn.addEventListener("click", hideModal);
    });

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
            metadata: {
                name: fields.name.value,
                composer: fields.composer.value,
                editor: fields.editor.value,
                modified_by: fields.modified_by.value,
                source: fields.source.value,
                license: fields.license.value,
                instruments: fields.instruments.value,
            },
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
}


function resetButtons() {
    buttons.forEach((btn) => {
        btn.textContent = "▶ Preview";
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
    currentButton = null;
}

function pauseTransportPlayback() {
    if (!currentPart || !isPlaying) return;
    Tone.Transport.pause();
    isPlaying = false;
    stopProgressLoop();
    setStatus("Paused");
    updatePlayPauseButton();
    if (currentButton) {
        currentButton.textContent = "Paused";
    }
}

function resumeTransportPlayback() {
    if (!currentPart || isPlaying) return;
    Tone.Transport.start();
    isPlaying = true;
    startProgressLoop();
    setStatus("Playing");
    updatePlayPauseButton();
    if (currentButton) {
        currentButton.textContent = "Playing…";
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

function collectEvents(midi) {
    const events = [];
    midi.tracks.forEach((track) => {
        track.notes.forEach((note) => events.push(note));
    });
    return events;
}

function calcMidiDuration(midi) {
    if (Number.isFinite(midi.duration) && midi.duration > 0) {
        return midi.duration;
    }

    let maxTime = 0;
    midi.tracks.forEach((track) => {
        track.notes.forEach((note) => {
            maxTime = Math.max(maxTime, note.time + note.duration);
        });
    });

    return maxTime;
}

async function loadAndPlayMidi(url, name, btn) {
    try {
        await Tone.start();

        showPlayerBar();
        teardownPlayback();
        resetButtons();
        resetPlayerUi();

        if (titleEl) titleEl.textContent = name;
        setStatus("Loading MIDI...");

        currentButton = btn;

        if (btn) {
            btn.classList.add("is-active");
            btn.textContent = "Loading…";
        }

        const midi = await Midi.fromUrl(url);
        const tempos = midi.header && midi.header.tempos ? midi.header.tempos : [];
        const tempoEvent = tempos.length ? tempos[0] : null;
        Tone.Transport.bpm.value = tempoEvent && tempoEvent.bpm ? tempoEvent.bpm : 120;

        midiDuration = calcMidiDuration(midi);
        if (totalTimeEl) totalTimeEl.textContent = formatTime(midiDuration);

        currentSynth = new Tone.PolySynth(Tone.Synth).toDestination();
        const events = collectEvents(midi);

        currentPart = new Tone.Part((time, note) => {
            currentSynth.triggerAttackRelease(note.name, note.duration, time, note.velocity);
        }, events).start(0);

        Tone.Transport.position = 0;
        Tone.Transport.start();
        isPlaying = true;
        updatePlayPauseButton();
        enablePlayerControls();
        startProgressLoop();

        if (btn) {
            btn.dataset.playing = "true";
            btn.textContent = "Playing…";
        }

        setStatus("Playing");
    } catch (error) {
        console.error(error);
        setStatus("Playback error — check console");
        resetButtons();
        resetPlayerUi();
        currentButton = null;
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
initAdminEditor();
