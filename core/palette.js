.pragma library

// Colour picking for the media visualizer. Inputs are ColorQuantizer colours; outputs are Qt colours.

function vividness(color) {
    return color.hsvSaturation * color.hsvValue;
}

function hueGap(a, b) {
    return Math.min(Math.abs(a - b), 1 - Math.abs(a - b));
}

// The most vivid colour and a partner with a clearly different hue, ignoring near-black buckets.
// Returns null without usable colours and {grey: true} when the image is essentially monochrome.
function vividPair(colors) {
    const usable = [];
    for (let i = 0; i < (colors ? colors.length : 0); i++)
        if (colors[i].hsvValue > 0.2)
            usable.push(colors[i]);
    if (!usable.length)
        return null;
    usable.sort((a, b) => vividness(b) - vividness(a));
    if (vividness(usable[0]) < 0.12)
        return {grey: true, first: usable[0], second: usable[0]};
    const second = usable.find(color => vividness(color) >= 0.12 && hueGap(color.hslHue, usable[0].hslHue) > 0.08) || usable[0];
    return {grey: false, first: usable[0], second: second};
}

// Lifts a colour so it reads over dark surfaces and busy artwork.
function tone(color, lightness) {
    return Qt.hsla(Math.max(0, color.hslHue), Math.max(0.5, color.hslSaturation), Math.max(lightness, Math.min(0.8, color.hslLightness)), 1);
}
