import QtQuick
import QtQuick.Controls
import "../core"

TextField {
    color: enabled ? Theme.text : Theme.disabled
    placeholderTextColor: Theme.disabled
    font.family: Theme.font
    font.pixelSize: Theme.body
    selectionColor: Theme.selectionHover
    selectedTextColor: Theme.text
    implicitHeight: Theme.controlLarge
    leftPadding: Theme.gapM + 2
    rightPadding: Theme.gapM + 2
    selectByMouse: true
    verticalAlignment: TextInput.AlignVCenter
    Accessible.name: placeholderText
    background: Rectangle {
        radius: Theme.controlRadius
        color: parent.activeFocus ? Theme.raised : Theme.surface
        // Typing focus shows as the same ring buttons use.
        border.width: parent.activeFocus ? 2 : 0
        border.color: Theme.focusRing
        Behavior on color {
            ColorAnimation {
                duration: Theme.fast
            }
        }
    }
}
