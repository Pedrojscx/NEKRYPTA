pragma Singleton
import QtQuick
import Quickshell
import Quickshell.Services.Mpris
import "../core"
import "../core/i18n.js" as I18n
import "../core/palette.js" as Palette
import "." as S

// Shared state for everything that shows the current track: the backend visualizer's frames, the cover's
// colours, and a signal when the track changes. CAVA itself runs only in the backend (visualizer.*).
Singleton {
    id: root
    readonly property var player: S.Services.player
    readonly property bool playing: !!player && player.isPlaying
    readonly property string artUrl: player ? player.trackArtUrl || "" : ""

    // The player's own volume (MPRIS), never the system's: 5% steps, 0–100%. A player that doesn't expose it is
    // left alone and the user is told why. volumeNote ("Spotify: 65%") is the short feedback the media views show.
    readonly property bool volumeAdjustable: !!player && player.canControl && player.volumeSupported
    // The text stays after it hides, so views can fade it out instead of blanking it first.
    property string volumeNote: ""
    property bool volumeNoteShown: false
    function stepPlayerVolume(steps) {
        if (!player || !steps)
            return;
        if (!volumeAdjustable) {
            if (!volumeRefusal.running)
                ShellState.message(I18n.t("media.volumeUnsupported"), "info");
            volumeRefusal.restart();
            return;
        }
        const next = Math.max(0, Math.min(1, Math.round((player.volume + steps * 0.05) * 20) / 20));
        player.volume = next;
        volumeNote = I18n.t("media.volumeNote", {player: player.identity || "", percent: Math.round(next * 100)});
        volumeNoteShown = true;
        volumeNoteClear.restart();
    }
    property Timer volumeNoteClear: Timer {
        interval: 1600
        onTriggered: root.volumeNoteShown = false
    }
    // One explanation per burst of scrolling, not one per notch.
    property Timer volumeRefusal: Timer {
        interval: 4000
    }

    // Visualizers call acquire() while they draw and release() when they stop. All of them together hold one
    // backend reference, so the backend never counts more than one consumer from this shell.
    property int demand: 0
    readonly property int bars: 32
    // The default comes from the backend's settings (on); the user's own choice, once saved, wins.
    readonly property bool enabled: ShellState.setting("visualizer", true)
    // Backend state object: {available, enabled, state, source, bands, errorCode, errorMessage}.
    property var backendState: ({})
    readonly property bool available: backendState.available === true
    // Real frames only: 32 levels in 0..1 and their energy. Empty whenever there is no live frame.
    property var levels: []
    property real energy: 0
    // The states of docs/architecture/rede-contratos.md ("Estado do visualizer"). The process's own (carregando,
    // reproduzindo, sem_audio, indisponivel, erro) come from the backend, read under their older names too
    // (iniciando, ativo, sem_musica, dependencia_ausente); pausado, sem_player and aguardando_reproducao come from
    // MPRIS here, and in those the backend is simply not asked for frames. Plus desligado (the switch) and
    // reduzido (reduced motion, nothing animates).
    readonly property string backendRaw: backendState.state || "desativado"
    readonly property string visualizerState: {
        const backend = backendRaw;
        if (!enabled)
            return "desligado";
        if (!player)
            return "sem_player";
        if (backend === "dependencia_ausente" || backend === "indisponivel")
            return "indisponivel";
        if (backend === "erro")
            return "erro";
        if (Theme.reduced)
            return "reduzido";
        if (!playing)
            return player.playbackState === MprisPlaybackState.Paused ? "pausado" : "aguardando_reproducao";
        if (backend === "ativo" || backend === "reproduzindo")
            return "reproduzindo";
        if (backend === "sem_musica" || backend === "sem_audio")
            return "sem_audio";
        return "carregando";
    }
    // Real frames are flowing.
    readonly property bool live: visualizerState === "reproduzindo"
    readonly property bool busy: backendRaw === "iniciando" || backendRaw === "carregando" || backendRaw === "encerrando"
    function acquire() { demand++; }
    function release() { demand = Math.max(0, demand - 1); }
    property bool held: false
    onDemandChanged: sync()
    function sync() {
        const want = demand > 0 && Backend.ready;
        if (want === held)
            return;
        held = want;
        if (want)
            Backend.request("visualizer.start", {}, (result, error) => {
                // Refused (no CAVA) or failed to spawn: the backend dropped the reference itself.
                if (error || !result || !result.accepted)
                    root.held = false;
                root.refresh();
            });
        else
            Backend.request("visualizer.stop", {}, () => root.refresh());
    }
    function refresh() {
        if (Backend.ready)
            Backend.request("visualizer.status", {}, (result, error) => {
                if (!error && result)
                    root.backendState = result;
            });
    }
    // After an error: give back any reference still held, then ask again once.
    function retry() {
        if (busy)
            return;
        if (held) {
            held = false;
            Backend.request("visualizer.stop", {}, () => root.sync());
        } else {
            refresh();
            sync();
        }
    }
    onEnabledChanged: if (enabled)
        refresh()
    onBackendStateChanged: if (backendState.state !== "ativo" && backendState.state !== "reproduzindo") {
        levels = [];
        energy = 0;
    }
    property Connections events: Connections {
        target: Backend
        function onEventReceived(name, data) {
            if (name === "visualizer.state")
                root.backendState = data;
            else if (name === "visualizer.frame" && root.held && Array.isArray(data.levels) && data.levels.length === root.bars) {
                root.levels = data.levels;
                root.energy = Number(data.energy) || 0;
            }
        }
        function onReadyChanged() {
            // A restarted backend starts with no consumers.
            root.held = false;
            if (Backend.ready) {
                root.resolveArt();
                root.refresh();
                root.sync();
            }
        }
    }

    // Two tones from the cover: its most vivid colour and a contrasting one.
    // Greyscale covers get soft white; no cover falls back to the accent.
    // The quantizer only reads files, so the helper saves https:// and data: covers first. It exists only while
    // there is a file: an empty source makes it try to open "". Every cover shown uses this file too, never the
    // player's address: the helper fetches only from public addresses, and the display must not bypass that.
    property string artFile: ""
    property int artRequest: 0
    onArtUrlChanged: resolveArt()
    // A player may already be playing when this is first created.
    Component.onCompleted: {
        resolveArt();
        refresh();
    }
    function resolveArt() {
        const url = root.artUrl;
        const request = ++artRequest;
        if (/^file:\/\//i.test(url)) {
            artFile = url;
        } else if (/^(https?:|data:image\/)/i.test(url) && Backend.ready) {
            // The accent until the new cover arrives, never the previous track's colours.
            artFile = "";
            Backend.request("media.art", {url: url}, (result, error) => {
                if (request === root.artRequest)
                    root.artFile = error ? "" : "file://" + result.path.split("/").map(encodeURIComponent).join("/");
            });
        } else {
            artFile = "";
        }
    }
    LazyLoader {
        id: quantizer
        active: root.artFile !== ""
        ColorQuantizer {
            source: root.artFile
            depth: 3
            rescaleSize: 64
        }
    }
    readonly property var artColors: {
        const pair = root.artUrl !== "" && quantizer.item ? Palette.vividPair(quantizer.item.colors) : null;
        if (!pair)
            return [Theme.accent, Theme.accentHover];
        if (pair.grey)
            return [Qt.hsla(0, 0, 0.9, 1), Qt.hsla(0, 0, 0.6, 1)];
        // Lift lightness and saturation so bars read over the artwork itself.
        return [Palette.tone(pair.first, 0.66), Palette.tone(pair.second, 0.5)];
    }

    // Players fill metadata in several steps, so a change is announced once it has settled.
    // The first track seen after start-up (or after the player appears) is never announced.
    signal trackChanged()
    readonly property string trackKey: player && player.trackTitle ? player.dbusName + "\n" + player.trackTitle + "\n" + (player.trackArtist || "") : ""
    property string announcedKey: ""
    property string playerName: ""
    onTrackKeyChanged: settle.restart()
    Timer {
        id: settle
        interval: 600
        onTriggered: {
            const key = root.trackKey;
            const name = root.player ? root.player.dbusName : "";
            const known = root.announcedKey !== "" && name === root.playerName;
            if (key !== "" && key !== root.announcedKey && known && root.playing)
                root.trackChanged();
            if (key !== "") {
                root.announcedKey = key;
                root.playerName = name;
            }
        }
    }
}
