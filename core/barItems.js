.pragma library
.import "i18n.js" as I18n
// What the bar can show, in its default places and order. helpers/backend.py (BAR_ITEMS) checks saved layouts
// against these IDs. Required items can move but never hide: what is recording or listening stays visible, and the
// control center button stays reachable.
const items = [
    {id: "launcher", title: I18n.t("bar.item.launcher"), icon: "grid", zone: "left", description: I18n.t("bar.item.launcherInfo")},
    {id: "workspaces", title: I18n.t("bar.item.workspaces"), icon: "window", zone: "left", description: I18n.t("bar.item.workspacesInfo")},
    {id: "windowTitle", title: I18n.t("bar.item.windowTitle"), icon: "window", zone: "left", description: I18n.t("bar.item.windowTitleInfo")},
    {id: "clock", title: I18n.t("bar.item.clock"), icon: "calendar", zone: "center", description: I18n.t("bar.item.clockInfo")},
    {id: "temperature", title: I18n.t("bar.item.temperature"), icon: "cloud-sun", zone: "right", description: I18n.t("bar.item.temperatureInfo")},
    {id: "media", title: I18n.t("bar.item.media"), icon: "music", zone: "right", description: I18n.t("bar.item.mediaInfo")},
    {id: "recording", title: I18n.t("bar.item.recording"), icon: "record", zone: "right", required: true, description: I18n.t("bar.item.recordingInfo")},
    {id: "privacy", title: I18n.t("bar.item.privacy"), icon: "mic", zone: "right", required: true, description: I18n.t("bar.item.privacyInfo")},
    {id: "focus", title: I18n.t("bar.item.focus"), icon: "focus", zone: "right", description: I18n.t("bar.item.focusInfo")},
    {id: "timer", title: I18n.t("bar.item.timer"), icon: "timer", zone: "right", description: I18n.t("bar.item.timerInfo")},
    {id: "stayAwake", title: I18n.t("bar.item.stayAwake"), icon: "moon", zone: "right", description: I18n.t("bar.item.stayAwakeInfo")},
    {id: "keyboard", title: I18n.t("bar.item.keyboard"), icon: "region", zone: "right", description: I18n.t("bar.item.keyboardInfo")},
    {id: "system", title: I18n.t("bar.item.system"), icon: "gauge", zone: "right", description: I18n.t("bar.item.systemInfo")},
    {id: "wifi", title: I18n.t("bar.item.wifi"), icon: "wifi", zone: "right"},
    {id: "audio", title: I18n.t("bar.item.audio"), icon: "volume", zone: "right", description: I18n.t("bar.item.audioInfo")},
    {id: "bluetooth", title: I18n.t("bar.item.bluetooth"), icon: "bluetooth", zone: "right", description: I18n.t("bar.item.bluetoothInfo")},
    {id: "battery", title: I18n.t("bar.item.battery"), icon: "battery", zone: "right", description: I18n.t("bar.item.batteryInfo")},
    {id: "notifications", title: I18n.t("bar.item.notifications"), icon: "bell", zone: "right"},
    {id: "tray", title: I18n.t("bar.item.tray"), icon: "more", zone: "right", description: I18n.t("bar.item.trayInfo")},
    {id: "control", title: I18n.t("bar.item.control"), icon: "settings", zone: "right", required: true}
];
// Old IDs that saved layouts may still hold, read as the item that replaced them.
const aliases = {};
// IDs helpers/backend.py (BAR_ITEMS) doesn't accept yet. Until it does, layouts are saved without them (they keep
// their default place). Empty this once the backend knows them.
const unsaved = ["system"];
const savedAs = {};
const zones = ["left", "center", "right", "hidden"];

function find(id) {
    id = aliases[id] || id;
    return items.find(item => item.id === id) || null;
}

// A saved layout ({left, center, right, hidden}, any part missing) with every item placed once. An item the saved
// layout doesn't mention (one added in a later release) goes to its default zone, after the items it follows there.
function layout(saved) {
    const result = {left: [], center: [], right: [], hidden: []};
    const placed = {};
    for (const zone of zones)
        for (const id of (saved && Array.isArray(saved[zone]) ? saved[zone] : [])) {
            const item = find(id);
            if (!item || placed[item.id])
                continue;
            const target = zone === "hidden" && item.required ? item.zone : zone;
            result[target].push(item.id);
            placed[item.id] = true;
        }
    items.forEach((item, index) => {
        if (placed[item.id])
            return;
        const list = result[item.zone];
        const before = items.slice(0, index).map(other => other.id);
        let at = 0;
        list.forEach((id, position) => {
            if (before.indexOf(id) >= 0)
                at = position + 1;
        });
        list.splice(at, 0, item.id);
        placed[item.id] = true;
    });
    return result;
}

// A layout in the IDs the backend accepts today (see `unsaved` and `savedAs`).
function storable(layout) {
    const result = {};
    for (const zone of zones)
        result[zone] = layout[zone].filter(id => unsaved.indexOf(id) < 0).map(id => savedAs[id] || id);
    return result;
}

// `saved` with `id` moved to `zone` (at its end), or by `step` places within its zone, ready to save.
function moved(saved, id, zone, step) {
    const current = layout(saved);
    const from = zones.find(name => current[name].indexOf(id) >= 0);
    const index = current[from].indexOf(id);
    if (zone && zone !== from) {
        current[from].splice(index, 1);
        current[zone].push(id);
    } else if (step) {
        const to = Math.max(0, Math.min(current[from].length - 1, index + step));
        current[from].splice(index, 1);
        current[from].splice(to, 0, id);
    }
    return storable(current);
}
