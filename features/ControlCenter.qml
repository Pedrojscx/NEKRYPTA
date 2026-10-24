import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Bluetooth
import Quickshell.Io
import "../core"
import "../core/paths.js" as Paths
import "../core/controlItems.js" as ControlItems
import "../services"
import "../components" as C
import "../core/i18n.js" as I18n

FocusScope {
    id: root
    readonly property string title: ({network: "Rede", bluetooth: "Bluetooth", audio: I18n.t("Sound"), power: I18n.t("Battery & power"), media: I18n.t("Now playing"), tray: I18n.t("System tray")})[ShellState.detail] || I18n.t("Control center")
    readonly property bool canGoBack: ShellState.detail !== ""
    // The controlLayout preference (config.json) orders the tiles and buttons and turns parts off.
    readonly property var arrangement: ControlItems.layout(ShellState.setting("controlLayout", {}))
    readonly property var shownTiles: arrangement.tiles.filter(tile => tile.shown
                                                                        && (tile.id !== "lid" || Backend.capabilities.lid !== false)).map(tile => tile.id)
    // What a tile shows. Wi-Fi and Bluetooth also open their page; the others are plain switches.
    function tile(id) {
        // Rede: the body switches Wi-Fi (wifi.power); the arrow opens the network page, like Bluetooth's.
        if (id === "wifi") {
            const state = ShellState.networkState;
            const wifi = ShellState.wifi;
            const current = (wifi.networks || []).find(n => n.connected);
            const ssid = wifi.ssid || (current ? current.ssid || current.name || "" : "");
            const subtitle = root.pendingMethod === "wifi.power" ? "Atualizando…"
                : ({wifi_desativado: "Desligado", procurando: "Procurando redes", desconectado: "Desconectado", conectando: "Conectando…",
                    conectado: ssid ? "Conectado · " + ssid : "Conectado", sem_internet: "Sem internet", cabo: "Cabo",
                    nm_indisponivel: "Indisponível", erro: "Erro"})[state] || "Verificando…";
            return {text: "Rede", glyph: state === "cabo" ? "link" : wifi.powered === false || state === "nm_indisponivel" ? "wifi-off" : "wifi",
                    selected: wifi.powered === true, subtitle: subtitle,
                    hint: wifi.powered ? "Desligar o Wi‑Fi" : "Ligar o Wi‑Fi", detail: "network", detailHint: "Detalhes da rede",
                    enabled: !root.acting && typeof wifi.powered === "boolean" && state !== "nm_indisponivel"};
        }
        if (id === "bluetooth") {
            const count = Services.bluetoothDevices.filter(d => d.connected).length;
            return {text: "Bluetooth", glyph: "bluetooth", selected: Services.bluetoothEnabled,
                    subtitle: !Services.bluetoothAvailable ? I18n.t("Unavailable") : count ? count + I18n.t(" connected") : Services.bluetoothEnabled ? I18n.t("On") : I18n.t("Off"),
                    hint: Services.bluetoothEnabled ? I18n.t("Turn Bluetooth off") : I18n.t("Turn Bluetooth on"), detail: "bluetooth", detailHint: I18n.t("Bluetooth devices"),
                    enabled: Services.bluetoothAvailable};
        }
        const toggle = ({
                dnd: {text: I18n.t("Do not disturb"), glyph: "bell-off", on: ShellState.setting("dnd", false), subtitle: I18n.t("Popups hidden"), busy: false},
                awake: {text: I18n.t("Stay awake"), glyph: "moon", on: ShellState.presentation, subtitle: I18n.t("Screen stays on"), busy: false},
                grayscale: {text: I18n.t("Grayscale"), glyph: "palette", on: ShellState.grayscale, subtitle: I18n.t("Colors off"), busy: ShellState.grayscaleBusy},
                lid: {text: I18n.t("Lid closed"), glyph: "laptop", on: ShellState.lidAwake, subtitle: I18n.t("Stays awake"), busy: ShellState.lidBusy}
            })[id];
        return {text: toggle.text, glyph: toggle.glyph, selected: toggle.on, subtitle: toggle.on ? toggle.subtitle : I18n.t("Off"), hint: "",
                detail: "", detailHint: "", enabled: !toggle.busy, switch: true};
    }
    function activateTile(id) {
        if (id === "wifi")
            root.perform("wifi.power", {powered: !ShellState.wifi.powered}, () => ShellState.refreshWifi());
        else if (id === "bluetooth")
            Services.bluetoothPower(!Services.bluetoothEnabled);
        else if (id === "dnd")
            ShellState.setSetting("dnd", !ShellState.setting("dnd", false));
        else if (id === "awake")
            ShellState.presentation = !ShellState.presentation;
        else if (id === "grayscale")
            ShellState.toggleGrayscale();
        else if (id === "lid")
            ShellState.toggleLidAwake();
    }
    function back() {
        ShellState.detail = "";
    }
    // Balanced and Performance only: the power saver mode is not offered.
    readonly property var powerModes: (ShellState.profiles.profiles || []).filter(mode => mode !== "power-saver")
    // The sound page is wider (min 420, preferred 520, max 560) so names and 180+ px sliders share a row; it
    // still fits a 1366 px screen.
    readonly property int panelWidth: ShellState.detail === "audio" ? Math.max(420, Math.min(560, 520)) : 420
    // Fits the page, header and padding included, within a 620 px ceiling.
    readonly property int panelHeight: preferredContentHeight > 0 ? Math.max(200, Math.min(620, preferredContentHeight + 88)) : 620
    readonly property Component headerTools: ShellState.detail === "power" ? refreshTool : null
    Component {
        id: refreshTool
        C.Button {
            glyph: "refresh"
            flat: true
            hint: I18n.t("Refresh")
            implicitWidth: Theme.controlHeightCompact
            implicitHeight: Theme.controlHeightCompact
            enabled: !root.busy
            onClicked: root.refresh(true)
        }
    }
    readonly property int preferredContentHeight: Math.ceil(controlContent.implicitHeight + (errorLabel.visible ? errorLabel.implicitHeight + 12 : 0) + (actionFooter.visible ? actionFooter.implicitHeight + 12 : 0))
    property string error: ""
    property bool busy: false
    // Busy with something the user asked for; background list refreshes don't dim the page.
    readonly property bool acting: busy && ["power.profiles"].indexOf(pendingMethod) < 0
    property string pendingMethod: ""
    property int requestGeneration: 0
    property bool alive: true
    TrayMenu {
        id: trayMenu
    }
    // Connected first, then paired, then nearby; nameless discoveries stay folded away.
    property bool showAllDevices: false
    function unnamed(device) {
        const name = device.name || "";
        return !name || /^([0-9a-f]{2}[-:]){5}[0-9a-f]{2}$/i.test(name);
    }
    readonly property var sortedDevices: {
        const rank = device => device.connected ? 0 : device.paired ? 1 : unnamed(device) ? 3 : 2;
        return Services.bluetoothDevices.slice().sort((a, b) => rank(a) - rank(b) || (a.name || "").localeCompare(b.name || ""));
    }
    readonly property int hiddenDevices: sortedDevices.filter(device => !device.paired && !device.connected && unnamed(device)).length
    readonly property var shownDevices: showAllDevices ? sortedDevices : sortedDevices.filter(device => device.paired || device.connected || !unnamed(device))
    readonly property var pairedDevices: sortedDevices.filter(device => device.paired)
    readonly property var nearbyDevices: shownDevices.filter(device => !device.paired)
    readonly property var autoConnectList: ShellState.setting("bluetoothAutoConnect", [])
    property var pairing: null
    // The Wi-Fi network waiting for a confirmation to be forgotten.
    property string forgetPath: ""
    // The device whose options are open, and the connects and disconnects the shell has under way.
    property string expandedDevice: ""
    property var bluetoothWork: ({})
    function setWork(path, kind) {
        const next = Object.assign({}, bluetoothWork);
        if (kind)
            next[path] = kind;
        else
            delete next[path];
        bluetoothWork = next;
    }
    // Tapping a device: connect or disconnect it through the helper (so auto-connect knows), or pair a new one.
    function bluetoothActivate(device) {
        const path = device.dbusPath;
        if (bluetoothWork[path] || !Services.bluetoothEnabled)
            return;
        if (!device.paired) {
            Services.bluetoothScan(false);
            perform("bluetooth.pair", {path: path}, () => {});
            return;
        }
        const kind = device.connected ? "disconnect" : "connect";
        setWork(path, kind);
        error = "";
        Backend.request("bluetooth." + kind, {path: path}, (r, e) => {
            if (!root.alive)
                return;
            root.setWork(path, "");
            if (e && e.code !== "cancelled")
                root.error = I18n.error(e);
        });
    }
    function toggleAutoConnect(device) {
        const address = String(device.address).toUpperCase();
        const list = autoConnectList.slice();
        const at = list.indexOf(address);
        if (at >= 0)
            list.splice(at, 1);
        else
            list.push(address);
        ShellState.setSetting("bluetoothAutoConnect", list);
    }
    property real pageProgress: 1
    NumberAnimation {
        id: pageEntrance
        target: root
        property: "pageProgress"
        from: 0
        to: 1
        duration: Theme.enter
        easing.type: Easing.OutCubic
    }
    function perform(method, params, callback) {
        const generation = ++requestGeneration;
        const page = ShellState.detail;
        busy = true;
        pendingMethod = method;
        error = "";
        Backend.request(method, params, (r, e) => {
            if (!root.alive || generation !== root.requestGeneration || page !== ShellState.detail)
                return;
            busy = false;
            pendingMethod = "";
            if (e) {
                if (method !== "power.profiles")
                    error = I18n.error(e);
            }
            else if (callback)
                callback(r);
        });
    }
    function refresh() {
        if (busy)
            return;
        if (ShellState.detail === "power")
            perform("power.profiles", {}, r => ShellState.profiles = r);
    }
    Component.onCompleted: {
        refresh();
        ShellState.run("display.lid.get", {}, r => ShellState.lidAwake = r.enabled);
        ShellState.run("display.grayscale.get", {}, r => ShellState.grayscale = r.enabled);
        ShellState.refreshWifi();
        ShellState.run("brightness.get", {}, r => ShellState.brightness = r.available ? r.value : -1);
    }
    Component.onDestruction: {
        alive = false;
        requestGeneration++;
        Services.bluetoothScan(false);
        Backend.request("bluetooth.reply", {
            accept: false
        });
    }
    Connections {
        target: ShellState
        function onDetailChanged() {
            pageEntrance.restart();
            root.requestGeneration++;
            root.busy = false;
            root.pendingMethod = "";
            root.error = "";
            root.forgetPath = "";
            root.expandedDevice = "";
            root.showAllDevices = false;
            scanWait.stop();
            if (root.pairing && ShellState.detail !== "bluetooth") {
                Backend.request("bluetooth.reply", { accept: false });
                root.pairing = null;
                pin.clear();
            }
            root.refresh();
            if (ShellState.detail !== "bluetooth")
                Services.bluetoothScan(false);
        }
    }
    Connections {
        target: Backend
        function onReadyChanged() {
            if (Backend.ready) root.refresh();
        }
        function onEventReceived(name, data) {
            if (name === "bluetooth.prompt") {
                if (ShellState.detail === "bluetooth")
                    root.pairing = data;
                else
                    Backend.request("bluetooth.reply", { accept: false });
            }
        }
    }
    ColumnLayout {
        anchors.fill: parent
        spacing: Theme.gapM
        C.Label {
            id: errorLabel
            visible: root.error !== ""
            text: root.error
            color: Theme.danger
            font.pixelSize: Theme.small
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
        ScrollView {
            id: controlScroll
            ScrollBar.vertical: C.ScrollBar {
                parent: controlScroll
                x: controlScroll.width - width
                y: controlScroll.topPadding
                height: controlScroll.availableHeight
            }
            opacity: root.pageProgress
            transform: Translate {
                x: Theme.reduced ? 0 : (1 - root.pageProgress) * 6
            }
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumHeight: 0
            Layout.preferredHeight: 0
            contentWidth: availableWidth
            clip: true
            ColumnLayout {
                id: controlContent
                width: controlScroll.availableWidth
                spacing: Theme.gapM
                ColumnLayout {
                    visible: ShellState.detail === ""
                    Layout.fillWidth: true
                    spacing: Theme.gapS
                    // Every tile here is an on/off switch; pages open from chevrons and the power row below.
                    GridLayout {
                        Layout.fillWidth: true
                        columns: 2
                        rowSpacing: Theme.gapS
                        columnSpacing: Theme.gapS
                        Repeater {
                            model: root.shownTiles
                            C.QuickTile {
                                id: quickTile
                                required property string modelData
                                readonly property var info: root.tile(modelData)
                                Layout.fillWidth: true
                                Layout.fillHeight: true
                                Layout.preferredWidth: 1
                                Layout.preferredHeight: info.switch ? Math.max(58, Theme.body * 4) : Math.max(66, Theme.body * 4.6)
                                text: info.text
                                glyph: info.glyph
                                selected: info.selected
                                subtitle: info.subtitle
                                hint: info.hint
                                detailHint: info.detailHint
                                enabled: info.enabled
                                Accessible.role: info.switch ? Accessible.CheckBox : Accessible.Button
                                Accessible.checkable: !!info.switch
                                Accessible.checked: !!info.switch && selected
                                onClicked: root.activateTile(modelData)
                                onDetailRequested: ShellState.detail = quickTile.info.detail
                            }
                        }
                    }
                    // Security Mode: the switch, its state and, folded, what the backend reported.
                    SecurityBlock {}
                    // A focus session: popups held, a ring in the bar, then one digest of what came in.
                    C.ListRow {
                        visible: root.arrangement.sections.focus
                        Layout.fillWidth: true
                        text: I18n.t("Focus")
                        glyph: "focus"
                        selected: Focus.active
                        checkmark: false
                        subtitle: Focus.active ? Focus.minutesText(Focus.remainingSeconds) + I18n.t(" left · Notifications held") : I18n.t("Holds notifications")
                        hint: Focus.active ? I18n.t("End focus") : I18n.t("Focus for ") + Focus.lastMinutes + I18n.t(" minutes")
                        Accessible.role: Accessible.CheckBox
                        Accessible.checkable: true
                        Accessible.checked: Focus.active
                        onClicked: Focus.active ? Focus.end(true) : Focus.start(Focus.lastMinutes)
                        trailing: [
                            C.Segmented {
                                visible: !Focus.active
                                fill: false
                                segmentHeight: Theme.controlHeightLarge
                                model: [{key: 25, text: I18n.t("25 min"), hint: I18n.t("Focus for 25 minutes")}, {key: 50, text: I18n.t("50 min"), hint: I18n.t("Focus for 50 minutes")}, {key: 90, text: I18n.t("90 min"), hint: I18n.t("Focus for 90 minutes")}]
                                current: Focus.lastMinutes
                                onActivated: key => Focus.start(key)
                            },
                            C.Button {
                                visible: Focus.active
                                text: I18n.t("+5 min")
                                implicitHeight: Theme.controlHeightCompact
                                onClicked: Focus.extend(5)
                            }
                        ]
                    }
                    C.Card {
                        visible: root.arrangement.sections.levels
                        Layout.fillWidth: true
                        implicitHeight: levels.implicitHeight + 2 * Theme.gapM
                        ColumnLayout {
                            id: levels
                            anchors.fill: parent
                            anchors.margins: Theme.gapM
                            spacing: Theme.gapS
                            C.SliderRow {
                                Layout.fillWidth: true
                                title: I18n.t("Volume")
                                muteable: true
                                muted: Services.muted
                                onIconClicked: Services.toggleMute()
                                value: Services.volume
                                maximum: ShellState.setting("amplification", false) ? 1.5 : 1
                                enabled: Services.audioAvailable
                                detailHint: I18n.t("Sound settings")
                                onDetailRequested: ShellState.detail = "audio"
                                onMoved: value => Services.setVolume(value)
                                scrollable: true
                                onScrolled: steps => Services.stepVolume(steps)
                            }
                            C.SliderRow {
                                Layout.fillWidth: true
                                title: I18n.t("Brightness")
                                glyph: "sun"
                                // Desktops have no backlight; setting it needs brightnessctl.
                                visible: ShellState.brightness >= 0 && Backend.has("brightnessctl", true)
                                value: Math.max(0, ShellState.brightness)
                                onMoved: value => {
                                    ShellState.brightness = value;
                                    brightnessDebounce.restart();
                                }
                            }
                        }
                    }
                    C.ListRow {
                        visible: root.arrangement.sections.media
                        Layout.fillWidth: true
                        text: Services.player ? (Services.player.trackTitle || I18n.t("Now playing")) : I18n.t("Nothing playing")
                        subtitle: Services.player ? (Services.player.trackArtist || Services.player.identity) : I18n.t("Media")
                        glyph: "music"
                        hint: I18n.t("Media")
                        onClicked: ShellState.detail = "media"
                        trailing: C.Button {
                            implicitWidth: 36
                            implicitHeight: Theme.controlHeight
                            glyph: Services.player && Services.player.isPlaying ? "pause" : "play"
                            hint: Services.player && Services.player.isPlaying ? I18n.t("Pause") : I18n.t("Play")
                            accent: true
                            enabled: !!Services.player && Services.player.canTogglePlaying
                            onClicked: Services.player.togglePlaying()
                        }
                    }
                    C.ListRow {
                        readonly property string mode: ({"balanced": I18n.t("Balanced"), "performance": I18n.t("Performance")})[ShellState.profiles.active] || ""
                        Layout.fillWidth: true
                        text: Services.batteryAvailable ? I18n.t("Battery & power") : I18n.t("Power")
                        subtitle: [Services.batteryAvailable ? Math.round(Services.batteryPercent) + "%" + (Services.batteryCharging ? I18n.t(" · Charging") : "") : "", mode].filter(part => part).join(" · ") || I18n.t("Power modes")
                        glyph: Services.batteryCharging ? "bolt" : Services.batteryAvailable ? "battery" : "gauge"
                        hint: I18n.t("Battery and power modes")
                        onClicked: ShellState.detail = "power"
                        trailing: C.Icon { name: "chevron"; color: Theme.muted; Layout.rightMargin: 4 }
                    }
                }
                ColumnLayout {
                    visible: ShellState.detail === "audio"
                    Layout.fillWidth: true
                    spacing: Theme.gapS
                    C.StatusCard {
                        visible: !Services.audioAvailable
                        Layout.fillWidth: true
                        glyph: "volume"
                        title: I18n.t("Audio unavailable")
                        subtitle: I18n.t("No output device or audio service")
                    }
                    C.Card {
                        visible: Services.audioAvailable
                        Layout.fillWidth: true
                        implicitHeight: audioLevels.implicitHeight + 2 * Theme.gapM
                        ColumnLayout {
                            id: audioLevels
                            anchors.fill: parent
                            anchors.margins: Theme.gapM
                            spacing: Theme.gapS
                            C.SliderRow {
                                Layout.fillWidth: true
                                title: I18n.t("Output")
                                inline: true
                                muteable: true
                                muted: Services.muted
                                onIconClicked: Services.toggleMute()
                                value: Services.volume
                                maximum: ShellState.setting("amplification", false) ? 1.5 : 1
                                onMoved: value => Services.setVolume(value)
                                scrollable: true
                                onScrolled: steps => Services.stepVolume(steps)
                            }
                            C.SliderRow {
                                Layout.fillWidth: true
                                title: I18n.t("Microphone")
                                inline: true
                                enabled: !!Services.input
                                glyph: "mic"
                                mutedGlyph: "mic"
                                muteable: true
                                muted: Services.micMuted
                                onIconClicked: Services.toggleMicMute()
                                value: Services.micVolume
                                onMoved: value => Services.setMicVolume(value)
                            }
                        }
                    }
                    C.SectionHeader {
                        visible: Services.audioAvailable
                        text: I18n.t("Output device")
                        Layout.topMargin: Theme.gapXs
                    }
                    Repeater {
                        model: Services.audioAvailable ? Services.audioOutputs : []
                        C.ListRow {
                            required property var modelData
                            readonly property string name: modelData.description || modelData.name
                            Layout.fillWidth: true
                            text: name
                            glyph: /head(phone|set)|earbud|earphone|airpods|buds/i.test(name) ? "headphones" : "speaker"
                            // Device rows are 48 px: the list reads as a compact picker, not as cards.
                            implicitHeight: 48
                            topPadding: 4
                            bottomPadding: 4
                            selected: modelData === Services.output
                            onClicked: Services.setOutput(modelData)
                        }
                    }
                    C.SectionHeader {
                        visible: Services.audioAvailable
                        text: I18n.t("Input device")
                        Layout.topMargin: Theme.gapXs
                    }
                    C.Label {
                        visible: Services.audioAvailable && Services.audioInputs.length === 0
                        text: I18n.t("No microphone connected")
                        color: Theme.muted
                        Layout.fillWidth: true
                    }
                    Repeater {
                        model: Services.audioAvailable ? Services.audioInputs : []
                        C.ListRow {
                            required property var modelData
                            Layout.fillWidth: true
                            text: modelData.description || modelData.name
                            glyph: "mic"
                            selected: modelData === Services.input
                            // Device rows are 48 px: the list reads as a compact picker, not as cards.
                            implicitHeight: 48
                            topPadding: 4
                            bottomPadding: 4
                            onClicked: Services.setInput(modelData)
                        }
                    }
                    C.SectionHeader {
                        visible: Services.audioAvailable
                        text: I18n.t("Applications")
                        Layout.topMargin: Theme.gapXs
                    }
                    C.Label {
                        visible: Services.audioAvailable && Services.audioStreams.length === 0
                        text: I18n.t("No apps are playing audio")
                        color: Theme.muted
                        Layout.fillWidth: true
                    }
                    C.Card {
                        visible: Services.audioAvailable && Services.audioStreams.length > 0
                        Layout.fillWidth: true
                        implicitHeight: streamLevels.implicitHeight + 2 * Theme.gapM
                        ColumnLayout {
                            id: streamLevels
                            anchors.fill: parent
                            anchors.margins: Theme.gapM
                            spacing: Theme.gapS
                            Repeater {
                                model: Services.audioAvailable ? Services.audioStreams : []
                                C.SliderRow {
                                    required property var modelData
                                    Layout.fillWidth: true
                                    title: modelData.properties && modelData.properties["application.name"] || modelData.description || modelData.name
                                    inline: true
                                    muteable: !!modelData.audio
                                    muted: !!modelData.audio && modelData.audio.muted
                                    onIconClicked: if (modelData.audio) modelData.audio.muted = !modelData.audio.muted
                                    value: modelData.audio ? modelData.audio.volume : 0
                                    onMoved: value => Services.setStreamVolume(modelData, value)
                                }
                            }
                        }
                    }
                }
                // The network page (from the Rede tile's arrow); loaded only while shown, so its polling stops.
                Loader {
                    active: ShellState.detail === "network"
                    visible: active
                    Layout.fillWidth: true
                    sourceComponent: NetworkPage {}
                }
                ColumnLayout {
                    visible: ShellState.detail === "bluetooth"
                    Layout.fillWidth: true
                    spacing: Theme.gapS
                    C.Toggle {
                        Layout.fillWidth: true
                        text: "Bluetooth"
                        description: {
                            const count = Services.bluetoothDevices.filter(d => d.connected).length;
                            return !Services.bluetoothAvailable ? I18n.t("No adapter detected") : count ? count + (count === 1 ? I18n.t(" device connected") : I18n.t(" devices connected")) : Services.bluetoothScanning ? I18n.t("Searching nearby") : Services.bluetoothEnabled ? I18n.t("On") : I18n.t("Off");
                        }
                        positive: Services.bluetoothDevices.some(d => d.connected)
                        selected: Services.bluetoothEnabled
                        glyph: "bluetooth"
                        enabled: Services.bluetoothAvailable
                        onClicked: Services.bluetoothPower(!Services.bluetoothEnabled)
                    }
                    C.Card {
                        visible: !!root.pairing
                        Layout.fillWidth: true
                        implicitHeight: pairingContent.implicitHeight + 2 * Theme.gapM
                        ColumnLayout {
                            id: pairingContent
                            anchors.fill: parent
                            anchors.margins: Theme.gapM
                            spacing: Theme.gapS
                            C.Label {
                                text: root.pairing ? root.pairing.message : ""
                                Layout.fillWidth: true
                                wrapMode: Text.WordWrap
                            }
                            C.Field {
                                id: pin
                                Layout.fillWidth: true
                                placeholderText: I18n.t("PIN or passkey")
                                visible: root.pairing && root.pairing.input
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: Theme.gapS
                                C.Button {
                                    text: I18n.t("Reject")
                                    onClicked: {
                                        ShellState.run("bluetooth.reply", {
                                            accept: false
                                        });
                                        root.pairing = null;
                                    }
                                }
                                C.Button {
                                    text: I18n.t("Accept")
                                    accent: true
                                    Layout.fillWidth: true
                                    enabled: !!root.pairing && (!root.pairing.input || pin.text.trim().length > 0)
                                    onClicked: {
                                        ShellState.run("bluetooth.reply", {
                                            accept: true,
                                            value: pin.text
                                        });
                                        pin.clear();
                                        root.pairing = null;
                                    }
                                }
                            }
                        }
                    }
                    // Paired devices, connected first.
                    C.SectionHeader {
                        visible: Services.bluetoothEnabled && root.pairedDevices.length > 0
                        text: I18n.t("My devices")
                        Layout.topMargin: Theme.gapXs
                    }
                    Repeater {
                        model: Services.bluetoothEnabled ? root.pairedDevices : []
                        BluetoothDeviceRow {
                            required property var modelData
                            Layout.fillWidth: true
                            device: modelData
                            working: root.bluetoothWork[modelData.dbusPath] || ""
                            autoConnect: root.autoConnectList.indexOf(String(modelData.address).toUpperCase()) >= 0
                            expanded: root.expandedDevice === modelData.dbusPath
                            locked: root.acting
                            onActivated: root.bluetoothActivate(modelData)
                            onOptionsRequested: root.expandedDevice = expanded ? "" : modelData.dbusPath
                            onAutoConnectToggled: root.toggleAutoConnect(modelData)
                            onForgetRequested: {
                                root.expandedDevice = "";
                                modelData.forget();
                            }
                        }
                    }
                    // Devices found nearby, to pair.
                    C.SectionHeader {
                        visible: Services.bluetoothEnabled
                        text: Services.bluetoothScanning ? I18n.t("Nearby · searching…") : I18n.t("Nearby")
                        Layout.topMargin: Theme.gapXs
                        C.Button {
                            text: Services.bluetoothScanning ? I18n.t("Stop") : I18n.t("Scan")
                            flat: true
                            implicitHeight: Theme.controlHeightCompact
                            onClicked: Services.bluetoothScan(!Services.bluetoothScanning)
                        }
                    }
                    C.EmptyState {
                        visible: Services.bluetoothEnabled && root.nearbyDevices.length === 0 && root.pairedDevices.length === 0
                        Layout.fillWidth: true
                        Layout.topMargin: Theme.gapL
                        Layout.bottomMargin: Theme.gapL
                        glyph: "bluetooth"
                        title: Services.bluetoothScanning ? I18n.t("Looking for devices…") : I18n.t("No devices yet")
                        description: Services.bluetoothScanning ? I18n.t("Put your device in pairing mode") : I18n.t("Scan to find nearby devices")
                    }
                    C.Caption {
                        visible: Services.bluetoothEnabled && root.nearbyDevices.length === 0 && root.pairedDevices.length > 0
                        text: Services.bluetoothScanning ? I18n.t("Looking for devices in pairing mode…") : I18n.t("Scan to add a new device")
                        Layout.fillWidth: true
                        Layout.leftMargin: Theme.gapXs
                    }
                    Repeater {
                        model: Services.bluetoothEnabled ? root.nearbyDevices : []
                        BluetoothDeviceRow {
                            required property var modelData
                            Layout.fillWidth: true
                            device: modelData
                            locked: root.acting
                            onActivated: root.bluetoothActivate(modelData)
                        }
                    }
                    C.Button {
                        visible: Services.bluetoothEnabled && root.hiddenDevices > 0 && !root.showAllDevices
                        text: I18n.t("Show ") + root.hiddenDevices + (root.hiddenDevices === 1 ? I18n.t(" unnamed device") : I18n.t(" unnamed devices"))
                        flat: true
                        Layout.fillWidth: true
                        onClicked: root.showAllDevices = true
                    }
                }
                ColumnLayout {
                    visible: ShellState.detail === "power"
                    Layout.fillWidth: true
                    spacing: Theme.gapS
                    C.StatusCard {
                        Layout.fillWidth: true
                        glyph: Services.batteryCharging ? "bolt" : Services.batteryAvailable ? "battery" : "gauge"
                        title: Services.batteryAvailable ? Math.round(Services.batteryPercent) + "%" : I18n.t("AC power")
                        subtitle: {
                            const state = Services.batteryCharging ? I18n.t("Charging") : Services.onBattery ? I18n.t("On battery") : I18n.t("Plugged in");
                            const seconds = Services.batterySeconds;
                            return state + (seconds > 0 ? " · " + (seconds >= 3600 ? Math.floor(seconds / 3600) + "h " : "") + Math.floor(seconds % 3600 / 60) + "m " + (Services.batteryCharging ? I18n.t("until full") : "remaining") : "");
                        }
                        level: Services.batteryAvailable ? Services.batteryPercent / 100 : -1
                        positive: Services.batteryCharging
                    }
                    C.SectionHeader {
                        text: I18n.t("Power mode")
                        Layout.topMargin: Theme.gapXs
                    }
                    C.Label {
                        visible: !root.powerModes.length
                        text: root.busy ? I18n.t("Loading power modes…") : I18n.t("Power modes are unavailable on this device.")
                        color: Theme.muted
                        wrapMode: Text.WordWrap
                        Layout.fillWidth: true
                    }
                    Repeater {
                        model: root.powerModes
                        C.ListRow {
                            required property string modelData
                            Layout.fillWidth: true
                            text: ({"balanced": I18n.t("Balanced"), "performance": I18n.t("Performance")})[modelData] || modelData
                            subtitle: ({"balanced": I18n.t("Fast when needed, efficient at idle"), "performance": I18n.t("Full CPU and GPU power")})[modelData] || I18n.t("System power profile")
                            glyph: modelData === "performance" ? "bolt" : "gauge"
                            selected: ShellState.profiles.active === modelData
                            enabled: !root.acting
                            Accessible.role: Accessible.RadioButton
                            Accessible.checkable: true
                            Accessible.checked: selected
                            onClicked: root.perform("power.set", {
                                profile: modelData
                            }, r => ShellState.profiles = r)
                        }
                    }
                }
                Media {
                    visible: ShellState.detail === "media"
                    Layout.fillWidth: true
                    active: visible
                }
                ColumnLayout {
                    visible: ShellState.detail === "tray"
                    Layout.fillWidth: true
                    spacing: Theme.gapS
                    C.EmptyState {
                        visible: Services.trayItems.length === 0
                        Layout.fillWidth: true
                        Layout.topMargin: Theme.gapXl
                        glyph: "grid"
                        title: I18n.t("No background apps")
                        description: I18n.t("App indicators appear here")
                    }
                    Repeater {
                        model: Services.trayItems
                        C.ListRow {
                            id: trayRow
                            required property var modelData
                            Layout.fillWidth: true
                            image: modelData.icon
                            text: modelData.tooltipTitle || modelData.title || modelData.id
                            hint: I18n.t("Open ") + text
                            onClicked: modelData.onlyMenu ? trayMenuButton.openMenu() : modelData.activate()
                            trailing: C.Button {
                                id: trayMenuButton
                                glyph: "more"
                                flat: true
                                hint: I18n.t("Menu for ") + (trayRow.modelData.title || trayRow.modelData.id)
                                implicitWidth: Theme.controlHeightCompact
                                implicitHeight: Theme.controlHeightCompact
                                selected: trayMenu.shown && trayMenu.item === trayRow.modelData
                                function openMenu() {
                                    trayMenu.toggle(trayRow.modelData, trayMenuButton.QsWindow.window, trayMenuButton);
                                }
                                onClicked: openMenu()
                            }
                        }
                    }
                }
                Item {
                    Layout.preferredHeight: 4
                }
            }
        }
        ColumnLayout {
            id: actionFooter
            visible: ShellState.detail === ""
            Layout.fillWidth: true
            spacing: Theme.gapXs
            Rectangle {
                Layout.fillWidth: true
                implicitHeight: 1
                color: Theme.surface
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.gapS
                Repeater {
                    model: root.arrangement.footer.filter(item => item.shown).map(item => ({
                            capture: { title: I18n.t("Capture"), glyph: "camera", action: "capture" },
                            lock: { title: I18n.t("Lock"), glyph: "lock", action: "lock" },
                            session: { title: I18n.t("Session"), glyph: "power", action: "power" }
                        })[item.id]).filter(item => !!item)
                    C.Button {
                        required property var modelData
                        Layout.fillWidth: true
                        Layout.preferredWidth: 1
                        implicitHeight: Math.max(58, Theme.body * 3.8)
                        flat: true
                        hint: modelData.title
                        padding: 3
                        contentItem: Item {
                            implicitWidth: 72
                            implicitHeight: Math.max(46, Theme.body * 3)
                            C.Icon {
                                name: modelData.glyph
                                size: Theme.iconSizeCompact
                                anchors.horizontalCenter: parent.horizontalCenter
                                anchors.top: parent.top
                                anchors.topMargin: 3
                            }
                            C.Label {
                                text: modelData.title
                                font.pixelSize: Theme.caption
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.bottom: parent.bottom
                                horizontalAlignment: Text.AlignHCenter
                                elide: Text.ElideRight
                            }
                        }
                        onClicked: modelData.action === "lock" ? ShellState.action("lock") : ShellState.open(modelData.action)
                    }
                }
            }
        }
    }
    Timer {
        id: scanWait
        interval: 1500
        onTriggered: root.refresh()
    }
    Timer {
        id: brightnessDebounce
        interval: 60
        onTriggered: ShellState.run("brightness.set", {
            value: ShellState.brightness
        }, r => ShellState.osd(I18n.t("Brightness"), r.value, "sun"))
    }
}
