let currentPart = null;
let currentSynth = null;
let isPlaying = false;
let currentButton = null;

const statusEl = document.getElementById("status");
const buttons = document.querySelectorAll(".play-btn");

function setStatus(text) {
    if (statusEl) statusEl.textContent = text;
}

function resetButtons() {
    buttons.forEach((btn) => {
        btn.textContent = "▶ Play";
        btn.dataset.playing = "false";
    });
}

function stopPlayback() {
    if (!isPlaying) return;

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

    resetButtons();
    setStatus("Stopped");
}

async function playMidiFromUrl(url, button) {
    try {
        await Tone.start();

        stopPlayback();
        setStatus("Loading MIDI...");

        const midi = await Midi.fromUrl(url);

        currentSynth = new Tone.PolySynth(Tone.Synth).toDestination();

        const tempoEvent = midi.header.tempos[0];
        Tone.Transport.bpm.value = tempoEvent?.bpm || 120;

        const events = [];

        midi.tracks.forEach((track) => {
            track.notes.forEach((note) => {
                events.push(note);
            });
        });

        currentPart = new Tone.Part((time, note) => {
            currentSynth.triggerAttackRelease(note.name, note.duration, time, note.velocity);
        }, events).start(0);

        Tone.Transport.position = 0;
        Tone.Transport.start();

        isPlaying = true;
        currentButton = button;

        button.textContent = "⏹ Stop";
        button.dataset.playing = "true";

        setStatus("Playing");
    } catch (e) {
        console.error(e);
        setStatus("Playback error — check console");
    }
}

buttons.forEach((btn) => {
    btn.dataset.playing = "false";

    btn.addEventListener("click", () => {
        const isBtnPlaying = btn.dataset.playing === "true";
        const midiUrl = btn.dataset.midiUrl;

        if (!midiUrl) return;

        if (isBtnPlaying) {
            stopPlayback();
        } else {
            resetButtons();
            playMidiFromUrl(midiUrl, btn);
        }
    });
});

window.addEventListener("beforeunload", () => {
    stopPlayback();
});
