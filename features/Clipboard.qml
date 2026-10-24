import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../core"
import "../components" as C
import "../core/i18n.js" as I18n

FocusScope {
    id: root
    readonly property string title: I18n.t("Clipboard")
    readonly property bool showHeader: false
    property alias query: search.text
    property var entries: []
    property var staleEntries: []
    property string error: ""
    property bool busy: false
    property int requestGeneration: 0
    property bool confirmClear: false
    property bool mutating: false
    property bool hasHistory: false
    property bool alive: true
    Component.onDestruction: alive = false
    property point lastPointer: Qt.point(-1, -1)
    function pointerMoved(position) {
        const moved = lastPointer.x >= 0 && (Math.abs(position.x - lastPointer.x) > 1 || Math.abs(position.y - lastPointer.y) > 1);
        lastPointer = position;
        return moved;
    }
    function moveSelection(amount) {
        if (!list.count)
            return;
        list.currentIndex = Math.max(0, Math.min(list.count - 1, list.currentIndex + amount));
        list.positionViewAtIndex(list.currentIndex, ListView.Contain);
    }
    function mutate(method, params) {
        if (mutating || busy)
            return;
        mutating = true;
        error = "";
        Backend.request(method, params, (result, failure) => {
            mutating = false;
            if (failure) {
                error = I18n.error(failure);
                ShellState.message(I18n.error(failure), "error");
                return;
            }
            confirmClear = false;
            if (method === "clipboard.clear") hasHistory = false;
            refresh();
            search.forceActiveFocus();
        });
    }
    Keys.onEscapePressed: event => {
        if (confirmClear) {
            if (!mutating) {
                confirmClear = false;
                search.forceActiveFocus();
            }
        } else
            event.accepted = false;
    }
    function refresh() {
        busy = true;
        const generation = ++requestGeneration;
        const query = search.text;
        const previousIndex = list.currentIndex;
        const previousEntry = (entries.length ? entries : staleEntries)[previousIndex];
        Backend.request("clipboard.list", {
            query: query,
            limit: 500
        }, (r, e) => {
            if (generation !== requestGeneration || query !== search.text)
                return;
            busy = false;
            error = e ? I18n.error(e) : "";
            entries = e ? [] : r.entries;
            staleEntries = [];
            if (!e && query === "") hasHistory = entries.length > 0;
            else if (!e && entries.length > 0) hasHistory = true;
            const retainedIndex = previousEntry ? entries.findIndex(entry => entry.raw === previousEntry.raw) : -1;
            list.currentIndex = entries.length ? (retainedIndex >= 0 ? retainedIndex : Math.max(0, Math.min(previousIndex, entries.length - 1))) : -1;
        });
    }
    function copy(entry) {
        if (!entry || busy || mutating)
            return;
        mutating = true;
        Backend.request("clipboard.copy", {entry: entry.raw}, (result, failure) => {
            mutating = false;
            if (failure) {
                error = I18n.error(failure);
                ShellState.message(I18n.error(failure), "error");
                return;
            }
            ShellState.close();
            ShellState.message(I18n.t("Copied to clipboard"), "success");
        });
    }
    // Links open in the browser; paths in their app or the file manager.
    function open(entry) {
        if (!entry || (entry.kind !== "link" && entry.kind !== "path") || busy || mutating)
            return;
        mutating = true;
        Backend.request("clipboard.open", {entry: entry.raw}, (result, failure) => {
            // Closed while cliphist was decoding: the user moved on, so open nothing.
            if (!root.alive)
                return;
            mutating = false;
            if (failure) {
                ShellState.message(I18n.error(failure), "error");
                return;
            }
            ShellState.close();
            if (result.url) ShellState.openUrl(result.url);
            else ShellState.openPath(result.path);
        });
    }
    Component.onCompleted: refresh()
    Connections {
        target: Backend
        function onReadyChanged() {
            if (Backend.ready) root.refresh();
        }
    }
    ColumnLayout {
        anchors.fill: parent
        spacing: Theme.gapL
        RowLayout {
            C.Field {
                id: search
                Layout.fillWidth: true
                focus: true
                placeholderText: I18n.t("Search clipboard history…")
                onTextChanged: {
                    ++root.requestGeneration;
                    root.busy = true;
                    if (root.entries.length) root.staleEntries = root.entries;
                    root.entries = [];
                    root.confirmClear = false;
                    debounce.restart();
                }
                Keys.onDownPressed: root.moveSelection(1)
                Keys.onUpPressed: root.moveSelection(-1)
                Keys.onPressed: event => {
                    if (event.key === Qt.Key_PageDown || event.key === Qt.Key_PageUp) {
                        root.moveSelection(event.key === Qt.Key_PageDown ? 6 : -6);
                        event.accepted = true;
                    } else if (event.key === Qt.Key_Delete && (event.modifiers & Qt.ShiftModifier)) {
                        // Shift+Delete removes the selected entry; plain Delete keeps editing the search.
                        const entry = root.entries[list.currentIndex];
                        if (entry)
                            root.mutate("clipboard.delete", {entry: entry.raw});
                        event.accepted = true;
                    } else if ((event.key === Qt.Key_Return || event.key === Qt.Key_Enter) && (event.modifiers & Qt.ControlModifier)) {
                        // Entries with nothing to open fall through to onAccepted and copy.
                        const entry = root.entries[list.currentIndex];
                        if (entry && (entry.kind === "link" || entry.kind === "path")) {
                            root.open(entry);
                            event.accepted = true;
                        }
                    }
                }
                onAccepted: root.copy(root.entries[list.currentIndex])
            }
        }
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.gapS
            visible: root.confirmClear
            C.Label {
                text: I18n.t("Delete all clipboard history?")
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
            }
            C.Button {
                text: I18n.t("Cancel")
                enabled: !root.mutating
                onClicked: { root.confirmClear = false; search.forceActiveFocus(); }
            }
            C.Button {
                text: I18n.t("Clear")
                danger: true
                enabled: !root.mutating && !root.busy
                onClicked: root.mutate("clipboard.clear", {})
            }
        }
        C.Label {
            visible: root.error !== "" && root.entries.length > 0
            text: root.error
            color: Theme.danger
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
        }
        ListView {
            id: list
            model: root.busy && root.staleEntries.length ? root.staleEntries : root.entries
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            spacing: Theme.gapXs
            boundsBehavior: Flickable.StopAtBounds
            highlightMoveDuration: Theme.fast
            highlight: Rectangle {
                color: Theme.raised
                width: list.width - 8
                radius: Theme.controlRadius
            }
            ScrollBar.vertical: C.ScrollBar {}
            delegate: Item {
                id: row
                required property var modelData
                required property int index
                property string thumbnail: ""
                Component.onCompleted: if (modelData.label) {
                    const requestedEntry = modelData.raw;
                    Backend.request("clipboard.thumbnail", {
                        entry: requestedEntry
                    }, (r, e) => {
                        if (!e && r.path && row.modelData.raw === requestedEntry)
                            thumbnail = "file://" + r.path;
                    })
                }
                width: list.width - 8
                height: Math.max(68, Theme.body * 2 + 32)
                MouseArea {
                    anchors.fill: parent
                    enabled: !root.busy && !root.mutating
                    hoverEnabled: true
                    onPositionChanged: mouse => {
                        if (root.pointerMoved(mapToItem(null, mouse.x, mouse.y)))
                            list.currentIndex = row.index;
                    }
                    onClicked: root.copy(row.modelData)
                }
                RowLayout {
                    anchors.fill: parent
                    anchors.margins: 12
                    spacing: Theme.gapL
                    C.Icon {
                        visible: row.thumbnail === "" && row.modelData.kind !== "color"
                        name: row.modelData.kind === "link" ? "link" : row.modelData.kind === "path" ? "folder" : "clipboard"
                        color: Theme.muted
                    }
                    Rectangle {
                        visible: row.modelData.kind === "color"
                        color: row.modelData.color || "transparent"
                        Layout.preferredWidth: 32
                        Layout.preferredHeight: 32
                        radius: Theme.radiusSmall
                        border.width: 1
                        border.color: Theme.border
                    }
                    Image {
                        visible: row.thumbnail !== ""
                        source: row.thumbnail
                        Layout.preferredWidth: 44
                        Layout.preferredHeight: 44
                        sourceSize.width: 88
                        sourceSize.height: 88
                        fillMode: Image.PreserveAspectFit
                        asynchronous: true
                    }
                    C.Label {
                        text: row.modelData.label || row.modelData.text
                        color: row.modelData.label ? Theme.muted : Theme.text
                        Layout.fillWidth: true
                        maximumLineCount: 2
                        wrapMode: Text.Wrap
                    }
                    C.Button {
                        visible: row.modelData.kind === "link" || row.modelData.kind === "path"
                        text: row.modelData.kind === "path" && !row.modelData.folder ? I18n.t("Show in folder") : I18n.t("Open")
                        flat: true
                        hint: text + I18n.t(" (Ctrl+Enter)")
                        implicitHeight: 32
                        enabled: !root.mutating && !root.busy
                        onClicked: root.open(row.modelData)
                    }
                    C.Button {
                        glyph: "trash"
                        flat: true
                        hint: I18n.t("Delete entry")
                        implicitWidth: 32
                        implicitHeight: 32
                        enabled: !root.mutating && !root.busy
                        onClicked: root.mutate("clipboard.delete", {entry: row.modelData.raw})
                    }
                }
            }
            C.EmptyState {
                anchors.centerIn: parent
                width: parent.width - 30
                visible: list.count === 0 && !root.busy
                glyph: "clipboard"
                title: root.error ? I18n.t("History unavailable") : search.text ? I18n.t("No matches") : I18n.t("Clipboard is empty")
                description: root.error || (search.text ? I18n.t("No saved items match this search.") : I18n.t("Copied text and images will appear here."))
            }
            C.EmptyState {
                anchors.centerIn: parent
                width: parent.width - 30
                visible: list.count === 0 && root.busy
                glyph: "clipboard"
                title: search.text ? I18n.t("Searching history…") : I18n.t("Loading history…")
                description: ""
            }
        }
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.gapS
            C.Label {
                text: root.mutating ? I18n.t("Updating history…") : root.busy ? (search.text ? I18n.t("Searching history…") : I18n.t("Loading history…")) : root.entries.length + (search.text ? root.entries.length === 1 ? I18n.t(" match") : I18n.t(" matches") : root.entries.length === 1 ? I18n.t(" item") : I18n.t(" items"))
                color: Theme.muted
                font.pixelSize: Theme.small
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
            }
            C.Button {
                visible: root.error !== "" && !root.busy
                text: I18n.t("Retry")
                glyph: "refresh"
                enabled: !root.mutating
                onClicked: root.refresh()
            }
            C.Button {
                text: I18n.t("Clear all")
                glyph: "trash"
                danger: true
                enabled: !root.busy && !root.mutating && root.hasHistory
                onClicked: root.confirmClear = !root.confirmClear
            }
        }
    }
    Timer {
        id: debounce
        interval: 160
        onTriggered: root.refresh()
    }
}
