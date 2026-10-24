import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import "../core"
import "../services"
import "../components" as C
import "../core/i18n.js" as I18n

FocusScope {
    readonly property string title: I18n.t("Notifications")
    readonly property int panelWidth: 420
    ColumnLayout {
        anchors.fill: parent
        spacing: Theme.gapM
        C.Toggle {
            text: I18n.t("Do not disturb")
            selected: ShellState.setting("dnd", false)
            Layout.fillWidth: true
            onClicked: ShellState.setSetting("dnd", !ShellState.setting("dnd", false))
        }
        ListView {
            id: list
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            spacing: Theme.gapS
            model: Services.notificationModel
            property int refocus: -1
            onCountChanged: if (refocus >= 0) {
                const index = Math.min(refocus, count - 1);
                refocus = -1;
                Qt.callLater(() => {
                    const card = list.itemAtIndex(index);
                    if (card)
                        card.forceActiveFocus(Qt.TabFocusReason);
                });
            }
            section.property: "appName"
            section.criteria: ViewSection.FullString
            section.delegate: RowLayout {
                id: sectionRow
                required property string section
                readonly property var latest: Services.notifications.find(n => n.appName === section)
                width: list.width
                height: 38
                spacing: Theme.gapS
                Image {
                    id: sectionIcon
                    visible: status === Image.Ready
                    source: sectionRow.latest && sectionRow.latest.appIcon ? Quickshell.iconPath(sectionRow.latest.appIcon, true) : ""
                    sourceSize.width: 16
                    sourceSize.height: 16
                    Layout.preferredWidth: 16
                    Layout.preferredHeight: 16
                    Layout.leftMargin: 2
                }
                C.Icon {
                    visible: !sectionIcon.visible
                    name: "bell"
                    color: Theme.muted
                    Layout.leftMargin: 2
                }
                C.SectionHeader {
                    text: sectionRow.section || I18n.t("Notification")
                    C.Button {
                        text: I18n.t("Clear")
                        flat: true
                        implicitHeight: Theme.controlHeightCompact
                        Accessible.name: I18n.t("Clear notifications from ") + (sectionRow.section || I18n.t("this app"))
                        onClicked: Services.dismissApplication(sectionRow.section)
                    }
                }
            }
            // Fade inside the delegate: a ListView add transition freezes later rows
            // at stale positions when a card's height settles ("Show more").
            delegate: NotificationCard {
                id: historyCard
                required property var notificationData
                required property int index
                width: list.width
                notification: notificationData
                onActiveFocusChanged: if (activeFocus) list.positionViewAtIndex(index, ListView.Contain)
                onRemoving: list.refocus = index
                NumberAnimation {
                    id: arrival
                    target: historyCard
                    property: "opacity"
                    from: 0
                    to: 1
                    duration: Theme.enter
                }
                Component.onCompleted: if (Date.now() - (notificationData.timestamp || 0) < 2000) arrival.start()
            }
            remove: Transition {
                NumberAnimation {
                    property: "opacity"
                    to: 0
                    duration: Theme.leave
                }
            }
            displaced: Transition {
                NumberAnimation {
                    properties: "x,y"
                    duration: Theme.reduced ? 0 : Theme.enter
                    easing.type: Easing.OutCubic
                }
            }
            ScrollBar.vertical: C.ScrollBar {}
            C.EmptyState {
                anchors.centerIn: parent
                width: parent.width - 20
                visible: list.count === 0
                title: I18n.t("All caught up")
                glyph: ShellState.setting("dnd", false) ? "bell-off" : "bell"
                description: ShellState.setting("dnd", false) ? I18n.t("Do not disturb is on") : ""
            }
        }
        RowLayout {
            visible: list.count > 0
            Layout.fillWidth: true
            spacing: Theme.gapS
            C.Label {
                text: list.count + (list.count === 1 ? I18n.t(" notification") : I18n.t(" notifications"))
                color: Theme.muted
                font.pixelSize: Theme.small
                Layout.fillWidth: true
            }
            C.Button {
                text: I18n.t("Clear all")
                flat: true
                onClicked: Services.dismissAllNotifications()
            }
        }
    }
}
