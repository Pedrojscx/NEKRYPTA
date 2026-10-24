import QtQuick
import QtQuick.Layouts
import "../core"
import "../components" as C
import "../core/i18n.js" as I18n

FocusScope {
    id: root
    readonly property string title: I18n.t("Calendar")
    readonly property int panelWidth: 360
    readonly property int panelHeight: 460 + extraHeight
    property date today: new Date()
    property date month: new Date(today.getFullYear(), today.getMonth(), 1)
    readonly property int firstDay: ShellState.firstDayOfWeek
    readonly property int offset: (month.getDay() - firstDay + 7) % 7
    // The panel grows by the weather card so the month grid keeps its size.
    readonly property int extraHeight: weatherCard.implicitHeight + 16
    property alias weatherCard: weatherCard
    function shift(delta) {
        month = new Date(month.getFullYear(), month.getMonth() + delta, 1);
    }
    Timer {
        interval: 30000
        running: true
        repeat: true
        onTriggered: root.today = new Date()
    }
    ColumnLayout {
        anchors.fill: parent
        spacing: Theme.gapL
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.gapL
            C.Label {
                text: I18n.date(root.today, "dd")
                font.pixelSize: 46
                font.family: Theme.mono
                font.weight: Font.Light
                Layout.preferredHeight: 52
            }
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 2
                // "terça-feira, 20 de outubro de 2026", in the locale's own long form.
                C.Label {
                    text: I18n.longDate(root.today)
                    font.weight: Font.DemiBold
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }
            }
            Rectangle {
                Layout.preferredWidth: 6
                Layout.preferredHeight: 24
                radius: 3
                color: Theme.accent
            }
        }
        WeatherCard {
            id: weatherCard
            Layout.fillWidth: true
        }
        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            implicitHeight: calendarBody.implicitHeight + 24
            radius: Theme.controlRadius
            color: Theme.surface
            WheelHandler {
                property double lastShift: 0
                onWheel: event => {
                    if (event.angleDelta.y === 0 || Date.now() - lastShift < 180)
                        return;
                    lastShift = Date.now();
                    root.shift(event.angleDelta.y < 0 ? 1 : -1);
                }
            }
            ColumnLayout {
                id: calendarBody
                anchors.fill: parent
                anchors.margins: 12
                spacing: Theme.gapS
                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme.gapS
                    C.Button {
                        glyph: "back"
                        implicitWidth: 36
                        implicitHeight: Theme.controlHeightCompact
                        flat: true
                        hint: I18n.t("Previous month")
                        onClicked: root.shift(-1)
                    }
                    C.Label {
                        text: I18n.date(root.month, "MMMM yyyy")
                        font.pixelSize: Theme.body
                        font.weight: Font.DemiBold
                        Layout.fillWidth: true
                        horizontalAlignment: Text.AlignHCenter
                    }
                    C.Button {
                        glyph: "chevron"
                        implicitWidth: 36
                        implicitHeight: Theme.controlHeightCompact
                        flat: true
                        hint: I18n.t("Next month")
                        onClicked: root.shift(1)
                    }
                }
                // The widest day number at the body size, so a cell never shrinks below it.
                TextMetrics {
                    id: dayMetrics
                    font.pixelSize: Theme.body
                    font.family: Theme.font
                    text: "30"
                }
                GridLayout {
                    columns: 7
                    columnSpacing: 2
                    rowSpacing: Theme.gapXs
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Repeater {
                        model: 7
                        C.Label {
                            required property int index
                            text: I18n.qtLocale().dayName(((root.firstDay + index) % 7) || 7, Locale.ShortFormat)
                            color: Theme.muted
                            font.pixelSize: Theme.caption
                            Layout.fillWidth: true
                            Layout.preferredWidth: 1
                            horizontalAlignment: Text.AlignHCenter
                        }
                    }
                    Repeater {
                        model: 42
                        C.Button {
                            required property int index
                            readonly property date day: new Date(root.month.getFullYear(), root.month.getMonth(), 1 + index - root.offset)
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            Layout.preferredWidth: 1
                            implicitWidth: Theme.controlHeightCompact
                            implicitHeight: Math.max(30, Theme.body + 16)
                            // The button's own side padding left no room for "30" in a seventh of the panel.
                            leftPadding: 0
                            rightPadding: 0
                            Layout.minimumWidth: dayMetrics.advanceWidth + 4
                            cornerRadius: 8
                            text: String(day.getDate())
                            flat: true
                            opacity: day.getMonth() === root.month.getMonth() ? 1 : 0.64
                            selected: I18n.date(day, "yyyy-MM-dd") === I18n.date(root.today, "yyyy-MM-dd")
                            accent: selected
                            readonly property bool outside: day.getMonth() !== root.month.getMonth()
                            hint: I18n.longDate(day)
                            Accessible.name: (outside ? I18n.t("Go to ") : I18n.t("Copy ")) + hint
                            // Days from a neighbouring month navigate there; days in view copy the date.
                            onClicked: {
                                if (outside) {
                                    root.month = new Date(day.getFullYear(), day.getMonth(), 1);
                                    return;
                                }
                                ShellState.run("clipboard.write", {
                                    text: I18n.date(day, "yyyy-MM-dd")
                                }, () => ShellState.message(I18n.t("Date copied · ") + I18n.date(day, "yyyy-MM-dd"), "success"));
                            }
                        }
                    }
                }
            }
        }
        // The footer's one action: back to the current month, across the full width.
        C.Button {
            text: I18n.t("Today")
            Layout.fillWidth: true
            glyph: "calendar"
            enabled: root.month.getFullYear() !== root.today.getFullYear() || root.month.getMonth() !== root.today.getMonth()
            onClicked: root.month = new Date(root.today.getFullYear(), root.today.getMonth(), 1)
        }
        C.Label {
            text: I18n.t("Local time · ") + I18n.date(root.today, "t")
            color: Theme.disabled
            font.pixelSize: Theme.caption
            Layout.fillWidth: true
            horizontalAlignment: Text.AlignHCenter
        }
    }
    Keys.onPressed: event => {
        if (event.key === Qt.Key_PageUp || event.key === Qt.Key_PageDown) {
            shift(event.key === Qt.Key_PageUp ? -1 : 1);
            event.accepted = true;
        } else if (event.key === Qt.Key_Home) {
            month = new Date(today.getFullYear(), today.getMonth(), 1);
            event.accepted = true;
        }
    }
    Keys.onLeftPressed: shift(-1)
    Keys.onRightPressed: shift(1)
}
