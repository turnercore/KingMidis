(function () {
    const logos = document.querySelectorAll(".brand-logo");
    if (!logos.length) return;

    const globalBase = document.body.dataset.logoBase || "default";
    const logoBases = new Map();
    logos.forEach((logo) => {
        const elementBase = logo.dataset.logoState || globalBase;
        logoBases.set(logo, elementBase);
    });

    const state = {
        base: globalBase,
        playing: false,
        thinking: false,
    };

    const applyState = () => {
        logos.forEach((logo) => {
            const elementBase = logoBases.get(logo) || state.base;
            let active = elementBase;
            if (state.thinking) {
                active = "thinking";
            } else if (state.playing && elementBase === "default") {
                active = "sing";
            }
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
