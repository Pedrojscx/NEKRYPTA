import "../components" as C
import "../core"
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../core/i18n.js" as I18n

FocusScope {
    id: root
    readonly property string title: I18n.t("Session")
    readonly property int panelWidth: 440
    readonly property int panelHeight: Theme.body >= 18 ? 470 : 440

    property bool leaving: false

    // ShellState runs the action, so a refusal still shows as a message after this panel closes.
    function endSession(action) {
        if (leaving)
            return;
        leaving = true;
        ShellState.close();
        ShellState.sessionAction(action);
    }

    ScrollView {
        id: scroll
        ScrollBar.vertical: C.ScrollBar {
                parent: scroll
                x: scroll.width - width
                y: scroll.topPadding
                height: scroll.availableHeight
            }
        anchors.fill: parent
        contentWidth: availableWidth
        clip: true

        ColumnLayout {
            width: scroll.availableWidth
            spacing: Theme.gapS

            C.Card {
                visible: ShellState.recording.active
                Layout.fillWidth: true
                implicitHeight: recordingWarning.implicitHeight + 2 * Theme.gapM
                RowLayout {
                    id: recordingWarning
                    anchors.fill: parent
                    anchors.margins: Theme.gapM
                    spacing: Theme.gapS
                    C.Icon { name: "record"; color: Theme.warning }
                    C.Label {
                        text: I18n.t("Recording in progress · save it before ending your session")
                        color: Theme.warning
                        Layout.fillWidth: true
                        wrapMode: Text.WordWrap
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.gapS

                C.Button {
                    text: I18n.t("Lock")
                    glyph: "lock"
                    implicitHeight: Theme.controlLarge
                    Layout.fillWidth: true
                    Layout.preferredWidth: 1
                    enabled: !root.leaving
                    onClicked: ShellState.action("lock")
                }

                C.Button {
                    text: I18n.t("Suspend")
                    glyph: "moon"
                    implicitHeight: Theme.controlLarge
                    Layout.fillWidth: true
                    Layout.preferredWidth: 1
                    enabled: !root.leaving
                    onClicked: root.endSession("suspend")
                }

            }

            C.SectionHeader {
                text: I18n.t("Hold to end session")
                Layout.topMargin: Theme.gapS
            }

            Repeater {
                model: [{
                    "title": I18n.t("Log out"),
                    "glyph": "logout",
                    "action": "logout",
                    "detail": I18n.t("Close your desktop session")
                }, {
                    "title": I18n.t("Restart"),
                    "glyph": "refresh",
                    "action": "reboot",
                    "detail": I18n.t("Restart your computer")
                }, {
                    "title": I18n.t("Shut down"),
                    "glyph": "power",
                    "action": "shutdown",
                    "detail": I18n.t("Power off your computer")
                }]

                C.HoldButton {
                    required property var modelData

                    text: modelData.title
                    glyph: modelData.glyph
                    description: modelData.detail
                    destructive: modelData.action === "shutdown"
                    Layout.fillWidth: true
                    implicitHeight: Math.max(74, Theme.body * 2 + 46)
                    armed: !root.leaving && ShellState.panel === "power" && !ShellState.locked && root.Window.active
                    onConfirmed: root.endSession(modelData.action)
                }

            }

        }

    }

}
