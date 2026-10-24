import QtQuick
import QtQuick.Layouts
import "../core"
import "../core/i18n.js" as I18n

ColumnLayout {
    id: root
    property string title: I18n.t("Nothing here yet")
    property string description: ""
    property string glyph: "grid"
    spacing: Theme.gapS
    GlyphTile {
        size: 48
        glyph: root.glyph
        glyphColor: Theme.muted
        color: Theme.surface
        radius: Theme.controlRadius
        Layout.alignment: Qt.AlignHCenter
    }
    Label {
        text: title
        font.pixelSize: Theme.body + 2
        font.weight: Font.DemiBold
        Layout.fillWidth: true
        horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.WordWrap
    }
    Label {
        visible: root.description !== ""
        text: description
        color: Theme.muted
        wrapMode: Text.WordWrap
        horizontalAlignment: Text.AlignHCenter
        Layout.fillWidth: true
    }
}
