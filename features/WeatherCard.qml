import QtQuick
import QtQuick.Layouts
import "../core"
import "../components" as C
import "../core/i18n.js" as I18n

// Weather for the city fixed in core/personal.json, shown in the calendar.
Rectangle {
    id: root
    readonly property var location: ShellState.setting("weatherLocation", {})
    readonly property bool hasLocation: !!preview || (!!location && !!location.name)
    readonly property string locationKey: hasLocation && !preview ? location.latitude + "," + location.longitude : ""
    readonly property string units: ShellState.setting("weatherUnits", "metric")
    // Fixed data for UI previews; disables all requests.
    property var preview: null
    property var weather: preview
    property string error: ""
    property bool loading: false
    property int generation: 0
    readonly property int now: Math.floor(clock.stamp / 1000)
    radius: Theme.controlRadius
    color: Theme.surface
    implicitHeight: body.implicitHeight + 24

    function refresh(force) {
        if (preview || !hasLocation || !Backend.ready)
            return;
        const request = ++generation;
        loading = true;
        Backend.request("weather.get", {location: location, units: units, refresh: !!force}, (r, e) => {
            if (request !== generation)
                return;
            loading = false;
            error = e ? I18n.error(e) : (r.stale ? r.warning || "" : "");
            if (!e)
                weather = r;
        });
    }
    function degrees(value) {
        return value === null || value === undefined ? "–" : value + "°";
    }
    function age(seconds) {
        const minutes = Math.max(0, Math.round((now - seconds) / 60));
        return minutes < 1 ? I18n.t("just now") : minutes < 60 ? minutes + I18n.t(" min ago") : Math.round(minutes / 60) + I18n.t(" h ago");
    }
    function dayName(date) {
        const parts = String(date).split("-").map(Number);
        return I18n.qtLocale().dayName(new Date(parts[0], parts[1] - 1, parts[2]).getDay(), Locale.ShortFormat);
    }
    onLocationKeyChanged: {
        weather = null;
        error = "";
        refresh(false);
    }
    onUnitsChanged: refresh(false)
    Component.onCompleted: refresh(false)
    Connections {
        target: Backend
        function onReadyChanged() {
            if (Backend.ready)
                root.refresh(false);
        }
    }
    Timer {
        id: clock
        property double stamp: Date.now()
        interval: 60000
        repeat: true
        running: true
        // The backend keeps forecasts for 30 minutes, so this refetches at most that often.
        onTriggered: {
            stamp = Date.now();
            root.refresh(false);
        }
    }
    ColumnLayout {
        id: body
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: 12
        spacing: Theme.gapM

        // Current conditions.
        RowLayout {
            visible: root.hasLocation
            Layout.fillWidth: true
            spacing: Theme.gapM
            C.Icon {
                name: root.weather ? root.weather.current.icon : "cloud"
                size: 32
                opacity: root.weather ? 1 : 0.5
            }
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 1
                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme.gapS
                    C.Label {
                        visible: !!root.weather
                        text: root.weather ? root.degrees(root.weather.current.temperature) : ""
                        font.pixelSize: Theme.heading + 4
                        font.weight: Font.DemiBold
                    }
                    C.Label {
                        text: root.weather ? I18n.weatherCondition(root.weather.current.label) : root.loading ? I18n.t("Loading weather…") : root.error || I18n.t("Weather unavailable")
                        color: root.weather ? Theme.text : Theme.muted
                        elide: Text.ElideRight
                        Layout.fillWidth: true
                    }
                }
                C.Label {
                    text: {
                        const place = root.hasLocation ? (root.preview ? root.preview.location.name : root.location.name) : "";
                        if (!root.weather)
                            return place;
                        const current = root.weather.current;
                        return I18n.t("Feels like ") + root.degrees(current.feelsLike) + " · " + place;
                    }
                    color: Theme.muted
                    font.pixelSize: Theme.small
                    elide: Text.ElideRight
                    Layout.fillWidth: true
                }
            }
            C.Button {
                visible: !root.weather && !root.loading
                glyph: "refresh"
                flat: true
                implicitWidth: 32
                implicitHeight: 32
                hint: I18n.t("Try again")
                onClicked: root.refresh(true)
            }
        }

        // Next three days.
        RowLayout {
            visible: root.hasLocation && !!root.weather && root.weather.days.length > 0
            Layout.fillWidth: true
            spacing: Theme.gapS
            Repeater {
                model: root.weather ? root.weather.days : []
                Rectangle {
                    id: dayCell
                    required property var modelData
                    Layout.fillWidth: true
                    Layout.preferredWidth: 1
                    implicitHeight: dayColumn.implicitHeight + 12
                    radius: Theme.radiusSmall + 2
                    color: Theme.raised
                    Accessible.role: Accessible.StaticText
                    Accessible.name: root.dayName(modelData.date) + ", " + I18n.weatherCondition(modelData.label) + I18n.t(", high ") + root.degrees(modelData.high) + I18n.t(", low ") + root.degrees(modelData.low)
                    ColumnLayout {
                        id: dayColumn
                        anchors.centerIn: parent
                        spacing: Theme.gapXs
                        C.Label {
                            text: root.dayName(dayCell.modelData.date)
                            color: Theme.muted
                            font.pixelSize: Theme.caption
                            Layout.alignment: Qt.AlignHCenter
                        }
                        C.Icon {
                            name: dayCell.modelData.icon
                            size: 18
                            Layout.alignment: Qt.AlignHCenter
                        }
                        Row {
                            spacing: Theme.gapXs
                            Layout.alignment: Qt.AlignHCenter
                            C.Label {
                                text: root.degrees(dayCell.modelData.high)
                                font.pixelSize: Theme.caption
                                font.family: Theme.mono
                            }
                            C.Label {
                                text: root.degrees(dayCell.modelData.low)
                                color: Theme.muted
                                font.pixelSize: Theme.caption
                                font.family: Theme.mono
                            }
                        }
                    }
                }
            }
        }

        C.Label {
            visible: root.hasLocation && !!root.weather && !!root.weather.stale
            text: I18n.t("Offline · updated ") + (root.weather ? root.age(root.weather.fetched) : "")
            color: Theme.disabled
            font.pixelSize: Theme.caption
            Layout.fillWidth: true
        }
    }
}
