import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../core"
import "../services"
import "../components" as C
import "../core/i18n.js" as I18n

// Security Mode as one block of the control center (docs/architecture/seguranca-contratos.md): the mode's own
// state from security.status (desativado … restaurando) with a switch, and, folded below, what the backend
// reported about the connection. Details are read-only and secondary; nothing here runs a command or guesses:
// whatever the backend doesn't confirm reads "Não foi possível verificar". Activating and deactivating each
// ask first; the backend allows one operation at a time.
C.Card {
    id: root
    Layout.fillWidth: true
    implicitHeight: column.implicitHeight + 2 * Theme.gapM

    // Until the first overview arrives (or fails or runs out of time), a missing value is still on its way.
    readonly property bool overviewPending: !answers.overview && !failures.overview && !overviewTimedOut
    readonly property string unverified: overviewPending ? "Verificando…" : "Não foi possível verificar"
    // Section -> read contract. Each answers {state, checkedAt, stale, capabilities, data, warnings}.
    readonly property var reads: ({
        overview: "security.overview",
        dns: "security.dns.audit",
        split: "security.split.list",
        media: "security.media.status",
        ports: "security.ports.list",
        lan: "security.lan.get"
    })
    property var answers: ({})
    property var failures: ({})
    property var loading: ({})
    // "", "activate" or "deactivate": the confirmation on screen.
    property string confirming: ""
    property string busy: ""
    property var actionFailure: null
    // {step, stepState} from security.changed while activating or restoring.
    property var progress: null
    // The mode itself, from security.status; "" until it has been read.
    property string modeState: ""
    // Why the mode is unavailable, when the helper or the daemon gave a reason.
    property string modeReason: ""
    property bool overviewTimedOut: false
    // Answers older than the last request sent are dropped: a late reply never replaces a newer one.
    property var sequence: ({})
    // The mode shown as "Indisponível" after this long without an answer, instead of waiting forever.
    readonly property int answerDeadline: 15000
    property var openPorts: ({})
    property date now: new Date()

    // The states of docs/MODO-DE-SEGURANCA.md, each with a short human explanation. The
    // backend's older names read as their new ones (preparando … verificando = ativando, restaurando = desativando).
    readonly property var modes: ({
        desativado: {text: "Desativado", glyph: "shield", tone: Theme.muted, why: "Tráfego normal, sem a Mullvad"},
        ativando: {text: "Ativando", glyph: "refresh", tone: Theme.accentText, why: "Bloqueando vazamentos e conectando à Mullvad"},
        protegido: {text: "Protegido", glyph: "shield", tone: Theme.success, why: "Tráfego protegido pela Mullvad"},
        // No exceptions are offered any more (maximum security): if the backend still reports one, it is said
        // as an alert, never as protected.
        protegido_com_excecao: {text: "Protegido", glyph: "shield", tone: Theme.success, why: "Tailscale permitido para Minecraft"},
        protecao_parcial: {text: "Proteção parcial", glyph: "alert", tone: Theme.warning, why: "Uma verificação obrigatória falhou"},
        desativando: {text: "Desativando", glyph: "refresh", tone: Theme.accentText, why: "Restaurando a conexão normal"},
        indisponivel: {text: "Indisponível", glyph: "close", tone: Theme.muted, why: modeReason || "Não foi possível consultar a Mullvad"},
        falha_fechada: {text: "Falha fechada", glyph: "lock", tone: Theme.danger, why: "Internet bloqueada para evitar vazamento"},
        erro: {text: "Erro", glyph: "close", tone: Theme.danger, why: "Falhou sem garantia de bloqueio"}
    })
    readonly property var aliases: ({preparando: "ativando", bloqueando: "ativando", conectando: "ativando", verificando: "ativando",
                                     restaurando: "desativando"})
    readonly property string mode2: aliases[modeState] || modeState
    readonly property var lockdown: answers.overview && answers.overview.data && answers.overview.data.mullvad ? answers.overview.data.mullvad.lockdown : undefined
    // What the block says. With the mode settled on, the words come from the overview:
    // "Protegido" only when every check passed and nothing runs outside the tunnel; partial is "Proteção parcial" with its short reason.
    // Falha fechada only while the lockdown isn't known to be off: an old snapshot never makes it.
    readonly property string overviewState: answers.overview ? String(answers.overview.state || "") : ""
    readonly property string shownState: {
        if (mode2 === "falha_fechada" && lockdown === false)
            return "erro";
        if (["protegido", "protegido_com_excecao", "protecao_parcial"].indexOf(mode2) < 0)
            return mode2;
        if (overviewState === "protected_with_exception" || overviewState === "protegido_com_excecao")
            return "protegido_com_excecao";
        if (overviewState === "protected" || overviewState === "protegido")
            return mode2 === "protecao_parcial" ? "protegido" : mode2;
        if (overviewState === "fail_closed" || overviewState === "falha_fechada")
            return lockdown === false ? "erro" : "falha_fechada";
        if (overviewState === "")
            return "ativando";
        return "protecao_parcial";
    }
    // The short reason for partial protection, from the overview's own data and warnings.
    readonly property string partialReason: {
        const info = answers.overview && answers.overview.data || {};
        const reasons = [];
        if (info.dns && info.dns.result && ["sem_vazamento", "confirmado"].indexOf(info.dns.result) < 0)
            reasons.push("DNS não confirmado");
        if (info.split && info.split.enabled === true)
            reasons.push("aplicativos fora da VPN");
        if (info.external && info.external.status && info.external.status !== "confirmado")
            reasons.push("verificação externa não confirmada");
        if (info.mullvad && info.mullvad.lockdown === false)
            reasons.push("lockdown desligado");
        if (!reasons.length && answers.overview && (answers.overview.warnings || []).length)
            reasons.push(answers.overview.warnings[0].message || answers.overview.warnings[0].code);
        return reasons.join(", ");
    }
    readonly property var mode: modes[shownState] || {text: modeLoading ? "Verificando…" : "Indisponível", glyph: "shield", tone: Theme.muted,
                                                       why: modeLoading ? "" : modeReason || "Não foi possível consultar o estado"}
    readonly property bool stepping: shownState === "ativando" || shownState === "desativando" || busy === "activate" || busy === "deactivate"
    readonly property bool modeOn: ["ativando", "protegido", "protegido_com_excecao", "protecao_parcial", "falha_fechada", "desativando"].indexOf(mode2) >= 0
    // The last known state, said as such when the backend marks it stale.
    property bool modeStale: false
    readonly property bool stale: modeStale || !!(answers.overview && answers.overview.stale)
    readonly property string subtitle: (shownState === "protecao_parcial" && partialReason ? partialReason.charAt(0).toUpperCase() + partialReason.slice(1) : mode.why || "")
        + (stale ? (mode.why ? " · " : "") + "dados desatualizados" : "")
    readonly property bool open: ShellState.securityExpanded
    // Opening the details scrolls the control center so the block's top is in view (details below the fold were
    // easy to miss). Only the nearest scrolling area moves; nothing outside the panel does.
    function reveal() {
        let area = parent;
        while (area && (area.contentY === undefined || area.contentHeight === undefined))
            area = area.parent;
        if (!area)
            return;
        const top = root.mapToItem(area.contentItem, 0, 0).y;
        area.contentY = Math.max(0, Math.min(top - Theme.gapS, area.contentHeight - area.height));
    }

    function isEmpty(value) {
        return value === undefined || value === null || value === "" || value === "unknown";
    }
    function show(value) {
        return isEmpty(value) ? unverified : value === true ? "Sim" : value === false ? "Não" : String(value);
    }
    function yesNo(value, yes, no) {
        return value === true ? (yes || "Sim") : value === false ? (no || "Não") : unverified;
    }
    function errorText(failure) {
        const known = ({dependency_missing: "Dependência ausente", permission_denied: "Permissão negada", timeout: "Tempo esgotado",
                        daemon_unavailable: "Serviço da Mullvad indisponível", logged_out: "Mullvad sem login", incompatible: "Versão da Mullvad não suportada",
                        unsupported: "Não suportado", verification_failed: "A verificação falhou", busy: "Outra operação de segurança está em andamento"})[failure && failure.code];
        return !failure ? "" : known ? known + (failure.message ? ": " + failure.message : "") : failure.message || I18n.error(failure) || unverified;
    }
    function checkedText(answer) {
        const at = answer && answer.checkedAt ? new Date(answer.checkedAt) : null;
        if (!at || isNaN(at.getTime()))
            return unverified;
        const minutes = Math.max(0, Math.floor((now.getTime() - at.getTime()) / 60000));
        const age = minutes < 1 ? "agora" : minutes < 60 ? "há " + minutes + " min" : "há " + Math.floor(minutes / 60) + " h";
        return I18n.date(at, "HH:mm") + " · " + age + (answer.stale ? " · desatualizado" : "");
    }
    function tunnelText(value) {
        return ({desconectado: "Desconectado", conectando: "Conectando…", conectado: "Conectado", bloqueado: "Bloqueado", erro: "Erro"})[value] || unverified;
    }
    function relayText(relay) {
        if (!relay)
            return unverified;
        const place = [relay.city, relay.country].filter(part => !isEmpty(part)).join(", ");
        return [place, relay.protocol].filter(part => !isEmpty(part)).join(" · ") || unverified;
    }
    function stepText(step) {
        return ({preparando: "Preparando", bloqueando: "Bloqueando o tráfego", lan: "Aplicando a rede local", relay: "Escolhendo o servidor",
                 dns: "Configurando o DNS", conectando: "Conectando", verificando: "Verificando", restaurando: "Restaurando",
                 concluido: "Concluído", falha_fechada: "Bloqueado para evitar vazamento"})[step] || "";
    }
    function dnsStateText(state) {
        return ({protegido: "protegido", permitido: "permitido", informativo: "informativo", inativo: "inativo", inconclusivo: "inconclusivo",
                 confirmado: "confirmado", alerta: "alerta", falha: "falha", vazamento: "vazamento"})[state] || (state ? state : unverified);
    }
    function dnsStateColor(state) {
        return ["protegido", "confirmado"].indexOf(state) >= 0 ? Theme.success : ["alerta", "falha", "vazamento"].indexOf(state) >= 0 ? Theme.danger
            : state === "inconclusivo" ? Theme.warning : Theme.muted;
    }
    function dataOf(key) {
        return answers[key] && answers[key].data || null;
    }

    function nextSequence(key) {
        const next = Object.assign({}, sequence);
        next[key] = (next[key] || 0) + 1;
        sequence = next;
        return next[key];
    }
    property var dirty: ({})
    function read(key) {
        if (!Backend.ready)
            return;
        if (loading[key]) {
            const marked = Object.assign({}, dirty);
            marked[key] = true;
            dirty = marked;
            return;
        }
        const nextLoading = Object.assign({}, loading);
        nextLoading[key] = true;
        loading = nextLoading;
        const mine = nextSequence(key);
        Backend.request(reads[key], key === "dns" ? {external: false} : {}, (result, failure) => {
            if (mine !== sequence[key])
                return;
            if (key === "overview")
                overviewTimedOut = false;
            const doneLoading = Object.assign({}, loading);
            doneLoading[key] = false;
            loading = doneLoading;
            if (dirty[key]) {
                const cleared = Object.assign({}, dirty);
                cleared[key] = false;
                dirty = cleared;
                Qt.callLater(() => root.read(key));
            }
            const nextFailures = Object.assign({}, failures);
            nextFailures[key] = failure || null;
            failures = nextFailures;
            if (failure)
                return;
            if (key === "overview") {
                mergeOverview(result);
                return;
            }
            const next = Object.assign({}, answers);
            next[key] = result;
            answers = next;
        });
    }
    // While the answer is on its way (the backend serialises Mullvad calls, so it can wait seconds behind another
    // read) the block says so and keeps the last state it knew; only a failed read is "Não foi possível verificar".
    property bool modeLoading: false
    // Overview answers and security.changed events land here. Events carry only what changed (a step, the DNS,
    // the external check), and verify's names the Mullvad summary data.local where the overview says
    // data.mullvad (same shape). Each is merged field by field into the last overview, so one that lacks the
    // summary never erases it, whatever order they arrive in.
    function mergeOverview(answer) {
        if (!answer)
            return;
        const incoming = Object.assign({}, answer.data || {});
        if (incoming.local && !incoming.mullvad)
            incoming.mullvad = incoming.local;
        delete incoming.local;
        const previous = answers.overview || {};
        const next = Object.assign({}, answers);
        next.overview = Object.assign({}, previous, answer, {data: Object.assign({}, previous.data || {}, incoming)});
        answers = next;
    }
    // Set when something changed while a read was on its way: it is read once more when that one ends.
    property bool modeDirty: false
    function readMode() {
        if (!Backend.ready)
            return;
        if (modeLoading) {
            modeDirty = true;
            return;
        }
        modeLoading = true;
        modeDeadline.restart();
        const mine = nextSequence("mode");
        Backend.request("security.status", {}, (result, failure) => {
            if (mine !== sequence.mode)
                return;
            modeLoading = false;
            modeDeadline.stop();
            if (modeDirty) {
                modeDirty = false;
                readMode();
            }
            if (failure)
                console.warn("SecurityBlock: security.status failed:", failure.code, failure.message);
            if (!failure && result) {
                modeState = String(result.state || "") || "indisponivel";
                modeReason = String(result.message || result.reason || "");
                modeStale = result.stale === true;
            } else {
                // A failed read is the mode being unreachable, with its reason; it never keeps the screen waiting.
                modeState = "indisponivel";
                modeReason = failure ? errorText(failure) : "Sem resposta do serviço";
            }
        });
    }
    Timer {
        id: modeDeadline
        interval: root.answerDeadline
        onTriggered: {
            // No answer in time: the request is abandoned (a later reply is dropped) and the reason is said.
            root.nextSequence("mode");
            root.modeLoading = false;
            root.modeState = "indisponivel";
            root.modeReason = "A Mullvad não respondeu no prazo";
        }
    }
    Timer {
        id: overviewDeadline
        interval: 45000
        running: root.loading.overview === true
        onTriggered: {
            root.overviewTimedOut = true;
            root.nextSequence("overview");
            const next = Object.assign({}, root.loading);
            next.overview = false;
            root.loading = next;
        }
    }
    // The summary and the mode always; the rest only while the details are open.
    function readAll() {
        readMode();
        read("overview");
        if (open)
            for (const key of ["dns", "split", "media", "ports", "lan"])
                read(key);
    }
    Timer {
        id: revealLater
        interval: 60
        onTriggered: root.reveal()
    }
    onOpenChanged: if (open) {
        revealLater.restart();
        readAll();
    }
    function act(kind, method, params) {
        if (busy !== "" || !Backend.ready)
            return;
        confirming = "";
        busy = kind;
        actionFailure = null;
        progress = null;
        Backend.request(method, params, (result, failure) => {
            busy = "";
            // A refused or failed operation answers {accepted: false, error, message}: said as it is, never as success.
            actionFailure = failure || (result && result.accepted === false ? {code: result.error || "failed", message: result.message || ""} : null);
            readAll();
        });
    }
    function activate() {
        act("activate", "security.activate", {confirm: true});
    }
    function deactivate() {
        act("deactivate", "security.deactivate", {confirm: true});
    }
    function copyGuidance(text) {
        Backend.request("clipboard.write", {text: text}, (result, failure) =>
            ShellState.message(failure ? I18n.error(failure) : "Orientação copiada", failure ? "error" : "success"));
    }

    Component.onCompleted: {
        readAll();
        if (open)
            revealLater.restart();
    }
    Connections {
        target: Backend
        function onReadyChanged() {
            if (Backend.ready)
                root.readAll();
        }
        function onEventReceived(name, payload) {
            if (name === "security.changed" && payload) {
                const info = payload.data || {};
                root.progress = info.step ? {step: info.step, stepState: info.stepState || ""} : root.progress;
                root.mergeOverview(payload);
                root.readMode();
            } else if (name === "security.state" && payload) {
                // The mode changed (or was recovered after a restart): both reads are repeated, not trusted from the event.
                root.readAll();
            } else if (name === "session.resumed") {
                root.readAll();
            } else if (name === "security.media.changed" && root.open) {
                root.read("media");
            }
        }
    }
    // Opening the control center reads again: what was true when it was last open may be stale.
    Connections {
        target: ShellState
        function onPanelChanged() {
            if (ShellState.panel === "control")
                root.readAll();
        }
    }
    Timer {
        interval: 30000
        repeat: true
        running: true
        onTriggered: root.now = new Date()
    }
    Timer {
        interval: 60000
        repeat: true
        running: true
        onTriggered: root.readAll()
    }

    // "name   value" in small text.
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
    // A confirmation or warning: tinted, with its buttons inside.
    component Notice: Rectangle {
        id: notice
        property color tone: Theme.warning
        property string body: ""
        default property alias extra: noticeColumn.data
        Layout.fillWidth: true
        implicitHeight: noticeColumn.implicitHeight + 20
        radius: Theme.controlRadius
        color: Theme.mix(Theme.surface, tone, 0.12)
        border.width: 1
        border.color: tone
        Accessible.role: Accessible.AlertMessage
        Accessible.name: body
        ColumnLayout {
            id: noticeColumn
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: 10
            spacing: Theme.gapS
            C.Label { text: notice.body; font.pixelSize: Theme.small; wrapMode: Text.WordWrap; Layout.fillWidth: true }
        }
    }

    ColumnLayout {
        id: column
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: Theme.gapM
        spacing: Theme.gapS

        // Header: shield in the state's colour, name, state in words and the switch.
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.gapM
            Rectangle {
                Layout.preferredWidth: 42
                Layout.preferredHeight: 42
                radius: 21
                color: Theme.mix(Theme.surface, root.mode.tone, 0.18)
                C.Icon {
                    id: shieldIcon
                    anchors.centerIn: parent
                    name: root.mode.glyph
                    size: Theme.iconSize
                    color: root.mode.tone
                    // Breathes gently while a step is under way.
                    SequentialAnimation on opacity {
                        running: root.stepping && !Theme.reduced
                        loops: Animation.Infinite
                        onRunningChanged: if (!running) shieldIcon.opacity = 1
                        NumberAnimation { to: 0.35; duration: 700; easing.type: Easing.InOutSine }
                        NumberAnimation { to: 1; duration: 700; easing.type: Easing.InOutSine }
                    }
                }
            }
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 1
                // The title has the line to itself; the state goes under it, beside the subtitle.
                C.Label { text: "Modo de Segurança"; font.weight: Font.DemiBold; elide: Text.ElideRight; Layout.fillWidth: true }
                C.Label {
                    readonly property string stateText: root.busy === "activate" ? "Ativando…" : root.busy === "deactivate" ? "Desativando…" : root.mode.text
                    readonly property string detail: root.stepping && root.progress && root.stepText(root.progress.step) !== "" ? "Etapa: " + root.stepText(root.progress.step)
                        : root.subtitle
                    text: "<font color=\"" + root.mode.tone + "\">" + stateText + "</font>" + (detail ? " · " + detail : "")
                    textFormat: Text.StyledText
                    color: Theme.muted
                    font.pixelSize: Theme.small
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                    Accessible.name: stateText + (detail ? ". " + detail : "")
                }
            }
            // The switch: on asks to deactivate, off asks to activate; disabled while the mode is unknown or busy.
            C.Button {
                id: modeSwitch
                flat: true
                implicitWidth: 46
                implicitHeight: 32
                enabled: root.busy === "" && ["", "ativando", "desativando", "indisponivel"].indexOf(root.mode2) < 0
                hint: root.modeOn ? "Desativar o Modo de Segurança" : "Ativar o Modo de Segurança"
                Accessible.role: Accessible.CheckBox
                Accessible.checkable: true
                Accessible.checked: root.modeOn
                onClicked: root.confirming = root.modeOn ? "deactivate" : "activate"
                contentItem: Item {
                    Rectangle {
                        anchors.centerIn: parent
                        width: 34
                        height: 20
                        radius: 10
                        color: root.modeOn ? Theme.accent : Theme.hover
                        opacity: modeSwitch.enabled ? 1 : Theme.disabledOpacity
                        Behavior on color { ColorAnimation { duration: Theme.fast } }
                        Rectangle {
                            x: root.modeOn ? 17 : 3
                            y: 3
                            width: 14
                            height: 14
                            radius: 7
                            color: root.modeOn ? Theme.background : Theme.muted
                            Behavior on x { NumberAnimation { duration: Theme.fast; easing.type: Easing.OutCubic } }
                        }
                    }
                }
            }
        }

        // A thin indeterminate bar while the steps run.
        Rectangle {
            visible: root.stepping
            Layout.fillWidth: true
            implicitHeight: 2
            radius: 1
            color: Theme.hover
            clip: true
            Rectangle {
                id: runner
                width: parent.width * 0.3
                height: parent.height
                radius: 1
                color: root.mode.tone
                NumberAnimation on x {
                    running: root.stepping && !Theme.reduced
                    loops: Animation.Infinite
                    from: -runner.width
                    to: runner.parent.width
                    duration: 1400
                }
            }
        }

        Notice {
            visible: root.confirming === "activate"
            body: "O Modo de Segurança bloqueará a rede local, removerá exceções da VPN e poderá interromper conexões atuais. Continuar?"
            RowLayout {
                spacing: Theme.gapS
                C.Button { text: "Ativar"; accent: true; onClicked: root.activate() }
                C.Button { text: "Cancelar"; flat: true; onClicked: root.confirming = "" }
            }
        }
        Notice {
            visible: root.confirming === "deactivate"
            body: "Desativar o Modo de Segurança poderá expor novamente seu IP e seu tráfego normal. Continuar?"
            RowLayout {
                spacing: Theme.gapS
                C.Button { text: "Desativar"; danger: true; onClicked: root.deactivate() }
                C.Button { text: "Cancelar"; flat: true; onClicked: root.confirming = "" }
            }
        }
        Notice {
            visible: root.shownState === "falha_fechada" && root.confirming === ""
            tone: Theme.danger
            body: "Falha fechada — internet bloqueada para evitar vazamento" + (root.actionFailure ? ". " + root.errorText(root.actionFailure) : "")
            RowLayout {
                spacing: Theme.gapS
                C.Button { text: "Tentar novamente"; accent: true; enabled: root.busy === ""; onClicked: root.activate() }
                C.Button { text: "Desativar e restaurar"; enabled: root.busy === ""; onClicked: root.confirming = "deactivate" }
            }
        }
        Notice {
            visible: root.actionFailure !== null && root.shownState !== "falha_fechada"
            tone: Theme.danger
            body: "A operação não foi concluída: " + root.errorText(root.actionFailure)
        }

        C.Button {
            text: root.open ? "Ocultar detalhes" : "Detalhes"
            glyph: "chevron"
            flat: true
            implicitHeight: Theme.controlHeightCompact
            Layout.alignment: Qt.AlignRight
            onClicked: ShellState.securityExpanded = !ShellState.securityExpanded
        }

        // Details: informational only, scrolling inside a bounded area so the card keeps its size and the control
        // center doesn't jump when they open or update.
        Flickable {
            id: detailScroll
            visible: root.open
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(details.implicitHeight, 300)
            contentWidth: width
            contentHeight: details.implicitHeight
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            interactive: contentHeight > height
            ScrollBar.vertical: C.ScrollBar {}
        ColumnLayout {
            id: details
            width: detailScroll.width - (detailScroll.interactive ? 10 : 0)
            spacing: 3
            readonly property var overviewData: root.dataOf("overview") || ({})
            readonly property var info: overviewData.mullvad || null
            readonly property var external: overviewData.external || null
            readonly property var dns: root.dataOf("dns") || details.overviewData.dns || ({})
            // One line per origin: identical lines (the same resolver over IPv4 and IPv6) show once.
            readonly property var dnsLines: (Array.isArray(dns.lines) ? dns.lines : []).filter((line, index, all) =>
                all.findIndex(other => other.origem === line.origem && other.resumo === line.resumo && other.estado === line.estado) === index)
            readonly property var split: root.dataOf("split") || ({})
            readonly property var lan: root.dataOf("lan") || ({})
            readonly property var ports: root.dataOf("ports") && root.dataOf("ports").items || []
            readonly property int flagged: ports.filter(port => port.level === "atencao" || port.level === "atencao_elevada").length
            Fact {
                name: "IP público"
                value: details.external && !root.isEmpty(details.external.ipMasked)
                    ? details.external.ipMasked + (details.external.status === "confirmado" ? (details.external.isMullvad === true ? " · Mullvad" : details.external.isMullvad === false ? " · não é Mullvad" : "") : "")
                    : details.info && !root.isEmpty(details.info.ipMasked) ? details.info.ipMasked : root.unverified
            }
            Fact { name: "Servidor e país"; value: details.info && details.info.tunnel === "desconectado" && !details.info.relay ? "Nenhum (túnel desconectado)" : root.relayText(details.info && details.info.relay) }
            Fact { name: "Túnel"; value: root.failures.overview ? root.errorText(root.failures.overview) : root.tunnelText(details.info && details.info.tunnel) }
            Fact { name: "Lockdown"; value: root.yesNo(details.info && details.info.lockdown, "Ligado", "Desligado") }
            // DNS by origin, one line each (origem — resumo — estado), from data.dns.lines; never a raw address list.
            C.Label { text: "DNS"; font.weight: Font.DemiBold; font.pixelSize: Theme.caption; Layout.topMargin: 2 }
            Repeater {
                model: details.dnsLines
                RowLayout {
                    id: dnsLine
                    required property var modelData
                    readonly property string level: String(modelData.estado || "")
                    Layout.fillWidth: true
                    spacing: Theme.gapS
                    Accessible.role: Accessible.StaticText
                    Accessible.name: [modelData.origem, modelData.resumo || modelData.finalidade, root.dnsStateText(level)].filter(part => part).join(", ")
                    C.Label { text: String(dnsLine.modelData.origem || root.unverified); color: Theme.muted; font.pixelSize: Theme.caption; Layout.preferredWidth: 140; elide: Text.ElideRight }
                    C.Label {
                        text: String(dnsLine.modelData.resumo || dnsLine.modelData.finalidade || "") + " — "
                            + "<font color=\"" + root.dnsStateColor(dnsLine.level) + "\">" + root.dnsStateText(dnsLine.level) + "</font>"
                        textFormat: Text.StyledText
                        font.pixelSize: Theme.caption
                        wrapMode: Text.WordWrap
                        Layout.fillWidth: true
                    }
                }
            }
            C.Label {
                visible: details.dnsLines.length === 0
                text: root.failures.dns ? root.errorText(root.failures.dns) : root.loading.dns || !root.answers.dns ? "Verificando…" : root.unverified
                color: Theme.muted
                font.pixelSize: Theme.caption
                Layout.leftMargin: 146
            }

            Fact {
                name: "Rede local"
                value: root.failures.lan ? root.errorText(root.failures.lan)
                    : ({bloqueado: "Bloqueada", permitido: "Permitida", aplicando: "Aplicando…", nao_suportado: "Não suportado", erro: "Erro"})[details.lan.state] || root.unverified
            }
            Fact {
                name: "Apps fora da VPN"
                value: root.failures.split ? root.errorText(root.failures.split)
                    : details.split.enabled === false ? "Nenhum (divisão de túnel desligada)"
                    : typeof details.split.count === "number" ? String(details.split.count) + ((details.split.items || []).length ? ": " + details.split.items.map(item => item.app || item.exe || "?").join(", ") : "") : root.unverified
            }
            Fact {
                name: "Câmera e microfone"
                value: !Services.captureMonitored ? "Monitoramento indisponível"
                    : [Services.micUsers.length ? "Microfone: " + Services.micUsers.join(", ") : "",
                       Services.cameraUsers.length ? "Câmera: " + Services.cameraUsers.join(", ") : ""].filter(line => line).join(" · ")
                      || "Nenhum uso visto pelo PipeWire. Isso não prova que não há captura."
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.gapS
                Fact {
                    name: "Serviços em escuta"
                    value: root.failures.ports ? root.errorText(root.failures.ports)
                        : !root.answers.ports ? root.unverified
                        : details.ports.length + " serviço(s)" + (details.flagged ? " · " + details.flagged + " merecem atenção" : "")
                }
                C.Button {
                    visible: details.ports.length > 0
                    text: root.openPorts.all ? "Menos" : "Ver"
                    flat: true
                    implicitHeight: Theme.controlHeightCompact
                    onClicked: root.openPorts = Object.assign({}, root.openPorts, {all: !root.openPorts.all})
                }
            }
            Repeater {
                model: root.openPorts.all ? details.ports : []
                ColumnLayout {
                    id: port
                    required property var modelData
                    readonly property var evidence: modelData.evidence || ({})
                    Layout.fillWidth: true
                    Layout.leftMargin: Theme.gapM
                    spacing: 1
                    C.Label {
                        text: (({baixo: "Baixo", atencao: "Atenção", atencao_elevada: "Atenção elevada", nao_verificado: "Não verificado"})[port.modelData.level] || root.unverified)
                            + " · " + (root.isEmpty(port.modelData.service) ? root.show(port.modelData.app) : port.modelData.service)
                            + " · " + String(port.evidence.proto || "").toUpperCase() + " " + root.show(port.evidence.address) + ":" + root.show(port.evidence.port)
                        color: port.modelData.level === "atencao_elevada" ? Theme.danger : port.modelData.level === "atencao" ? Theme.warning : Theme.text
                        font.pixelSize: Theme.caption
                        elide: Text.ElideRight
                        Layout.fillWidth: true
                    }
                    C.Label { visible: !root.isEmpty(port.modelData.guidance); text: String(port.modelData.guidance || ""); color: Theme.muted; font.pixelSize: Theme.caption; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                    C.Button {
                        visible: !root.isEmpty(port.modelData.guidance)
                        text: "Copiar orientação"
                        glyph: "copy"
                        flat: true
                        implicitHeight: Theme.controlHeightCompact
                        onClicked: root.copyGuidance(String(port.modelData.guidance))
                    }
                }
            }
            Fact { name: "Última verificação"; value: root.checkedText(root.answers.overview) }
        }
        }
    }
}
