import QtQuick
import QtQuick.Layouts
import QtQuick.Shapes
import QtQuick.Controls as QQC
import Quickshell
import "../core"
import "../core/barItems.js" as BarItems
import "../core/i18n.js" as I18n
import "../services"
import "../components" as C

// The bar's items in their zones. Bar.qml puts it in a layer-shell window; previews put it in a plain one.
Item {
    id: bar
    component BarButton: C.Button {
        // Whether the item has something to show; the zone hides its slot otherwise.
        property bool shown: true
        labelSize: Theme.barText
        glyphSize: Theme.barIconSize
        // Drawn compact (34 px, or the bar's height if that is less) but never narrower than a 40 px target.
        implicitHeight: Math.min(Theme.barControlHeight, bar.height)
        implicitWidth: Math.max(Theme.barHitTarget, contentItem.implicitWidth + leftPadding + rightPadding)
    }
    required property string outputName
    // The window it is in, the tooltip its buttons share and the tray menu, which open next to it.
    property var window: null
    property var tooltip: null
    property var trayMenu: null
    // False while the bar is hidden (fullscreen, lock): live visuals pause.
    property bool onScreen: true
    readonly property bool compact: width < 760
    // Physical centre: the centre group sits on the bar's horizontal centre, and each side may use at most half
    // the bar minus half the centre group minus a margin. A side that doesn't fit gives up room in steps:
    // 1 secondary items compact (tray icons into the tray button, counters and percentages off),
    // 2 CPU/RAM compact, 3 media narrower (its title elides more), 4 explicit compact mode (media without title).
    // The left side drops the window title first. Date and time never shrink.
    readonly property real sideMargin: 12
    readonly property real sideRoom: Math.max(0, width / 2 - (zones.itemAt(1) ? zones.itemAt(1).width : 0) / 2 - sideMargin - 5)
    property int squeeze: 0
    property int leftSqueeze: 0
    readonly property bool secondaryCompact: compact || squeeze >= 1
    // The side's width when each step was taken: a step is undone only once that much room is back, so the bar
    // never flips between two steps.
    property var squeezedAt: []
    property var leftSqueezedAt: []
    function relieve() {
        const left = zones.itemAt(0), right = zones.itemAt(2);
        if (!left || !right)
            return;
        if (right.implicitWidth > sideRoom && squeeze < 4) {
            squeezedAt = squeezedAt.slice(0, squeeze).concat([right.implicitWidth]);
            squeeze++;
        } else if (squeeze > 0 && squeezedAt[squeeze - 1] <= sideRoom) {
            squeeze--;
        }
        if (left.implicitWidth > sideRoom && leftSqueeze < 1) {
            leftSqueezedAt = [left.implicitWidth];
            leftSqueeze = 1;
        } else if (leftSqueeze > 0 && leftSqueezedAt[0] <= sideRoom) {
            leftSqueeze = 0;
        }
    }
    onSideRoomChanged: relief.restart()
    property Timer relief: Timer {
        interval: 60
        onTriggered: bar.relieve()
    }
    // Recording, sharing, focus, timers and stay-awake take room on the right; below 1700 px, media and the
    // temperature give it up first so status icons never get pushed.
    readonly property bool crowded: width < 1700 && (ShellState.recording.active || Services.micUsers.length > 0 || Services.screenSharers.length > 0
                                                     || Focus.active || !!Focus.nextTimer || ShellState.presentation)
    readonly property bool edge: ShellState.setting("barStyle", "floating") === "edge"
    // The barLayout preference (config.json) arranges the items; anything it doesn't mention keeps its default place.
    readonly property var layout: BarItems.layout(ShellState.setting("barLayout", {}))
    readonly property var items: ({
            launcher: launcherItem, workspaces: workspacesItem, windowTitle: windowTitleItem, clock: clockItem, temperature: temperatureItem,
            media: mediaItem, tray: trayItem, recording: recordingItem, privacy: privacyItem, focus: focusItem, timer: timerItem,
            stayAwake: stayAwakeItem, keyboard: keyboardItem, wifi: wifiItem, audio: audioItem, bluetooth: bluetoothItem,
            battery: batteryItem, notifications: notificationsItem, control: controlItem,
            system: systemItem
        })
    // The now-playing button while it exists, for the hover card.
    property Item mediaButton: null
    property bool mediaHovered: false
    readonly property var monitor: Services.workspaces.find(w => w.monitor && w.monitor.name === bar.outputName && w.active)
    function controlActive(detail) {
        return ShellState.panel === "control" && ShellState.detail === detail && ShellState.screenName === outputName;
    }
    function toggleControl(detail) {
        if (controlActive(detail)) {
            ShellState.close();
        } else {
            ShellState.open("control", bar.outputName);
            ShellState.detail = detail;
        }
    }
    SystemClock {
        id: clock
        precision: ShellState.setting("clockSeconds", false) ? SystemClock.Seconds : SystemClock.Minutes
    }
    // One quiet rail gives the independent controls a shared visual baseline.
    Rectangle {
        id: rail
        anchors.fill: parent
        radius: bar.edge ? 0 : Theme.radiusSmall + 2
        color: Theme.panel
    }
    // "reflexo-azul" ambient: a faint wallpaper-toned reflection drifting slowly across the rail. Fixed tokens,
    // never read from the image per frame; low enough alpha that text contrast holds. Still under reduced motion,
    // hidden while the bar is off screen, and paused in power saving.
    readonly property bool ambient: ShellState.setting("barAmbient", true) === true && ShellState.setting("barAmbientStyle", "reflexo-azul") === "reflexo-azul"
    readonly property bool ambientMoving: ambient && onScreen && !Theme.reduced
    Item {
        visible: bar.ambient && bar.onScreen
        anchors.fill: rail
        clip: true
        Rectangle {
            id: reflection
            width: parent.width * 0.6
            height: parent.height
            radius: rail.radius
            x: parent.width * 0.2
            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0; color: "transparent" }
                GradientStop { position: 0.5; color: Qt.rgba(Theme.wallpaperReflection.r, Theme.wallpaperReflection.g, Theme.wallpaperReflection.b, 0.16) }
                GradientStop { position: 0.75; color: Qt.rgba(Theme.wallpaperPetroleum.r, Theme.wallpaperPetroleum.g, Theme.wallpaperPetroleum.b, 0.05) }
                GradientStop { position: 1; color: "transparent" }
            }
            SequentialAnimation on x {
                running: bar.ambientMoving
                loops: Animation.Infinite
                NumberAnimation { to: bar.width * 0.4; duration: 24000; easing.type: Easing.InOutSine }
                NumberAnimation { to: 0; duration: 24000; easing.type: Easing.InOutSine }
            }
        }
    }
    // The three zones; each slot loads its item and hides while the item has nothing to show.
    Repeater {
        id: zones
        model: ["left", "center", "right"]
        RowLayout {
            id: zone
            onImplicitWidthChanged: bar.relief.restart()
            required property string modelData
            anchors.verticalCenter: parent.verticalCenter
            x: modelData === "left" ? 5 : modelData === "center" ? Math.round((bar.width - width) / 2) : bar.width - width - 5
            height: parent.height
            spacing: modelData === "left" ? Theme.gapS : Theme.gapXs
            Repeater {
                model: bar.layout[zone.modelData]
                Loader {
                    id: slot
                    required property string modelData
                    sourceComponent: bar.items[modelData] || null
                    // Items fade in when they appear and out before their slot closes; instant under reduced motion.
                    readonly property bool wanted: !!item && item.shown
                    opacity: wanted ? 1 : 0
                    visible: wanted || opacity > 0
                    Behavior on opacity { NumberAnimation { duration: slot.wanted ? Theme.enter : Theme.leave; easing.type: Easing.OutCubic } }
                    Layout.alignment: Qt.AlignVCenter
                    Layout.preferredWidth: item ? item.implicitWidth : 0
                    Layout.preferredHeight: item ? item.implicitHeight : 0
                }
            }
        }
    }
    Component {
        id: launcherItem
        BarButton {
            glyph: "grid"
            selected: ShellState.panel === "launcher" && ShellState.screenName === bar.outputName
            flat: true
            hint: I18n.t("bar.item.launcher")
            tooltip: bar.tooltip
            onClicked: ShellState.toggle("launcher", bar.outputName)
        }
    }
    Component {
        id: workspacesItem
        ListView {
            id: workspaceStrip
            readonly property bool shown: true
            readonly property bool dots: ShellState.setting("workspaceStyle", "numbers") === "dots"
            readonly property int slotWidth: dots ? 18 : 24
            // Empty workspaces 1…N shown as well, unless they belong to another screen.
            readonly property int slots: ShellState.setting("workspaceSlots", 0)
            implicitWidth: Math.min(Math.max(78, bar.width / 2 - 170), dots ? 200 : 238, workspaceModel.count * (slotWidth + spacing))
            implicitHeight: bar.height
            clip: true
            orientation: ListView.Horizontal
            spacing: Theme.gapXs
            boundsBehavior: Flickable.StopAtBounds
            interactive: contentWidth > width
            readonly property int activeId: bar.monitor ? bar.monitor.id : (Services.focusedWorkspace ? Services.focusedWorkspace.id : 1)
            readonly property var occupied: Services.workspaces.filter(w => w.id > 0 && w.monitor && w.monitor.name === bar.outputName && (w.id === activeId || Services.windows.some(win => win.workspace && win.workspace.id === w.id))).sort((a, b) => a.id - b.id)
            function syncWorkspaces() {
                if (!workspaceModel)
                    return;
                const entries = occupied.map(w => ({ workspaceId: w.id, workspaceName: w.name || String(w.id), urgent: !!w.urgent, empty: false }));
                if (!entries.some(w => w.workspaceId === activeId) && activeId > 0)
                    entries.push({ workspaceId: activeId, workspaceName: String(activeId), urgent: false, empty: false });
                for (let id = 1; id <= slots; ++id)
                    if (!entries.some(w => w.workspaceId === id) && !Services.workspaces.some(w => w.id === id && w.monitor && w.monitor.name !== bar.outputName))
                        entries.push({ workspaceId: id, workspaceName: String(id), urgent: false, empty: true });
                entries.sort((a, b) => a.workspaceId - b.workspaceId);
                for (let i = workspaceModel.count - 1; i >= 0; --i)
                    if (!entries.some(w => w.workspaceId === workspaceModel.get(i).workspaceId))
                        workspaceModel.remove(i);
                for (let i = 0; i < entries.length; ++i) {
                    if (i >= workspaceModel.count || workspaceModel.get(i).workspaceId !== entries[i].workspaceId)
                        workspaceModel.insert(i, entries[i]);
                    else
                        workspaceModel.set(i, entries[i]);
                }
                currentIndex = entries.findIndex(w => w.workspaceId === activeId);
            }
            onOccupiedChanged: syncWorkspaces()
            onActiveIdChanged: syncWorkspaces()
            onSlotsChanged: syncWorkspaces()
            Component.onCompleted: syncWorkspaces()
            model: ListModel { id: workspaceModel }
            highlightMoveDuration: Theme.reduced ? 0 : 220
            highlightResizeDuration: 0
            highlight: Item {
                Rectangle {
                    visible: !workspaceStrip.dots
                    anchors.centerIn: parent
                    width: workspaceStrip.slotWidth
                    height: 26
                    radius: 8
                    color: Theme.selection
                    Behavior on color { ColorAnimation { duration: Theme.fast } }
                }
            }
            delegate: BarButton {
                id: workspaceButton
                required property int workspaceId
                required property string workspaceName
                required property bool urgent
                required property bool empty
                readonly property bool current: workspaceStrip.activeId === workspaceId
                readonly property color tint: current ? Theme.accentText : urgent ? Theme.warning : empty ? Theme.disabled : Theme.muted
                width: workspaceStrip.slotWidth
                height: workspaceStrip.height
                padding: 0
                flat: true
                hint: I18n.t("bar.workspace", {name: workspaceName})
                contentItem: Item {
                    C.Label {
                        visible: !workspaceStrip.dots
                        anchors.centerIn: parent
                        text: workspaceButton.workspaceName
                        horizontalAlignment: Text.AlignHCenter
                        color: workspaceButton.tint
                        font.pixelSize: Theme.barText - 1
                        font.family: Theme.mono
                    }
                    Rectangle {
                        visible: workspaceStrip.dots
                        anchors.centerIn: parent
                        width: workspaceButton.current ? 14 : 6
                        height: 6
                        radius: 3
                        color: workspaceButton.tint
                        Behavior on width { NumberAnimation { duration: Theme.reduced ? 0 : Theme.fast; easing.type: Easing.OutCubic } }
                    }
                }
                background: Rectangle {
                    anchors.centerIn: parent
                    width: workspaceStrip.slotWidth
                    height: 26
                    radius: 8
                    // A blue-grey wash from the wallpaper tones on hover and keyboard focus.
                    color: workspaceButton.hovered || workspaceButton.visualFocus ? Qt.rgba(Theme.wallpaperCloud.r, Theme.wallpaperCloud.g, Theme.wallpaperCloud.b, 0.2) : "transparent"
                    Behavior on color { ColorAnimation { duration: Theme.fast } }
                }
                onClicked: Services.workspace(workspaceId)
            }
            add: Transition {
                NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Theme.fast }
            }
            remove: Transition {
                NumberAnimation { property: "opacity"; to: 0; duration: Theme.fast }
            }
            displaced: Transition {
                NumberAnimation { property: "x"; duration: Theme.reduced ? 0 : 200; easing.type: Easing.OutCubic }
            }
            WheelHandler {
                onWheel: event => {
                    if (!workspaceModel.count || event.angleDelta.y === 0)
                        return;
                    const next = (workspaceStrip.currentIndex + (event.angleDelta.y < 0 ? 1 : -1) + workspaceModel.count) % workspaceModel.count;
                    Services.workspace(workspaceModel.get(next).workspaceId);
                }
            }
        }
    }
    Component {
        id: windowTitleItem
        RowLayout {
            readonly property bool shown: bar.width > 1400 && bar.leftSqueeze === 0
            spacing: Theme.gapS
            Rectangle {
                Layout.preferredWidth: 1
                Layout.preferredHeight: 12
                color: Theme.hover
            }
            // The focused window's title; plain text, nothing opens from here.
            C.Label {
                Layout.preferredWidth: 150
                Layout.leftMargin: 6
                text: Services.activeWindow ? Services.activeWindow.title : "NEKRYPTA"
                color: Theme.muted
                font.pixelSize: Theme.barText
                elide: Text.ElideRight
                verticalAlignment: Text.AlignVCenter
            }
        }
    }
    // The centre group, [weather] [20/10/2026] [time], always on the bar's horizontal centre. Date and time keep
    // a fixed width (mono digits), so the group never shifts as they tick.
    Component {
        id: clockItem
        RowLayout {
            readonly property bool shown: true
            spacing: Theme.gapXs
            BarTemperature {
                host: bar
                embedded: true
                visible: shown
                Layout.alignment: Qt.AlignVCenter
            }
            BarButton {
                id: clockButton
                readonly property string timeFormat: ShellState.setting("clock24h", true) ? (ShellState.setting("clockSeconds", false) ? "HH:mm:ss" : "HH:mm")
                    : (ShellState.setting("clockSeconds", false) ? "h:mm:ss AP" : "h:mm AP")
                // Fixed dd/MM/yyyy in local time; SystemClock ticks over at midnight.
                readonly property string dateText: I18n.date(clock.date, "dd/MM/yyyy")
                readonly property string timeText: I18n.date(clock.date, timeFormat)
                implicitWidth: Math.ceil(dateMetrics.advanceWidth + timeMetrics.advanceWidth) + Theme.gapM + 24
                implicitHeight: Math.min(Theme.barControlHeight, bar.height)
                Layout.alignment: Qt.AlignVCenter
                TextMetrics {
                    id: dateMetrics
                    text: "00/00/0000"
                    font.family: Theme.mono
                    font.pixelSize: Theme.barText
                }
                TextMetrics {
                    id: timeMetrics
                    // The widest the current format gets, so the width holds as digits change.
                    text: clockButton.timeText.replace(/[0-9]/g, "0")
                    font.family: Theme.mono
                    font.pixelSize: Theme.barText
                }
                flat: true
                contentItem: RowLayout {
                    spacing: Theme.gapM
                    C.Label {
                        text: clockButton.dateText
                        color: Theme.wallpaperHighlight
                        font.family: Theme.mono
                        font.pixelSize: Theme.barText
                        Layout.preferredWidth: Math.ceil(dateMetrics.advanceWidth)
                        horizontalAlignment: Text.AlignHCenter
                    }
                    C.Label {
                        text: clockButton.timeText
                        font.family: Theme.mono
                        font.pixelSize: Theme.barText
                        Layout.preferredWidth: Math.ceil(timeMetrics.advanceWidth)
                        horizontalAlignment: Text.AlignHCenter
                    }
                }
                hint: I18n.longDate(clock.date)
                tooltip: bar.tooltip
                Accessible.name: hint + ", " + timeText
                onClicked: ShellState.toggle("calendar", bar.outputName)
            }
        }
    }
    Component {
        id: temperatureItem
        BarTemperature {
            host: bar
            // The centre group already shows it whenever the clock is on the bar.
            suppressed: ["left", "center", "right"].some(zone => bar.layout[zone].indexOf("clock") >= 0)
        }
    }
    // Now playing as COVER (30) | TITLE / ARTIST (one line each, elides). The controls live in the music page:
    // left click opens it, right click skips to the next track, scrolling sets the player's volume.
    // Width 240 (300 on very wide bars), 190 when the right side is short of room, the cover alone in compact mode.
    Component {
        id: mediaItem
        Item {
            id: mediaButton
            readonly property var player: NowPlaying.player
            readonly property bool shown: !!player && bar.width > 1200 && !bar.crowded && ShellState.setting("barMedia", true)
            readonly property bool selected: bar.controlActive("media")
            readonly property bool titled: bar.squeeze < 4
            readonly property string title: player ? player.trackTitle || player.identity || "" : ""
            readonly property string artist: player ? player.trackArtist || "" : ""
            implicitWidth: bar.squeeze >= 4 ? 36 : bar.squeeze >= 3 ? 190 : bar.width >= 2200 ? 300 : 240
            implicitHeight: Math.min(Theme.barControlHeight, bar.height)
            Component.onCompleted: bar.mediaButton = mediaButton
            Component.onDestruction: if (bar.mediaButton === mediaButton) {
                bar.mediaButton = null;
                bar.mediaHovered = false;
            }
            HoverHandler {
                onHoveredChanged: bar.mediaHovered = hovered
            }
            // Right click: next track, once per click (a repeated event within 250 ms is the same click).
            property double skippedAt: 0
            TapHandler {
                acceptedButtons: Qt.RightButton
                onTapped: {
                    const now = Date.now();
                    if (now - mediaButton.skippedAt < 250 || !mediaButton.player || !mediaButton.player.canGoNext)
                        return;
                    mediaButton.skippedAt = now;
                    mediaButton.player.next();
                }
            }
            // Scrolling changes this player's own volume (MPRIS), never the system's; the event stops here.
            C.WheelSteps {
                onStepped: steps => NowPlaying.stepPlayerVolume(steps)
            }
            Rectangle {
                anchors.fill: parent
                radius: Theme.radiusSmall
                color: mediaButton.selected ? Theme.selection : info.hovered || info.visualFocus ? Theme.hover : "transparent"
                Behavior on color { ColorAnimation { duration: Theme.fast } }
                // Reacts only to the backend's real energy; without live frames it stays at zero.
                Rectangle {
                    anchors.fill: parent
                    radius: parent.radius
                    color: Theme.wallpaperPetroleum
                    opacity: NowPlaying.live && bar.onScreen && !Theme.reduced ? Math.min(0.22, NowPlaying.energy * 0.35) : 0
                    Behavior on opacity { NumberAnimation { duration: 90 } }
                }
            }
            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 3
                anchors.rightMargin: mediaButton.titled ? 8 : 3
                spacing: 8
                C.CoverArt {
                    Layout.preferredWidth: 30
                    Layout.preferredHeight: 30
                    Layout.minimumWidth: 30
                    Layout.alignment: Qt.AlignVCenter
                    radius: 6
                    glyphSize: 14
                    source: NowPlaying.artFile
                    // Left click on the cover pauses or resumes the player; the title opens the music page.
                    TapHandler {
                        onTapped: if (mediaButton.player && mediaButton.player.canTogglePlaying)
                            mediaButton.player.togglePlaying()
                    }
                }
                // Title and artist; opens the music page. The full title is in the tooltip (also on keyboard
                // focus), the accessible description and the music page.
                QQC.AbstractButton {
                    id: info
                    readonly property string hint: mediaButton.title + (mediaButton.artist ? "\n" + mediaButton.artist : "")
                    visible: mediaButton.titled
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    Layout.fillHeight: true
                    hoverEnabled: true
                    focusPolicy: Qt.TabFocus
                    Accessible.name: mediaButton.player ? I18n.t("bar.nowPlaying", {title: mediaButton.title}) : I18n.t("bar.mediaControls")
                    Accessible.description: hint
                    onHoveredChanged: hovered ? bar.tooltip.show(info) : bar.tooltip.hide(info, false)
                    onActiveFocusChanged: activeFocus ? bar.tooltip.show(info) : bar.tooltip.hide(info, false)
                    onPressedChanged: if (pressed) bar.tooltip.hide(info, true)
                    onClicked: bar.toggleControl("media")
                    background: null
                    contentItem: ColumnLayout {
                        spacing: 0
                        C.Label {
                            text: mediaButton.title
                            color: NowPlaying.playing || mediaButton.selected ? Theme.text : Theme.muted
                            font.pixelSize: Theme.barText
                            elide: Text.ElideRight
                            wrapMode: Text.NoWrap
                            maximumLineCount: 1
                            Layout.fillWidth: true
                            Layout.minimumWidth: 0
                        }
                        // The artist, or for a moment the player's volume ("Spotify: 65%") after a scroll.
                        C.Label {
                            text: NowPlaying.volumeNoteShown ? NowPlaying.volumeNote : mediaButton.artist
                            visible: text !== ""
                            color: NowPlaying.volumeNoteShown ? Theme.accentText : Theme.muted
                            font.pixelSize: Theme.barText - 2
                            elide: Text.ElideRight
                            wrapMode: Text.NoWrap
                            maximumLineCount: 1
                            Layout.fillWidth: true
                            Layout.minimumWidth: 0
                            Behavior on color { ColorAnimation { duration: Theme.enter } }
                        }
                    }
                }
            }
        }
    }
    // CPU and memory from the backend's shared sampler (system.telemetry, the system monitor's own source):
    // "CPU 12% • RAM 48%", or icons and "12%  48%" when compact (squeeze 2, or bars under 1600 px). Digits are
    // mono in fixed-width slots; anything unavailable reads "--". Opens the system monitor.
    Component {
        id: systemItem
        BarButton {
            id: systemButton
            property var telemetry: null
            property int generation: 0
            readonly property bool compactView: bar.squeeze >= 2 || bar.width < 1600
            readonly property var cpu: telemetry && telemetry.cpu || ({})
            readonly property var memory: telemetry && telemetry.memory || ({})
            readonly property bool cpuReady: cpu.state === "disponivel" && typeof cpu.percent === "number"
            readonly property bool memoryReady: memory.state === "disponivel" && typeof memory.percent === "number"
            readonly property string cpuText: cpuReady ? Math.round(cpu.percent) + "%" : "--"
            readonly property string ramText: memoryReady ? Math.round(memory.percent) + "%" : "--"
            flat: true
            hint: !cpuReady && !memoryReady ? I18n.t("bar.systemUnavailable")
                : I18n.t("bar.systemHint", {cpu: cpuText, ram: ramText, used: memoryReady ? I18n.number(memory.usedGiB, 1) : "--", total: memoryReady ? I18n.number(memory.totalGiB, 1) : "--"})
            tooltip: bar.tooltip
            Accessible.name: hint
            selected: ShellState.panel === "system" && ShellState.screenName === bar.outputName
            onClicked: ShellState.toggle("system", bar.outputName)
            function poll() {
                if (!Backend.ready)
                    return;
                const request = ++generation;
                Backend.request("system.telemetry", {}, (result, failure) => {
                    if (request === generation)
                        telemetry = failure ? null : result;
                });
            }
            Timer {
                // Every 2 s; every second while the system monitor is open, as the contract allows.
                interval: ShellState.panel === "system" ? 1000 : 2000
                running: bar.onScreen && Backend.ready
                repeat: true
                triggeredOnStart: true
                onTriggered: systemButton.poll()
            }
            TextMetrics {
                id: percentSlot
                text: "100%"
                font.family: Theme.mono
                font.pixelSize: Theme.barText
            }
            contentItem: Item {
                implicitWidth: figures.implicitWidth
                implicitHeight: figures.implicitHeight
                RowLayout {
                    id: figures
                    anchors.centerIn: parent
                    spacing: Theme.gapXs
                    C.Icon { visible: systemButton.compactView; name: "gauge"; size: Theme.barIconSize - 3; color: Theme.muted }
                    C.Label { visible: !systemButton.compactView; text: "CPU"; color: Theme.muted; font.pixelSize: Theme.barText }
                    C.Label {
                        text: systemButton.cpuText
                        font.family: Theme.mono
                        font.pixelSize: Theme.barText
                        horizontalAlignment: Text.AlignRight
                        Layout.preferredWidth: Math.ceil(percentSlot.advanceWidth)
                    }
                    C.Label { visible: !systemButton.compactView; text: "•"; color: Theme.muted; font.pixelSize: Theme.barText }
                    Item { visible: systemButton.compactView; Layout.preferredWidth: 4 }
                    C.Icon { visible: systemButton.compactView; name: "activity"; size: Theme.barIconSize - 3; color: Theme.muted }
                    C.Label { visible: !systemButton.compactView; text: "RAM"; color: Theme.muted; font.pixelSize: Theme.barText }
                    C.Label {
                        text: systemButton.ramText
                        font.family: Theme.mono
                        font.pixelSize: Theme.barText
                        horizontalAlignment: Text.AlignRight
                        Layout.preferredWidth: Math.ceil(percentSlot.advanceWidth)
                    }
                }
            }
        }
    }
    Component {
        id: trayItem
        RowLayout {
            id: tray
            // How many apps get their own icon; the rest open from the tray page. Narrow bars show none.
            readonly property int count: {
                const chosen = ShellState.setting("trayIcons", -1);
                return bar.width <= 1100 || bar.squeeze >= 1 ? 0 : chosen >= 0 ? chosen : bar.width > 1700 ? 3 : 2;
            }
            readonly property bool shown: Services.trayItems.length > 0
            spacing: Theme.gapXs
            Repeater {
                model: Services.trayItems.slice(0, tray.count)
                BarButton {
                    id: trayButton
                    required property var modelData
                    flat: true
                    hint: modelData.tooltipTitle || modelData.title || modelData.id
                    tooltip: bar.tooltip
                    // A fixed cell with the icon clipped and centred inside it, whatever size the app's image is.
                    // Order: the app's own (theme) icon, then a theme icon named after the app, then a generic glyph.
                    contentItem: Item {
                        clip: true
                        // Set once the app's own icon fails to load; then the theme icon named after the app is tried.
                        property bool ownFailed: false
                        readonly property string own: String(trayButton.modelData.icon || "")
                        onOwnChanged: ownFailed = false
                        Image {
                            id: trayIcon
                            anchors.centerIn: parent
                            width: Theme.iconSizeCompact + 1
                            height: width
                            source: parent.own !== "" && !parent.ownFailed ? parent.own : Quickshell.iconPath(String(trayButton.modelData.id || "").toLowerCase(), true)
                            sourceSize.width: width
                            sourceSize.height: height
                            fillMode: Image.PreserveAspectFit
                            visible: status === Image.Ready
                            onStatusChanged: if (status === Image.Error && source.toString() === parent.own)
                                parent.ownFailed = true
                        }
                        C.Icon {
                            anchors.centerIn: parent
                            visible: trayIcon.status !== Image.Ready
                            name: "window"
                            size: Theme.barIconSize
                            color: Theme.muted
                        }
                    }
                    implicitWidth: Theme.barHitTarget
                    selected: !!bar.trayMenu && bar.trayMenu.shown && bar.trayMenu.item === modelData
                    onClicked: modelData.onlyMenu ? bar.trayMenu.toggle(modelData, bar.window, trayButton) : modelData.activate()
                    TapHandler {
                        acceptedButtons: Qt.RightButton
                        onTapped: {
                            bar.tooltip.hide(trayButton, true);
                            bar.trayMenu.toggle(trayButton.modelData, bar.window, trayButton);
                        }
                    }
                    TapHandler {
                        acceptedButtons: Qt.MiddleButton
                        onTapped: {
                            bar.tooltip.hide(trayButton, true);
                            trayButton.modelData.secondaryActivate();
                        }
                    }
                    // Apps such as volume mixers act on scrolling; deltas are Qt's, as Plasma sends them.
                    WheelHandler {
                        onWheel: event => {
                            if (event.angleDelta.y !== 0)
                                trayButton.modelData.scroll(event.angleDelta.y, false);
                            if (event.angleDelta.x !== 0)
                                trayButton.modelData.scroll(event.angleDelta.x, true);
                        }
                    }
                }
            }
            BarButton {
                visible: Services.trayItems.length > tray.count
                glyph: "grid"
                flat: true
                hint: I18n.t("bar.systemTray")
                tooltip: bar.tooltip
                selected: bar.controlActive("tray")
                onClicked: bar.toggleControl("tray")
            }
        }
    }
    Component {
        id: recordingItem
        BarButton {
            shown: ShellState.recording.active
            accent: true
            glyph: "record"
            text: "REC " + Math.floor(ShellState.recordingSeconds / 60) + ":" + String(ShellState.recordingSeconds % 60).padStart(2, "0")
            flat: true
            hint: I18n.t("bar.stopRecording")
            tooltip: bar.tooltip
            onClicked: ShellState.action("stop-recording")
            contentItem: Item {
                // Mono digits keep the chip from jittering every second.
                implicitWidth: recordingRow.implicitWidth
                implicitHeight: recordingRow.implicitHeight
                RowLayout {
                    id: recordingRow
                    anchors.centerIn: parent
                    spacing: Theme.gapS
                    C.Icon { name: "record"; size: Theme.barIconSize; color: Theme.background }
                    C.Label {
                        text: "REC " + Math.floor(ShellState.recordingSeconds / 60) + ":" + String(ShellState.recordingSeconds % 60).padStart(2, "0")
                        color: Theme.background
                        font.family: Theme.mono
                        font.pixelSize: Theme.barText
                    }
                }
            }
        }
    }
    Component {
        id: privacyItem
        // Microphone, camera or both in use (PipeWire), with the apps (or "unknown") in the tooltip; screen sharing
        // as before. While PipeWire isn't connected, a muted marker says capture can't be monitored. Opens the
        // control center with the Security Mode block's details showing.
        BarButton {
            id: privacyButton
            readonly property bool mic: Services.micUsers.length > 0
            readonly property bool camera: Services.cameraUsers.length > 0
            readonly property bool sharing: Services.screenSharers.length > 0
            readonly property bool unmonitored: !Services.captureMonitored
            shown: mic || camera || sharing || unmonitored
            accent: !unmonitored
            flat: true
            hint: unmonitored ? I18n.t("privacy.unmonitored")
                : [mic && camera ? I18n.t("privacy.both") : "",
                   mic ? I18n.t("bar.micInUse", {apps: Services.micUsers.join(", ")}) : "",
                   camera ? I18n.t("privacy.cameraInUse", {apps: Services.cameraUsers.join(", ")}) : "",
                   sharing ? I18n.t("bar.sharing", {apps: Services.screenSharers.join(", ")}) : ""].filter(line => line).join("\n")
            tooltip: bar.tooltip
            Accessible.name: hint
            contentItem: Row {
                spacing: Theme.gapXs
                C.Icon { visible: privacyButton.mic; name: "mic"; size: Theme.barIconSize; color: Theme.background; anchors.verticalCenter: parent.verticalCenter }
                C.Icon { visible: privacyButton.camera; name: "camera"; size: Theme.barIconSize; color: Theme.background; anchors.verticalCenter: parent.verticalCenter }
                C.Icon { visible: privacyButton.sharing; name: "cast"; size: Theme.barIconSize; color: Theme.background; anchors.verticalCenter: parent.verticalCenter }
                C.Icon { visible: privacyButton.unmonitored; name: "mic"; size: Theme.barIconSize; color: Theme.muted; opacity: 0.6; anchors.verticalCenter: parent.verticalCenter }
            }
            onClicked: {
                ShellState.open("control", bar.outputName);
                ShellState.securityExpanded = true;
            }
        }
    }
    Component {
        id: focusItem
        BarButton {
            id: focusButton
            shown: Focus.active
            accent: true
            flat: true
            readonly property string remaining: Focus.remainingSeconds < 60 ? Focus.remainingSeconds + "s" : Focus.minutesText(Focus.remainingSeconds)
            hint: I18n.t("bar.focusHint", {time: remaining})
            tooltip: bar.tooltip
            Accessible.name: I18n.t("bar.focusName", {time: remaining})
            onClicked: Focus.end(true)
            WheelHandler {
                onWheel: event => {
                    if (event.angleDelta.y !== 0)
                        Focus.extend(event.angleDelta.y > 0 ? 5 : -5);
                }
            }
            contentItem: Item {
                implicitWidth: focusRow.implicitWidth
                implicitHeight: focusRow.implicitHeight
                RowLayout {
                    id: focusRow
                    anchors.centerIn: parent
                    spacing: Theme.gapS
                    // The ring empties as the session runs out.
                    Shape {
                        Layout.preferredWidth: 16
                        Layout.preferredHeight: 16
                        preferredRendererType: Shape.CurveRenderer
                        ShapePath {
                            strokeColor: Qt.rgba(Theme.background.r, Theme.background.g, Theme.background.b, 0.3)
                            strokeWidth: 2
                            fillColor: "transparent"
                            PathAngleArc { centerX: 8; centerY: 8; radiusX: 6.5; radiusY: 6.5; startAngle: 0; sweepAngle: 360 }
                        }
                        ShapePath {
                            strokeColor: Theme.background
                            strokeWidth: 2
                            fillColor: "transparent"
                            capStyle: ShapePath.RoundCap
                            PathAngleArc { centerX: 8; centerY: 8; radiusX: 6.5; radiusY: 6.5; startAngle: -90; sweepAngle: 360 * Focus.progress }
                        }
                    }
                    C.Label {
                        text: focusButton.remaining
                        color: Theme.background
                        font.family: Theme.mono
                        font.pixelSize: Theme.barText
                    }
                }
            }
        }
    }
    Component {
        id: timerItem
        BarButton {
            id: timerButton
            readonly property var timer: Focus.nextTimer
            readonly property int remaining: timer ? Math.max(0, Math.ceil((timer.endsAt - Focus.now) / 1000)) : 0
            shown: !!timer
            flat: true
            hint: Focus.timers.map(t => (t.label || I18n.t("bar.timerLabel", {duration: Focus.minutesText(t.seconds)})) + " · " + Focus.clockText(Math.max(0, Math.ceil((t.endsAt - Focus.now) / 1000)))).join("\n") + "\n" + I18n.t("bar.timerCancelFirst")
            tooltip: bar.tooltip
            Accessible.name: I18n.t("bar.timerName", {time: Focus.clockText(remaining)})
            onClicked: {
                const timer = timerButton.timer, left = timerButton.remaining;
                Focus.cancelTimer(timer.id);
                ShellState.message(I18n.t("bar.timerCancelled"), "info", {label: I18n.t("bar.undo"), run: () => Focus.addTimer(left, timer.label)});
            }
            contentItem: Item {
                implicitWidth: timerRow.implicitWidth
                implicitHeight: timerRow.implicitHeight
                RowLayout {
                    id: timerRow
                    anchors.centerIn: parent
                    spacing: Theme.gapS
                    C.Icon { name: "timer"; size: Theme.barIconSize; color: timerButton.foreground }
                    C.Label {
                        text: Focus.clockText(timerButton.remaining)
                        color: timerButton.foreground
                        font.family: Theme.mono
                        font.pixelSize: Theme.barText
                    }
                }
            }
        }
    }
    Component {
        id: stayAwakeItem
        BarButton {
            shown: ShellState.presentation
            glyph: "moon"
            flat: true
            hint: I18n.t("bar.stayAwake")
            tooltip: bar.tooltip
            onClicked: ShellState.presentation = false
        }
    }
    Component {
        id: keyboardItem
        BarButton {
            readonly property var layouts: ShellState.keyboard.layouts || []
            readonly property var current: layouts[ShellState.keyboard.index] || null
            shown: layouts.length > 1
            flat: true
            text: current ? current.code.toUpperCase().slice(0, 3) : ""
            hint: (ShellState.keyboard.name || I18n.t("bar.keyboardLayout")) + "\n" + layouts.map((l, i) => (i === ShellState.keyboard.index ? "● " : "○ ") + l.code + (l.variant ? " (" + l.variant + ")" : "")).join("\n") + "\n" + I18n.t("bar.nextLayout")
            tooltip: bar.tooltip
            Accessible.name: I18n.t("bar.keyboardName", {name: ShellState.keyboard.name || text})
            onClicked: ShellState.nextKeyboardLayout()
        }
    }
    Component {
        id: wifiItem
        BarButton {
            shown: bar.width >= 560 && ShellState.wifiAvailable
            glyph: "wifi"
            flat: true
            // Dimmed while disconnected so a dropped network is visible at a glance.
            opacity: ShellState.wifiConnected || selected ? 1 : 0.45
            Behavior on opacity { NumberAnimation { duration: Theme.fast } }
            hint: {
                const network = (ShellState.wifi.networks || []).find(n => n.connected);
                const name = ShellState.wifi.ssid || (network ? network.ssid || network.name : "");
                return ShellState.wifiConnected ? I18n.t("bar.wifi") + (name ? " · " + name : "") : I18n.t(ShellState.wifi.powered ? "bar.wifiNotConnected" : "bar.wifiOff");
            }
            tooltip: bar.tooltip
            // Opens the control center on the network page.
            selected: bar.controlActive("network")
            onClicked: bar.toggleControl("network")
        }
    }
    Component {
        id: audioItem
        BarButton {
            glyph: Services.muted ? "mute" : "volume"
            selected: bar.controlActive("audio")
            flat: true
            hint: I18n.t("bar.audio", {percent: Math.round(Services.volume * 100)})
            tooltip: bar.tooltip
            onClicked: bar.toggleControl("audio")
            // The OSD shows the new percentage.
            C.WheelSteps {
                onStepped: steps => {
                    if (Services.audioAvailable)
                        Services.stepVolume(steps);
                }
            }
            TapHandler {
                acceptedButtons: Qt.MiddleButton
                onTapped: Services.toggleMute()
            }
        }
    }
    Component {
        id: bluetoothItem
        BarButton {
            readonly property var connected: Services.bluetoothDevices.filter(device => device.connected)
            shown: Services.bluetoothAvailable
            glyph: "bluetooth"
            flat: true
            // Dimmed while off, like Wi-Fi.
            opacity: Services.bluetoothEnabled || bar.controlActive("bluetooth") ? 1 : 0.45
            Behavior on opacity { NumberAnimation { duration: Theme.fast } }
            hint: !Services.bluetoothEnabled ? I18n.t("bar.bluetoothOff")
                : connected.length ? I18n.t("bar.bluetoothConnected", {devices: connected.map(device => device.name || device.deviceName || device.address).join(", ")})
                : I18n.t("bar.bluetoothOn")
            tooltip: bar.tooltip
            Accessible.name: hint
            selected: bar.controlActive("bluetooth")
            onClicked: bar.toggleControl("bluetooth")
        }
    }
    Component {
        id: batteryItem
        BarButton {
            readonly property bool low: !Services.batteryCharging && Services.onBattery && Services.batteryPercent <= 20
            // Low charge always shows its number, even when the percentage is turned off.
            readonly property bool percent: {
                const choice = ShellState.setting("batteryPercent", "auto");
                return low || choice === "always" || (choice === "auto" && !bar.secondaryCompact);
            }
            shown: Services.batteryAvailable
            // Sized to its content (the 19 px icon plus "100%"), never below the 40 px target.
            implicitWidth: Math.max(Theme.barHitTarget, contentItem.implicitWidth + leftPadding + rightPadding)
            glyph: Services.batteryCharging ? "bolt" : "battery"
            text: percent ? Math.round(Services.batteryPercent) + "%" : ""
            tone: !low ? "" : Services.batteryPercent <= 10 ? "danger" : "warning"
            flat: true
            hint: I18n.t("bar.battery", {percent: Math.round(Services.batteryPercent)})
            tooltip: bar.tooltip
            Accessible.name: I18n.t("bar.batteryName", {percent: Math.round(Services.batteryPercent)}) + (Services.batteryCharging ? I18n.t("bar.charging") : "")
            selected: bar.controlActive("power")
            onClicked: bar.toggleControl("power")
        }
    }
    Component {
        id: notificationsItem
        BarButton {
            // Do not disturb swaps the bell so suppressed popups are never a surprise.
            glyph: ShellState.setting("dnd", false) ? "bell-off" : "bell"
            implicitWidth: bar.secondaryCompact ? Theme.barHitTarget : 58
            selected: ShellState.panel === "notifications" && ShellState.screenName === bar.outputName
            flat: true
            text: !bar.secondaryCompact && Services.notifications.length ? (Services.notifications.length > 99 ? "99+" : String(Services.notifications.length)) : ""
            hint: I18n.t("bar.notifications")
            tooltip: bar.tooltip
            Accessible.name: I18n.t("bar.notifications") + (ShellState.setting("dnd", false) ? I18n.t("bar.dnd") : "") + (Services.notifications.length ? I18n.t("bar.waiting", {count: Services.notifications.length}) : "")
            Rectangle {
                visible: bar.secondaryCompact && Services.notifications.length > 0
                anchors.right: parent.right
                anchors.rightMargin: 4
                anchors.top: parent.top
                anchors.topMargin: 4
                width: 5
                height: 5
                radius: 2.5
                color: Theme.accent
            }
            onClicked: ShellState.toggle("notifications", bar.outputName)
        }
    }
    Component {
        id: controlItem
        BarButton {
            glyph: "settings"
            selected: bar.controlActive("")
            implicitHeight: Math.min(Theme.barControlHeight, bar.height)
            flat: false
            hint: I18n.t("bar.control")
            tooltip: bar.tooltip
            Accessible.name: hint
            onClicked: bar.toggleControl("")
        }
    }
}
