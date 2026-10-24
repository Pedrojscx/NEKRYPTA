# Centro de Segurança — contratos do backend

Contrato Gate 6 para o painel **Modo de Segurança**. O QML apenas chama
`Backend.request`; comandos, D-Bus, PipeWire, portais e leitura de rede ficam
nos helpers. Nenhum valor desconhecido é convertido em uma proteção presumida:
o texto exibido deve ser **Não foi possível verificar**.

## Resposta comum

Toda operação de leitura retorna:

```json
{
  "state": "protected|warning|partial|failed|fail_closed|unavailable",
  "checkedAt": "2026-10-20T12:00:00+00:00",
  "stale": false,
  "capabilities": {},
  "data": {},
  "warnings": []
}
```

`checkedAt` é ISO-8601 com fuso. `stale` é verdadeiro quando a resposta veio
de cache vencido ou quando alguma invariável não pôde ser relida. `warnings` é
uma lista de objetos `{code, message, details?}` independentes do estado geral.
Campos ausentes, `null` ou `unknown` significam não verificado.

Estados do modo e textos da interface: `desativado` — Desativado;
`preparando` — Preparando; `bloqueando` — Bloqueando vazamentos;
`conectando` — Conectando à Mullvad; `verificando` — Verificando proteção;
`protegido` — Protegido; `protecao_parcial` — Proteção parcial;
`falha_fechada` — Falha fechada; `erro` — Erro; `restaurando` — Restaurando.

Erros usam `{code, message, retryable, details}`. `message` é pt-BR e os
códigos são estáveis: `dependency_missing`, `permission_denied`, `timeout`,
`unsupported`, `verification_failed` ou códigos já documentados em
`error-codes.md`.

## Operação e concorrência

Existe uma única operação de segurança ativa por vez. `overview`, `verify`,
auditorias e listas podem compartilhar snapshots somente leitura; `activate`,
`deactivate`, `lan.set` e `media.revoke` usam o lock do controlador Mullvad e
retornam `busy` quando uma operação mutável já está em andamento. Reinício sem
snapshot confiável mantém `fail_closed` e exige ação manual; nunca há palpite.

## Contratos

### `security.overview`

Parâmetros: `{}`. Retorna a resposta comum com `data` contendo `tunnel`,
`mullvad`, `dns`, `ports`, `media`, `split`, `lan` e `alerts`.
`protected` exige todas as invariantes locais e externas confirmadas. `warning`
indica split tunnel, escuta ampla, mic/câmera, auditoria velha ou diagnóstico
incompleto; `partial` indica túnel correto sem verificação completa; `failed`
indica contradição externa/DNS ou modo desligado quando deveria estar ativo.
Quando o modo está inativo e o túnel está desconectado, o estado é
`unavailable` (e o aviso `Modo de Segurança desativado`); não é uma falha.
`external.status` é `confirmado` quando o túnel local desconectado coincide com
uma saída não-Mullvad, ou quando ambos confirmam Mullvad. Só é
`contraditorio` quando essas evidências divergem;
`fail_closed` indica lockdown ativo sem túnel.

### `security.verify`

Parâmetros: `{ "manual": false }`. Releia CLI/daemon, túnel, relay, lockdown,
LAN, DNS, split e teste externo permitido. `manual:true` ignora apenas o TTL do
cache externo, não ignora falhas. `data` contém os mesmos diagnósticos factuais
sem conta, chave, token ou IP público em logs.

### `security.activate`

Parâmetros: `{ "confirm": true, "country": "cc?" }`; `confirm` é obrigatório.
As etapas são emitidas nesta ordem: checar Mullvad; guardar somente lockdown,
LAN, exclusões split e conexão que serão alterados; lockdown; LAN block; detectar
split; remover cada PID excluído; connect com espera de 45 s; confirmar túnel
local; confirmar IP externo Mullvad; auditar DNS; finalizar o estado. O modo não
altera relay, DNS configurado ou auto-connect nesta etapa.
`protected` exige lockdown on, túnel conectado, IP externo Mullvad, LAN block,
split vazio e DNS local sem vazamento. Sem teste externo oficial, a evidência
local só permite `protecao_parcial` se não for possível confirmar DNS Mullvad
no túnel. Qualquer falha depois do lockdown retorna `fail_closed`, mantém o
bloqueio e inclui `retryable:true` e `Desativar e restaurar`.

### `security.deactivate`

Parâmetros obrigatórios: `{ "confirm": true }`. Restaura apenas o que a sessão
alterou: LAN anterior, exclusões cujos PIDs ainda existem, conexão anterior e
lockdown anterior por último, sempre com releitura. O snapshot transacional fica
em `XDG_STATE_HOME/nekrypta/security-session.json` (permissões 0600); não é backup de configuração.
Se falhar, mantém lockdown e retorna `fail_closed`/`failed`; nunca desliga
silenciosamente.

### `security.dns.audit`

Parâmetros: `{ "external": false }`. `data.local` descreve resolvectl,
`resolv.conf`, interface, rota, DNS customizado Mullvad e DoH conhecido; `data`
externo só é preenchido por método oficial/documentado. Resultado factual é
`sem_vazamento`, `vazamento`, `inconclusivo`, `indisponivel` ou `nao_executado`.
Resolvedores aparecem somente na resposta, nunca em log.

### `security.ports.list`

Parâmetros: `{}`. Reutiliza `exposure.audit` e retorna `data.items` com
`scope` (`loopback`, `lan`, `all`), classificação, motivo, serviço/app/PID,
orientação segura e evidência. `loopback` é baixo; IP LAN é atenção;
`0.0.0.0`/`::` é atenção elevada; processo desconhecido é não verificado.
Nenhuma ação de fechar é oferecida.

### `security.media.status`

Parâmetros: `{}`. Retorna fluxos detectados por PipeWire sob demanda e portais,
com `kind`, app, appId, PID, início, origem e `portal`. Declara cobertura:
PipeWire monitorado, portal revogável e ALSA/V4L2 direto limitado. Ausência de
dados é `Não foi possível verificar`, não ausência de captura.

### `security.media.revoke`

Parâmetros obrigatórios: `{ "confirm": true, "id": "fluxo" }`. Usa
PermissionStore quando suportado, relê o fluxo e informa se a mudança afeta
somente acessos futuros. Sem portal retorna `unsupported`; nunca mata processo.

### `security.split.list`

Parâmetros: `{}`. Somente leitura. `data` contém PIDs excluídos, app,
executável, appId, estado, `detectado_em`, PIDs que sumiram e PIDs não
associados.

### `security.lan.get`

Parâmetros: `{}`. Retorna estado `bloqueado`, `permitido`, `aplicando`,
`nao_suportado`, `desconhecido` ou `erro`, com evidência Mullvad factual.
Audita avahi, samba, nfs, ssh e kdeconnect sem alterar nenhum serviço.

### `security.lan.set`

Parâmetros obrigatórios: `{ "blocked": true|false, "confirm": true }`.
Valida, aplica pela CLI Mullvad permitida, relê e retorna o estado confirmado.
Não usa nftables, não altera firewall e falha fechado quando o bloqueio não pode
ser confirmado.

## Eventos push

O backend emite `security.changed` após cada transição ou releitura relevante,
com a resposta comum completa, e `security.media.changed` quando um fluxo
PipeWire/portal aparece, desaparece ou muda de permissão. Eventos nunca
incluem conta Mullvad, token, chave, resolvedor detalhado em log ou IP público
sem mascaramento. `security.status`, `security.enable`, `security.restore` e
`exposure.audit` permanecem aliases compatíveis dos contratos novos, sem um
segundo controlador ou coletor.

## Schema de campos e exemplos

Os exemplos abaixo são normativos. O IP público é sempre mascarado no backend
antes de chegar à UI (`198.51.100.xxx`); o endereço completo nunca entra em
logs. `null` significa desconhecido, e não desligado.

### `security.overview`

`data.mullvad` é um resumo factual:

| Chave | Tipo | Valores |
|---|---|---|
| `daemon` | bool ou null | daemon confirmado ativo/inativo |
| `tunnel` | string | `desconectado`, `conectando`, `conectado`, `bloqueado`, `erro`, `unknown` |
| `isMullvad` | bool ou null | saída/estado confirmado pela Mullvad |
| `ipMasked` | string ou null | IPv4/IPv6 mascarado ou null |
| `relay` | objeto ou null | `{direction, country, city, protocol, hostnameMasked?}` |
| `incomingRelay` | objeto ou null | mesmo formato, ou null |
| `lockdown` | bool ou null | valor lido, nunca inferido |

`relay.direction` é `saida` ou `entrada`; país/cidade/protocolo são strings ou
null. `hostnameMasked` é opcional e nunca contém o hostname integral. O parser
usa `details.location.mullvad_exit_ip`, `hostname`, `entry_hostname` e
`details.locked_down` do `mullvad status -j`; latitude e longitude são
descartadas. `data.lan` mantém o formato de `lan.get` (`state`, `value`, `services`),
e `data.split` é o resumo compatível de `split.list` (`enabled`, `count`,
`items`, `vanished`, `unassociated`). Assim o painel pode mostrar o resumo sem
perder a forma dos métodos detalhados.

Exemplo:

```json
{"data":{"mullvad":{"daemon":true,"tunnel":"conectado","isMullvad":true,"ipMasked":"198.51.100.xxx","relay":{"direction":"saida","country":"Suécia","city":"Stockholm","protocol":"wireguard"},"incomingRelay":null,"lockdown":true},"lan":{"state":"bloqueado","value":"block","services":[]},"split":{"enabled":false,"count":0,"items":[],"vanished":[],"unassociated":[]}},"state":"protected","checkedAt":"2026-10-20T12:00:00+00:00","stale":false,"capabilities":{},"warnings":[]}
```

### `security.verify`

`data.local` usa os mesmos objetos factuais de `overview`; `data.external` é
`{isMullvad: bool|null, ipMasked: string|null, country: string|null, city:
string|null, organization: string|null}`. `data.external.status` é
`confirmado`, `contraditorio`, `inconclusivo` ou `indisponivel`. Quando
`mullvad_exit_ip` for booleano, ele só alimenta `isMullvad`; nenhum IP público
é devolvido sem mascaramento.

```json
{"data":{"local":{"isMullvad":true,"tunnel":"conectado"},"external":{"status":"confirmado","isMullvad":true,"ipMasked":"203.0.113.xxx","country":"Suécia","city":"Stockholm","organization":"Mullvad VPN"}},"state":"partial","checkedAt":"2026-10-20T12:00:00+00:00","stale":false,"capabilities":{"external":true},"warnings":[]}
```

### `security.activate` e `security.deactivate`

Além da resposta comum, retornam `data.changedKeys` (lista de strings entre
`lockdown`, `lan`, `relay`, `dns`, `tunnel`) e `data.snapshotAvailable` (bool).
Eventos `security.changed` durante activate têm `data.step` com um destes
valores: `preparando`, `bloqueando`, `lan`, `relay`, `dns`, `conectando`,
`verificando`, `restaurando`, `concluido`, `falha_fechada`; `data.stepState` é
`iniciando`, `confirmado`, `falhou` ou `pulando`. `data.lockdown` é sempre o
valor lido ou `null`/`unknown`.

```json
{"state":"fail_closed","checkedAt":"2026-10-20T12:00:00+00:00","stale":false,"capabilities":{"mullvad":true},"data":{"changedKeys":["lockdown","lan"],"snapshotAvailable":true,"step":"bloqueando","stepState":"falhou","lockdown":true},"warnings":[{"code":"verification_failed","message":"Tráfego bloqueado para evitar vazamento"}]}
```

### `security.dns.audit`

`data.local` tem exatamente `{servers: string[], interface: string|null,
route: string|null, customDns: bool|null, doh: string|null}`. Os servidores
somente aparecem na resposta. `data.result` é
`sem_vazamento|vazamento|inconclusivo|indisponivel|nao_executado`.
`data.external` é `{available: bool, result: string, checkedAt: string|null,
officialUrl: "https://mullvad.net/en/check"}`. A URL vem do backend para que a
UI não precise duplicá-la; quando não há método oficial automático, o resultado
é `indisponivel` e a URL permanece disponível para abertura manual.

```json
{"state":"warning","checkedAt":"2026-10-20T12:00:00+00:00","stale":false,"capabilities":{"local":true,"external":false},"data":{"local":{"servers":["10.0.0.1"],"interface":"wg-mullvad","route":"default","customDns":false,"doh":null},"result":"inconclusivo","external":{"available":false,"result":"indisponivel","checkedAt":null,"officialUrl":"https://mullvad.net/en/check"}},"warnings":[]}
```

### `security.ports.list`

Cada `data.items[]` tem `{scope, level, reason, service, app, pid, guidance,
evidence}`. `scope` é `loopback|lan|all|unknown`; `level` é
`baixo|atencao|atencao_elevada|nao_verificado|desconhecido`. `service`, `app`
e `pid` podem ser null. `evidence` é `{address: string, port: int, proto:
string}`. `guidance` é texto pt-BR de orientação segura, nunca uma ação.

```json
{"state":"warning","checkedAt":"2026-10-20T12:00:00+00:00","stale":false,"capabilities":{"ss":true},"data":{"items":[{"scope":"all","level":"atencao_elevada","reason":"O serviço está escutando em todas as interfaces.","service":"sshd","app":"sshd","pid":42,"guidance":"Revise a configuração oficial do serviço; não o feche por aqui.","evidence":{"address":"0.0.0.0","port":22,"proto":"tcp"}}]},"warnings":[]}
```

### `security.media.status` e `security.media.revoke`

Cada item de `data.items[]` tem `{id: string, kind: camera|microphone, app:
string|null, appId: string|null, pid: int|null, startedAt: string|null,
origin: pipewire|portal|alsa|v4l2|unknown, portal: bool}`. A cobertura está em
`capabilities` como `{pipewire: bool, portal: bool, alsa_v4l2: "limitado"|
"indisponivel"}`. Ausência de itens não prova ausência de captura.

`media.revoke` retorna `data: {id: string, revoked: bool, futureOnly: bool,
verified: bool|null}`. Sem PermissionStore: erro `unsupported`, mensagem
`Bloqueio individual não suportado`.

```json
{"state":"warning","checkedAt":"2026-10-20T12:00:00+00:00","stale":false,"capabilities":{"pipewire":true,"portal":true,"alsa_v4l2":"limitado"},"data":{"items":[{"id":"pipewire:77","kind":"microphone","app":"Recorder","appId":"org.example.Recorder","pid":77,"startedAt":"2026-10-20T11:59:00+00:00","origin":"portal","portal":true}]},"warnings":[]}
```

### `security.split.list`

`data.items[]` tem `{pid: int, app: string|null, exe: string|null, appId:
string|null, state: string, detectedAt: string}`. `data.vanished[]` contém
`{pid: int, detectedAt: string}` e `data.unassociated[]` contém
`{exe: string|null, app: string|null, appId: string|null, detectedAt: string}`.
`data.enabled` é bool ou null e `data.count` é int ou null.

```json
{"state":"warning","checkedAt":"2026-10-20T12:00:00+00:00","stale":false,"capabilities":{"mullvad":true},"data":{"enabled":true,"count":1,"items":[{"pid":812,"app":"Browser","exe":"/usr/bin/browser","appId":"org.example.Browser","state":"ativo","detectedAt":"2026-10-20T12:00:00+00:00"}],"vanished":[],"unassociated":[]},"warnings":[]}
```

### `security.lan.get` e `security.lan.set`

`data` tem `{state: bloqueado|permitido|aplicando|nao_suportado|desconhecido|
erro, value: block|allow|null, services: [{name: string, active: bool|null,
port: int|null}]}`. A lista de serviços audita `avahi`, `samba`, `nfs`, `ssh` e
`kdeconnect`. `lan.set` recebe `{blocked: bool, confirm: true}` e devolve
exatamente o mesmo schema após releitura; `aplicando` só pode aparecer em evento
de progresso.

```json
{"state":"protected","checkedAt":"2026-10-20T12:00:00+00:00","stale":false,"capabilities":{"mullvad":true},"data":{"state":"bloqueado","value":"block","services":[{"name":"ssh","active":true,"port":22}]},"warnings":[]}
```

### Diagnósticos

O centro alimenta diagnósticos em `overview.warnings` para a visão agregada e
em `verify.data` para evidência factual local/externa. Auditorias detalhadas
mantêm seus próprios `warnings`; o FrontEnd não deve deduzir falha a partir de
lista vazia. Em qualquer campo indisponível, mostrar `Não foi possível
verificar`.

## Tailscale exclusivo para o Minecraft

A política `allowTailscaleMinecraft=true` é fixa em `core/personal.json`; não existe método para alterá-la.
A ativação nunca executa `tailscale down`, não desliga `tailscaled`, não altera `--accept-dns` e não
habilita Exit Node. O backend só lê o estado (`tailscale status --json`, `tailscale debug prefs`,
`ip route show default`, `resolvectl status`) e conclui:

| Exceção | Condição | Estado do modo |
|---|---|---|
| `inativo` | Tailscale parado ou ausente | `protegido` |
| `permitido` | ativo, sem Exit Node, sem rota padrão pelo Tailscale e MagicDNS restrito à tailnet | `protegido_com_excecao` |
| `nao_comprovado` | alguma dessas leituras falhou ou o MagicDNS não está restrito | `protecao_parcial` |
| `bloqueado` | Exit Node ou rota padrão pelo Tailscale | ativação recusada com `tailscale_exception`, nada é alterado |

`data.tailscale.exception` leva esse valor em `security.verify` e `security.overview`
(`state: "protected_with_exception"` no resumo). O MagicDNS (`100.100.100.100` e `fd7a:115c:a1e0::53`) não é
vazamento quando limitado à interface `tailscale0`, aos domínios da tailnet e sem rota padrão.

## Leituras estáveis

`security.status` responde sempre, em até 12 s: se o daemon não responder, devolve
`{"state":"indisponivel","reason":"timeout","message":…}`. Durante uma ativação ou restauração ele responde da
memória (`inProgress:true`) em vez de esperar a CLI. `security.overview` tem prazo de 40 s e, durante uma
operação, devolve `data.busy:true`. O evento `session.resumed` pede nova leitura após a suspensão.
