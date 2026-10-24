pragma Singleton
import QtQuick

QtObject {
    // Every colour, size and font family of the look comes from core/personal.json.
    readonly property var surfaces: PersonalConfig.palette.surfaces
    readonly property color background: surfaces.background
    readonly property color panel: surfaces.panel
    readonly property color surface: surfaces.surface
    // Shape: panels, then cards and large controls, then small controls (buttons up to 36 px, chips, keycaps).
    readonly property int panelRadius: 16
    readonly property int controlRadius: 12
    readonly property int radiusSmall: 8
    readonly property int panelPadding: 20
    // Spacing steps; everything between controls and sections uses one of these.
    readonly property int gapXs: 4
    readonly property int gapS: 8
    readonly property int gapM: 12
    readonly property int gapL: 16
    readonly property int gapXl: 24
    readonly property int sectionSpacing: gapL
    // Control heights: compact (inline and header buttons), standard, and large (fields, list rows).
    readonly property int controlSmall: 28
    readonly property int controlLarge: 44
    readonly property int iconSmall: 14
    readonly property int icon: 18
    readonly property int iconLarge: 20
    readonly property real disabledOpacity: 0.45
    readonly property int titleSize: 18
    property color accent: String(ShellState.setting("accentColor", PersonalConfig.palette.accent))
    // Accent changes (for example from a new wallpaper) cross-fade instead of snapping.
    Behavior on accent {
        ColorAnimation {
            duration: Theme.reduced ? 0 : 450
            easing.type: Easing.InOutQuad
        }
    }
    readonly property real accentHue: accent.hslHue < 0 ? 0 : accent.hslHue
    readonly property real selectionSaturation: accent.hslSaturation < 0.05 ? 0 : Math.max(0.25, Math.min(0.38, accent.hslSaturation * 0.72))
    readonly property color selection: Qt.hsla(accentHue, selectionSaturation, 0.23, 1)
    readonly property color selectionHover: Qt.hsla(accentHue, selectionSaturation, 0.29, 1)
    // Control sizes (gate 6): standard 40, large 44 in panels, compact 36, and the bar's 34 px drawing (its
    // buttons stay at least 40 px wide to click). Icons 20, or 18 in compact controls.
    readonly property int controlHeight: 40
    readonly property int controlHeightLarge: 44
    readonly property int controlHeightCompact: 36
    readonly property int barControlHeight: PersonalConfig.bar.controlHeight
    // Gate 6 (continuação): the bar grew 35 -> 38 px (+8.6%); capsules, pills, covers and icons grow with it
    // (37, 26, 30, 21), while text, widths and the gaps between groups stay as they were.
    readonly property int barHeight: PersonalConfig.bar.height
    readonly property int barIconSize: PersonalConfig.bar.iconSize
    readonly property int barHitTarget: PersonalConfig.bar.hitTarget
    readonly property int iconSize: 20
    readonly property int iconSizeCompact: 18
    readonly property int controlPaddingX: 12
    readonly property int controlPaddingY: 8
    // Wallpaper tones (D1 fallback) for the bar's ambient layer and the music visualizer. Fixed colours: they
    // change only with the palette, never per frame.
    readonly property color wallpaperSky: PersonalConfig.palette.wallpaper.sky
    readonly property color wallpaperCloud: PersonalConfig.palette.wallpaper.cloud
    readonly property color wallpaperPetroleum: PersonalConfig.palette.wallpaper.petroleum
    readonly property color wallpaperReflection: PersonalConfig.palette.wallpaper.reflection
    readonly property color wallpaperHighlight: PersonalConfig.palette.wallpaper.highlight
    // Visualizer bars: bluish white tops over a cyan base, readable on the charcoal shade below them.
    readonly property color visualizerTop: PersonalConfig.palette.visualizer.top
    readonly property color visualizerBase: PersonalConfig.palette.visualizer.base
    readonly property color raised: surfaces.raised
    readonly property color hover: surfaces.hover
    readonly property color border: surfaces.border
    readonly property color text: surfaces.text
    readonly property color muted: surfaces.muted
    readonly property color accentHover: Qt.hsla(accentHue, accent.hslSaturation, Math.min(1, accent.hslLightness + 0.08), 1)
    // Keyboard focus outline: the accent clears 3:1 against every surface; the D1 blue is the strong fill.
    readonly property color focusRing: contrast(accent, surface) >= 3 ? accent : text
    readonly property color accentStrong: PersonalConfig.palette.accentStrong
    readonly property color accentText: contrast(accent, selection) >= 4.5 ? accent : text
    readonly property color disabled: surfaces.disabled
    readonly property color danger: PersonalConfig.palette.status.danger
    readonly property color success: PersonalConfig.palette.status.success
    readonly property color warning: PersonalConfig.palette.status.warning
    // Adwaita when installed; otherwise fontconfig's own sans and monospace (keeps clock digits fixed-width).
    readonly property var installedFonts: Qt.fontFamilies()
    readonly property string font: installedFonts.indexOf(PersonalConfig.typography.sans) >= 0 ? PersonalConfig.typography.sans : "sans-serif"
    readonly property string mono: installedFonts.indexOf(PersonalConfig.typography.mono) >= 0 ? PersonalConfig.typography.mono : "monospace"
    readonly property int body: ShellState.setting("fontSize", 14)
    // Secondary sizes follow the chosen text size so large text scales everywhere.
    readonly property int label: Math.max(12, body - 1)
    readonly property int small: Math.max(11, body - 2)
    readonly property int caption: Math.max(10, body - 3)
    // In-page titles and headline numbers.
    readonly property int heading: Math.max(20, body + 6)
    // The bar keeps its own fixed sizes so its 32 px height holds at every text size.
    readonly property int barText: PersonalConfig.typography.barText
    readonly property bool reduced: ShellState.setting("reducedMotion", false)
    // Power saver always trims effects; the setting keeps them trimmed in every mode.
    readonly property int fast: reduced ? 0 : 120
    readonly property int enter: reduced ? 0 : 220
    readonly property int leave: reduced ? 0 : 160
    function luminance(color) {
        const channel = value => value <= 0.04045 ? value / 12.92 : Math.pow((value + 0.055) / 1.055, 2.4);
        return 0.2126 * channel(color.r) + 0.7152 * channel(color.g) + 0.0722 * channel(color.b);
    }
    function contrast(first, second) {
        const a = luminance(first), b = luminance(second);
        return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
    }
    function mix(first, second, fraction) {
        return Qt.rgba(first.r * (1 - fraction) + second.r * fraction,
                       first.g * (1 - fraction) + second.g * fraction,
                       first.b * (1 - fraction) + second.b * fraction, 1);
    }
    // Slider and progress fills.
    readonly property color fill: mix(selection, accent, 0.70)
    readonly property color activityLevel1: selection
    readonly property color activityLevel2: mix(selection, accent, 0.35)
    readonly property color activityLevel3: fill
    readonly property color activityIdle: mix(raised, selection, 0.65)
    readonly property color activityWindow: mix(raised, accent, 0.45)
    // Watching (video, no input) reads warm against the cool active accent.
    readonly property color activityWatching: mix(raised, warning, 0.78)
    // Distinct, equally muted hues for telling apps apart on one timeline, kept clear of the accent and the
    // watching amber; the last is for "other".
    readonly property var activityApps: [accent, mix(raised, Qt.rgba(0.56, 0.71, 0.89, 1), 0.86), mix(raised, Qt.rgba(0.78, 0.64, 0.83, 1), 0.86),
                                         mix(raised, Qt.rgba(0.88, 0.60, 0.60, 1), 0.86), mix(raised, Qt.rgba(0.74, 0.80, 0.52, 1), 0.86),
                                         mix(raised, Qt.rgba(0.70, 0.68, 0.94, 1), 0.86), mix(raised, muted, 0.55)]
}
