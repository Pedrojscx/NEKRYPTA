import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../core"
import "." as C
import "../core/i18n.js" as I18n

ColumnLayout {
    id: root
    property string title: ""
    property string glyph: "volume"
    property real value: 0
    property real maximum: 1
    // A muteable row turns its icon into a mute toggle; muted keeps the real level visible.
    property bool muteable: false
    property bool muted: false
    property string mutedGlyph: "mute"
    // With a hint, a trailing chevron opens this level's detail page.
    property string detailHint: ""
    // One 48 px row instead of two: mute, name, slider and percentage side by side, in columns that line up
    // from row to row (the audio page puts outputs, inputs and apps in one grid this way).
    property bool inline: false
    // With a handler, the whole row takes wheel and touchpad scrolling as 5% steps (scrolled) instead of the
    // slider's own wheel; the page behind doesn't scroll.
    property bool scrollable: false
    signal scrolled(int steps)
    signal moved(real value)
    signal iconClicked()
    signal detailRequested()
    spacing: Theme.gapXs
    opacity: enabled ? 1 : Theme.disabledOpacity
    RowLayout {
        visible: !root.inline
        Layout.fillWidth: true
        C.Icon {
            visible: !root.muteable
            name: root.glyph
        }
        C.Button {
            visible: root.muteable
            glyph: root.muted ? root.mutedGlyph : root.glyph
            flat: true
            selected: root.muted
            opacity: root.muted && root.mutedGlyph === root.glyph ? 0.55 : 1
            implicitWidth: Theme.controlHeightCompact
            implicitHeight: Theme.controlHeightCompact
            Layout.leftMargin: -6
            Layout.topMargin: -4
            Layout.bottomMargin: -4
            hint: (root.muted ? I18n.t("Unmute ") : I18n.t("Mute ")) + root.title.toLowerCase()
            Accessible.role: Accessible.CheckBox
            Accessible.checkable: true
            Accessible.checked: root.muted
            onClicked: root.iconClicked()
        }
        C.Label {
            text: root.title + (root.muted ? I18n.t(" · muted") : "")
            color: root.muted ? Theme.muted : Theme.text
            Layout.fillWidth: true
        }
        C.Label {
            text: Math.round(root.shownValue * 100) + "%"
            color: Theme.muted
            font.family: Theme.mono
        }
        C.Button {
            visible: root.detailHint !== ""
            glyph: "chevron"
            flat: true
            hint: root.detailHint
            implicitWidth: Theme.controlHeightCompact
            implicitHeight: Theme.controlHeightCompact
            Layout.rightMargin: -4
            Layout.topMargin: -4
            Layout.bottomMargin: -4
            onClicked: root.detailRequested()
        }
    }
    component LevelSlider: Slider {
        id: slider
        // An inline component can't see the row's ids, so the row hands these in.
        property bool slim: false
        property bool muted: false
        from: 0
        stepSize: 0.05
        // In the slim grid the page scrolls under the wheel; levels move by drag, click or the arrow keys.
        wheelEnabled: !slim && !scrollable
        property bool scrollable: false
        implicitHeight: Theme.controlHeightCompact
        background: Rectangle {
            x: slider.leftPadding
            y: (slider.height - height) / 2
            width: slider.availableWidth
            height: slider.slim ? 6 : 10
            radius: height / 2
            color: Theme.raised
            Rectangle {
                width: slider.visualPosition * parent.width
                height: parent.height
                radius: parent.radius
                color: slider.muted ? Theme.hover : Theme.fill
                Behavior on color {
                    ColorAnimation {
                        duration: Theme.fast
                    }
                }
                Behavior on width {
                    enabled: !slider.pressed
                    NumberAnimation {
                        duration: Theme.fast
                        easing.type: Easing.OutCubic
                    }
                }
            }
        }
        handle: Rectangle {
            x: slider.leftPadding + slider.visualPosition * (slider.availableWidth - width)
            y: (slider.height - height) / 2
            width: slider.slim ? 16 : slider.pressed ? 18 : 14
            height: width
            radius: width / 2
            color: slider.muted ? Theme.muted : Theme.accent
            border.width: 0
            // Keyboard focus: a ring around the handle (arrow keys move it).
            Rectangle {
                anchors.centerIn: parent
                width: parent.width + 8
                height: parent.height + 8
                radius: width / 2
                visible: slider.visualFocus
                color: "transparent"
                border.width: 2
                border.color: Theme.focusRing
            }
            Behavior on width {
                NumberAnimation {
                    duration: Theme.fast
                }
            }
            Behavior on color {
                ColorAnimation {
                    duration: Theme.fast
                }
            }
        }
    }
    C.WheelSteps {
        enabled: root.scrollable && root.enabled
        onStepped: steps => root.scrolled(steps)
    }
    readonly property real shownValue: stacked.pressed || row.pressed ? (stacked.pressed ? stacked.value : row.value) : root.value
    LevelSlider {
        id: stacked
        slim: root.inline
        scrollable: root.scrollable
        muted: root.muted
        to: root.maximum
        value: root.value
        Accessible.name: root.title
        onMoved: root.moved(value)
        visible: !root.inline
        Layout.fillWidth: true
    }
    RowLayout {
        id: inlineRow
        visible: root.inline
        Layout.fillWidth: true
        Layout.preferredHeight: 48
        spacing: Theme.gapS
        C.Button {
            glyph: root.muteable && root.muted ? root.mutedGlyph : root.glyph
            flat: true
            enabled: root.muteable
            selected: root.muted
            opacity: root.muted && root.mutedGlyph === root.glyph ? 0.55 : 1
            implicitWidth: Theme.controlHeight
            implicitHeight: Theme.controlHeight
            hint: (root.muted ? I18n.t("Unmute ") : I18n.t("Mute ")) + root.title
            Accessible.name: hint
            Accessible.role: Accessible.CheckBox
            Accessible.checkable: true
            Accessible.checked: root.muted
            onClicked: root.iconClicked()
        }
        // Long device and app names elide; the full name shows on hover.
        C.Label {
            id: name
            text: root.title
            color: root.muted ? Theme.muted : Theme.text
            elide: Text.ElideRight
            Layout.fillWidth: true
            Layout.minimumWidth: 60
            HoverHandler { id: nameHover }
            ToolTip.visible: nameHover.hovered && name.truncated
            ToolTip.text: root.title + (root.muted ? I18n.t(" · muted") : "")
            ToolTip.delay: 500
        }
        LevelSlider {
            id: row
        slim: root.inline
        scrollable: root.scrollable
        muted: root.muted
        to: root.maximum
        value: root.value
        Accessible.name: root.title
        onMoved: root.moved(value)
            Layout.preferredWidth: 200
            Layout.minimumWidth: 180
            Layout.preferredHeight: Theme.controlHeight
        }
        C.Label {
            text: Math.round(root.shownValue * 100) + "%"
            color: Theme.muted
            font.family: Theme.mono
            horizontalAlignment: Text.AlignRight
            Layout.preferredWidth: 48
            Layout.minimumWidth: 48
        }
    }
}
