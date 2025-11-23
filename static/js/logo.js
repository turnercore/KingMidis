(function () {
    const logos = document.querySelectorAll(".brand-logo");
    if (!logos.length) return;

    const state = {
        base: document.body.dataset.logoBase || "default",
        playing: false,
        thinking: false,
    };

    const applyState = () => {
        let active = state.base;
        if (state.thinking) {
            active = "thinking";
        } else if (state.playing && state.base === "default") {
            active = "sing";
        }
        logos.forEach((logo) => {
            logo.setAttribute("data-logo-state", active);
        });
    };

    window.kmLogoSetPlaying = (flag) => {
        state.playing = !!flag;
        applyState();
    };

    window.kmLogoSetThinking = (flag) => {
        state.thinking = !!flag;
        applyState();
    };

    applyState();
})();

