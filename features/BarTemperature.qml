import QtQuick
import QtQuick.Layouts
import "../core"
import "../components" as C
import "../core/i18n.js" as I18n

// The current temperature in Celsius for the city chosen in the calendar's weather card. Asks every 10 minutes;
// the backend answers from its 30-minute cache, or with an old copy marked stale while offline.
C.Button {
    id: root
    // The BarContent it sits in, for its width, tooltip and screen.
    required property Item host
    readonly property var location: ShellState.setting("weatherLocation", {})
    readonly property bool hasLocation: !!location && !!location.name
    property var weather: null
    property int generation: 0
    readonly property bool stale: !!weather && weather.stale === true
    readonly property string degrees: weather && weather.current ? Math.round(weather.current.temperature) + "°" : ""
    readonly property string updated: weather && weather.fetched ? I18n.date(new Date(weather.fetched * 1000), "HH:mm") : ""
    // Inside the bar's centre group it shows whenever there is a reading; on its own, narrow bars drop it,
    // together with media, before any status icon. `suppressed`: the centre group already shows it.
    property bool embedded: false
    property bool suppressed: false
    property bool shown: hasLocation && degrees !== "" && !suppressed && (embedded || (host.width >= 1280 && !host.crowded))
    labelSize: Theme.barText
    implicitHeight: Math.min(Theme.barControlHeight, Theme.barHeight)
    flat: true
    leftPadding: 10
    rightPadding: 10
    hint: !weather ? "" : I18n.t("bar.temperature", {city: location.name, degrees: degrees + "C"})
        + (weather.current && weather.current.label ? "\n" + I18n.weatherCondition(weather.current.label) : "")
        + (stale ? "\n" + I18n.t("bar.outdated") + " · " + I18n.t("bar.updatedAt", {time: updated}) : "")
    tooltip: host.tooltip
    Accessible.name: I18n.t("bar.temperatureName", {degrees: degrees + "C"}) + (stale ? I18n.t("bar.temperatureOutdated", {time: updated}) : "")
    selected: ShellState.panel === "calendar" && ShellState.screenName === host.outputName
    // The weather card lives in the calendar.
    onClicked: ShellState.toggle("calendar", host.outputName)

    function refresh() {
        if (!hasLocation || !Backend.ready)
            return;
        const request = ++generation;
        // Celsius regardless of the card's own unit choice.
        Backend.request("weather.get", {location: location, units: "metric"}, (result, error) => {
            if (request !== generation)
                return;
            // A failed request keeps the last reading; with none, the item stays hidden.
            if (!error && result && result.current)
                weather = result;
        });
    }
    readonly property string locationKey: hasLocation ? location.latitude + "," + location.longitude : ""
    onLocationKeyChanged: {
        weather = null;
        refresh();
    }
    Component.onCompleted: refresh()
    Connections {
        target: Backend
        function onReadyChanged() {
            if (Backend.ready)
                root.refresh();
        }
    }
    Timer {
        interval: 10 * 60 * 1000
        repeat: true
        running: root.hasLocation
        onTriggered: root.refresh()
    }
    // Icon and reading as one group centred in the capsule. The width is measured on the widest reading
    // ("-00°C"), so it doesn't jump between values, while loading or when the reading goes stale.
    contentItem: Item {
        implicitWidth: group.implicitWidth
        implicitHeight: group.implicitHeight
        TextMetrics {
            id: widest
            text: "-00°C"
            font.family: Theme.mono
            font.pixelSize: Theme.barText
        }
        RowLayout {
            id: group
            anchors.centerIn: parent
            spacing: Theme.gapXs
            C.Icon {
                name: root.weather && root.weather.current && root.weather.current.icon ? root.weather.current.icon : "cloud-sun"
                size: Theme.barIconSize - 2
                color: root.stale ? Theme.muted : Theme.text
                Layout.alignment: Qt.AlignVCenter
            }
            C.Label {
                // Degrees and unit never split: one word, no wrapping.
                text: root.degrees !== "" ? root.degrees + "C" : "--°C"
                color: root.stale ? Theme.muted : Theme.text
                font.family: Theme.mono
                font.pixelSize: Theme.barText
                wrapMode: Text.NoWrap
                horizontalAlignment: Text.AlignHCenter
                Layout.preferredWidth: Math.ceil(widest.advanceWidth)
                Layout.alignment: Qt.AlignVCenter
            }
        }
        // Out of date: a small warning dot over the corner, explained in the tooltip; it takes no width.
        Rectangle {
            visible: root.stale
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.rightMargin: -4
            width: 5
            height: 5
            radius: 2.5
            color: Theme.warning
        }
    }
}
