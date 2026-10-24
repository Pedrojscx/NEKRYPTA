import QtQuick
import QtQuick.Layouts
import "../core"
import "../services"
import "../components" as C
import "../core/i18n.js" as I18n

// The network page of the control center, opened from the Rede tile's arrow like Bluetooth's
// (docs/architecture/rede-contratos.md): state, SSID and signal, connectivity and the Wi-Fi switch, then the masked
// local IP, a one-line Mullvad summary (the Security Mode block has the rest), the Wi-Fi networks and
// NetworkManager's VPN profiles. Everything comes from ShellState.wifi (wifi.list) and the wifi.* actions; nothing
// reads "connected" before the backend confirms it. Passwords never pass through here: a new secured network is
// handed to NetworkManager's secret agent by the backend.
ColumnLayout {
    id: root
    Layout.fillWidth: true
    spacing: Theme.gapS

    readonly property string unverified: "Não foi possível verificar"
    readonly property var wifi: ShellState.wifi || ({})
    readonly property string netState: ShellState.networkState
    readonly property var netStates: ({
        wifi_desativado: {text: "Wi‑Fi desligado", glyph: "wifi-off", tone: Theme.muted},
        procurando: {text: "Procurando redes", glyph: "wifi-1", tone: Theme.accentText},
        desconectado: {text: "Desconectado", glyph: "wifi-off", tone: Theme.muted},
        conectando: {text: "Conectando…", glyph: "wifi-2", tone: Theme.accentText},
        conectado: {text: "Conectado", glyph: "wifi", tone: Theme.success},
        sem_internet: {text: "Sem internet", glyph: "alert", tone: Theme.warning},
        cabo: {text: "Cabo conectado", glyph: "link", tone: Theme.success},
        nm_indisponivel: {text: "NetworkManager indisponível", glyph: "close", tone: Theme.muted},
        erro: {text: "Erro", glyph: "alert", tone: Theme.danger}
    })
    readonly property var shown: netStates[netState] || {text: unverified, glyph: "wifi-off", tone: Theme.muted}
    readonly property string kind: wifi.type === "cabo" || netState === "cabo" ? "cabo" : wifi.type === "nenhum" ? "nenhum" : "wifi"
    readonly property var current: (wifi.networks || []).find(network => network.connected) || null
    readonly property string ssid: wifi.ssid || (current ? current.ssid || current.name || "" : "")
    readonly property var signal: typeof wifi.signal === "number" ? wifi.signal : current && typeof current.signal === "number" ? current.signal : null
    readonly property string headerGlyph: kind === "cabo" ? "link" : kind === "nenhum" || netState === "wifi_desativado" || netState === "nm_indisponivel" ? "wifi-off"
        : signal === null ? shown.glyph : signal >= 67 ? "wifi" : signal >= 34 ? "wifi-2" : "wifi-1"
    readonly property string connectivityText: ({full: "Internet funcionando", limited: "Rede limitada", portal: "Login necessário (portal da rede)",
                                                 none: "Sem internet", unknown: unverified})[wifi.connectivity] || unverified
    // Limited, portal or no internet while connected: said in words, not only by colour.
    readonly property string warning: ["conectado", "cabo"].indexOf(netState) >= 0 && ["limited", "portal", "none"].indexOf(wifi.connectivity) >= 0
        ? connectivityText : netState === "sem_internet" ? "Conectado à rede, mas sem acesso à internet" : ""
    readonly property bool wifiOn: wifi.powered === true

    // One action at a time: "power", "scan", "disconnect" or "connect:<path>".
    property string busy: ""
    property string actionError: ""
    // The network row offering "Desconectar" (clicked while connected).
    property string offering: ""
    function act(kind, method, params, done) {
        if (busy !== "" || !Backend.ready)
            return;
        busy = kind;
        actionError = "";
        Backend.request(method, params, (result, failure) => {
            busy = "";
            if (failure)
                actionError = errorText(failure);
            if (done)
                done(result, failure);
            ShellState.refreshWifi();
        });
    }
    function errorText(failure) {
        return ({secret_agent_required: "Esta rede pede senha e o agente de senhas do sistema não está disponível. Conecte-a uma vez pelas configurações de rede do sistema.",
                 unavailable: "NetworkManager indisponível", timeout: "Tempo esgotado ao conectar",
                 failed: "A conexão falhou", invalid: "Esta rede não está mais disponível"})[failure && failure.code]
            || I18n.error(failure) || unverified;
    }
    function setPower(on) {
        act("power", "wifi.power", {powered: on});
    }
    function scan() {
        act("scan", "wifi.scan", {});
    }
    function choose(network) {
        if (network.connected) {
            offering = offering === key(network) ? "" : key(network);
            return;
        }
        offering = "";
        if (!network.path) {
            actionError = "Esta rede não pode ser escolhida por aqui";
            return;
        }
        act("connect:" + network.path, "wifi.connect", {path: network.path});
    }
    function disconnect() {
        offering = "";
        act("disconnect", "wifi.disconnect", {});
    }
    function key(network) {
        return String(network.path || network.ssid || network.name || "");
    }
    function known(network) {
        return network.known === true || network.saved === true;
    }
    function secured(network) {
        return network.security ? network.security !== "aberta" : network.type !== "open";
    }
    // One row per network: access points sharing a name and security show once, as the connected one or the
    // strongest. Then connected > known > signal > name.
    function sorted(list) {
        const best = {};
        for (const network of list || []) {
            const id = String(network.ssid || network.name || network.path || "") + "|" + secured(network);
            const kept = best[id];
            if (!kept || (network.connected === true && kept.connected !== true)
                    || (kept.connected !== true && (network.signal || 0) > (kept.signal || 0)))
                best[id] = network;
        }
        return Object.keys(best).map(id => best[id]).sort((a, b) => (b.connected === true) - (a.connected === true) || known(b) - known(a)
                                          || (b.signal || 0) - (a.signal || 0) || String(a.ssid || a.name || "").localeCompare(String(b.ssid || b.name || "")));
    }
    // The list on screen. Updates wait while the pointer or keyboard focus is in it, so nothing moves under you.
    property var networks: []
    readonly property bool holding: listHover.hovered || networkList.activeFocus
    function refreshList() {
        if (!holding)
            networks = sorted(wifi.networks);
    }
    onWifiChanged: refreshList()
    onHoldingChanged: refreshList()

    // A one-line Mullvad summary from the mode's own read (security.status): the tunnel and the exit, never
    // "Protegido" (that word belongs to the Security Mode block and its full checks).
    property var mullvad: null
    function readMullvad() {
        if (Backend.ready)
            Backend.request("security.status", {}, (result, failure) => mullvad = failure ? null : result);
    }
    readonly property string mullvadText: !mullvad ? "Mullvad: " + unverified
        : mullvad.tunnel === true ? "Mullvad: túnel conectado" + (mullvad.relay && (mullvad.relay.city || mullvad.relay.country) ? " · " + [mullvad.relay.city, mullvad.relay.country].filter(part => part).join(", ") : "")
        : mullvad.tunnel === false ? "Mullvad: túnel desconectado" : "Mullvad: " + unverified

    Component.onCompleted: {
        refreshList();
        ShellState.refreshWifi();
        readMullvad();
    }
    Connections {
        target: Backend
        function onEventReceived(name, payload) {
            if (name === "security.state")
                root.readMullvad();
        }
    }
    // While open, the list follows NetworkManager every few seconds (held while you point at it).
    Timer {
        interval: 5000
        repeat: true
        running: root.busy === ""
        onTriggered: ShellState.refreshWifi()
    }

    component Fact: RowLayout {
        property string name: ""
        property string value: ""
        Layout.fillWidth: true
        spacing: Theme.gapS
        Accessible.role: Accessible.StaticText
        Accessible.name: name + ": " + value
        C.Label { text: parent.name; color: Theme.muted; font.pixelSize: Theme.caption; Layout.preferredWidth: 140; elide: Text.ElideRight }
        C.Label { text: parent.value; font.pixelSize: Theme.caption; wrapMode: Text.WordWrap; Layout.fillWidth: true }
    }

    // The page's top card: state and the Wi-Fi switch.
    C.Card {
        Layout.fillWidth: true
        implicitHeight: column.implicitHeight + 2 * Theme.gapM
    ColumnLayout {
        id: column
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: Theme.gapM
        spacing: Theme.gapS

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.gapM
            Rectangle {
                Layout.preferredWidth: 42
                Layout.preferredHeight: 42
                radius: 21
                color: Theme.mix(Theme.surface, root.shown.tone, 0.18)
                C.Icon {
                    id: networkIcon
                    anchors.centerIn: parent
                    name: root.headerGlyph
                    size: Theme.iconSize
                    color: root.shown.tone
                    SequentialAnimation on opacity {
                        running: (root.netState === "procurando" || root.netState === "conectando" || root.busy !== "") && !Theme.reduced
                        loops: Animation.Infinite
                        onRunningChanged: if (!running) networkIcon.opacity = 1
                        NumberAnimation { to: 0.35; duration: 700; easing.type: Easing.InOutSine }
                        NumberAnimation { to: 1; duration: 700; easing.type: Easing.InOutSine }
                    }
                }
            }
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 1
                C.Label { text: "Rede"; font.weight: Font.DemiBold; elide: Text.ElideRight; Layout.fillWidth: true }
                C.Label {
                    readonly property string detail: [root.ssid, root.signal !== null && root.kind === "wifi" && root.netState === "conectado" ? "sinal " + root.signal + "%" : ""].filter(part => part).join(" · ")
                    text: "<font color=\"" + root.shown.tone + "\">" + root.shown.text + "</font>" + (detail ? " · " + detail : "")
                    textFormat: Text.StyledText
                    color: Theme.muted
                    font.pixelSize: Theme.small
                    elide: Text.ElideRight
                    Layout.fillWidth: true
                    Accessible.name: root.shown.text + (detail ? ". " + detail : "")
                }
            }
            // The Wi-Fi radio; hidden on wired-only or without NetworkManager.
            C.Button {
                id: wifiSwitch
                visible: root.netState !== "nm_indisponivel" && root.kind !== "nenhum"
                flat: true
                implicitWidth: 46
                implicitHeight: 32
                enabled: root.busy === "" && typeof root.wifi.powered === "boolean"
                hint: root.wifiOn ? "Desligar o Wi‑Fi" : "Ligar o Wi‑Fi"
                Accessible.role: Accessible.CheckBox
                Accessible.checkable: true
                Accessible.checked: root.wifiOn
                onClicked: root.setPower(!root.wifiOn)
                contentItem: Item {
                    Rectangle {
                        anchors.centerIn: parent
                        width: 34
                        height: 20
                        radius: 10
                        color: root.wifiOn ? Theme.accent : Theme.hover
                        opacity: wifiSwitch.enabled ? 1 : Theme.disabledOpacity
                        Behavior on color { ColorAnimation { duration: Theme.fast } }
                        Rectangle {
                            x: root.wifiOn ? 17 : 3
                            y: 3
                            width: 14
                            height: 14
                            radius: 7
                            color: root.wifiOn ? Theme.background : Theme.muted
                            Behavior on x { NumberAnimation { duration: Theme.fast; easing.type: Easing.OutCubic } }
                        }
                    }
                }
            }
        }
        C.Label {
            visible: root.warning !== ""
            text: "⚠ " + root.warning
            color: Theme.warning
            font.pixelSize: Theme.small
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
        C.Label {
            visible: root.actionError !== ""
            text: root.actionError
            color: Theme.danger
            font.pixelSize: Theme.small
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
    }
    }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 3
            Fact { name: "Conectividade"; value: root.connectivityText }
            Fact { name: "IP local"; value: root.wifi.ip || root.unverified }
            Fact { name: "Mullvad"; value: root.mullvadText.replace(/^Mullvad: /, "") }

            RowLayout {
                visible: root.wifiOn
                Layout.fillWidth: true
                Layout.topMargin: Theme.gapS
                spacing: Theme.gapS
                C.Label { text: "Redes Wi‑Fi"; font.weight: Font.DemiBold; font.pixelSize: Theme.small; Layout.fillWidth: true }
                C.Button {
                    text: root.busy === "scan" || root.netState === "procurando" ? "Procurando…" : "Procurar redes"
                    glyph: "refresh"
                    flat: true
                    implicitHeight: Theme.controlHeightCompact
                    enabled: root.busy === ""
                    onClicked: root.scan()
                }
            }
            C.Label {
                visible: root.wifiOn && root.networks.length === 0
                text: "Nenhuma rede encontrada"
                color: Theme.muted
                font.pixelSize: Theme.small
            }
            ColumnLayout {
                id: networkList
                visible: root.wifiOn
                Layout.fillWidth: true
                spacing: 2
                HoverHandler { id: listHover }
                Repeater {
                    model: root.networks
                    ColumnLayout {
                        id: row
                        required property var modelData
                        readonly property string rowKey: root.key(modelData)
                        readonly property bool connecting: modelData.connecting === true || root.busy === "connect:" + modelData.path
                        Layout.fillWidth: true
                        spacing: 2
                        C.ListRow {
                            Layout.fillWidth: true
                            implicitHeight: 44
                            topPadding: 2
                            bottomPadding: 2
                            text: row.modelData.ssid || row.modelData.name || "Rede oculta"
                            glyph: (row.modelData.signal || 0) >= 67 ? "wifi" : (row.modelData.signal || 0) >= 34 ? "wifi-2" : "wifi-1"
                            selected: row.modelData.connected === true
                            subtitle: [row.connecting ? "Conectando…" : row.modelData.connected ? "Conectada" : "",
                                       root.secured(row.modelData) ? "Protegida" : "Aberta",
                                       root.known(row.modelData) ? "Conhecida" : "",
                                       typeof row.modelData.signal === "number" ? row.modelData.signal + "%" : ""].filter(part => part).join(" · ")
                            enabled: root.busy === "" || row.connecting
                            hint: row.modelData.connected ? "Opções da rede atual" : "Conectar a " + text
                            onClicked: root.choose(row.modelData)
                        }
                        C.Button {
                            visible: root.offering === row.rowKey && row.modelData.connected === true
                            text: "Desconectar"
                            flat: true
                            implicitHeight: Theme.controlHeightCompact
                            Layout.alignment: Qt.AlignRight
                            onClicked: root.disconnect()
                        }
                    }
                }
            }

            // NetworkManager's own VPN and WireGuard profiles (not Mullvad, which lives in Security Mode).
            C.Label {
                visible: (ShellState.vpn.connections || []).length > 0
                text: "VPN do sistema"
                font.weight: Font.DemiBold
                font.pixelSize: Theme.small
                Layout.topMargin: Theme.gapS
            }
            Repeater {
                model: ShellState.vpn.connections || []
                C.ListRow {
                    required property var modelData
                    Layout.fillWidth: true
                    implicitHeight: 44
                    topPadding: 2
                    bottomPadding: 2
                    text: modelData.name
                    glyph: "lock"
                    selected: modelData.state === "on"
                    subtitle: ({off: "Desligada", connecting: "Conectando…", on: "Conectada", disconnecting: "Desconectando…"})[modelData.state] || ""
                    enabled: root.busy === ""
                    onClicked: root.act("vpn", modelData.state === "off" ? "vpn.connect" : "vpn.disconnect", {path: modelData.path}, () => ShellState.refreshVpn())
                }
            }
        }
}
