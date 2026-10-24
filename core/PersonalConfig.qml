pragma Singleton
import QtQuick
import Quickshell.Io
import "paths.js" as Paths

// This rice's fixed configuration, read from core/personal.json: the same file the helper reads, so the look and
// the behaviour have one source. Nothing here is saved or changed while the shell runs.
QtObject {
    id: root
    property FileView file: FileView {
        path: Paths.localPath(Qt.resolvedUrl("personal.json"))
        blockLoading: true
    }
    readonly property var data: {
        try {
            return JSON.parse(file.text());
        } catch (e) {
            console.warn("PersonalConfig: personal.json could not be read:", e);
            return {bindings: {}, operationalDefaults: {}};
        }
    }
    readonly property var palette: data.palette || ({})
    readonly property var typography: data.typography || ({})
    readonly property var bar: data.bar || ({})
    readonly property var bindings: data.bindings || ({})
    readonly property var operationalDefaults: data.operationalDefaults || ({})
    readonly property var shortcuts: data.shortcuts || []

    function at(path) {
        let value = data;
        for (const part of path.split(".")) {
            if (value === undefined || value === null)
                return undefined;
            value = value[part];
        }
        return value;
    }
    // A flat setting name fixed by this configuration ("fontSize", "barLayout"), or undefined.
    function fixed(key) {
        return bindings[key] === undefined ? undefined : at(bindings[key]);
    }
    function isFixed(key) {
        return bindings[key] !== undefined;
    }
}
