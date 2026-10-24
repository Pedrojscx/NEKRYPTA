# Contrato de rede — Gate 6

Fonte única: `Backend.wifi` e os métodos `wifi.*` já existentes. O backend continua
usando o controlador do NetworkManager; o QML apenas chama `Backend.request` e não
executa comandos.

Quando `python-dbus-next` não está instalado, o mesmo controlador usa um fallback
`nmcli` com vetores explícitos. `wifi.list` é então totalmente funcional em leitura;
`scan`, `connect`, `disconnect` e `power` também usam somente os subcomandos
correspondentes do NetworkManager, sem senha no backend e sem `sh -c`.

## `wifi.list`

Consulta somente leitura. Retorna:

```json
{
  "available": true,
  "type": "wifi",
  "state": "conectado",
  "connectivity": "full",
  "powered": true,
  "ssid": "Rede-exemplo",
  "signal": 87,
  "ip": "192.168.x.x",
  "networks": [
    {"ssid":"Rede-exemplo","signal":87,"security":"protegida","known":true,"connected":true,"connecting":false},
    {"ssid":"Rede-aberta","signal":42,"security":"aberta","known":false,"connected":false,"connecting":false}
  ]
}
```

`type` é `wifi`, `cabo` ou `nenhum`. `state` é `wifi_desativado`, `procurando`,
`desconectado`, `conectando`, `conectado`, `sem_internet`, `cabo`,
`nm_indisponivel` ou `erro`. `connectivity` é `full`, `limited`, `portal`, `none`
ou `unknown`. O IP é mascarado no contrato (`x.x`); SSID é dado de interface e não
é escrito em logs.

Compatibilidade: `name`, `type` (classificação antiga `open`/`secured`/`8021x`),
`path`, `saved` e `connected` permanecem nos itens antigos quando disponíveis;
`ssid`, `security` e `known` são os campos canônicos novos.

## `wifi.scan`

Pede uma varredura ao NetworkManager, sem credenciais:

```json
{"accepted":true,"state":"procurando"}
```

O próximo `wifi.list` contém os resultados. Não há garantia de conclusão síncrona.

## `wifi.connect`

Parâmetro: `{ "path": "..." }` para uma rede já descoberta. Não aceita senha.
Perfil conhecido usa `nmcli connection up`/ativação equivalente do NetworkManager.
Rede desconhecida usa `nmcli device wifi connect <ssid>` sem senha: o agente de
segredos do NetworkManager solicita a credencial. Sem agente, retorna
`secret_agent_required`. O SSID é passado como um argumento separado, nunca por
`sh -c`.

Só retorna sucesso após releitura real confirmar o estado `conectado`:

```json
{"done":true,"state":"conectado","ssid":"Rede-exemplo","connectivity":"full"}
```

## `wifi.disconnect`

Sem parâmetros sensíveis. Desconecta o dispositivo Wi-Fi e confirma a releitura:

```json
{"done":true,"state":"desconectado"}
```

## `wifi.power`

Parâmetro `{ "powered": true|false }`. Usa o NetworkManager existente e retorna a
confirmação lida:

```json
{"done":true,"powered":false,"state":"wifi_desativado"}
```

## Erros

`nm_indisponivel` é estado de leitura quando o serviço não existe. Falhas de ação
usam os códigos estáveis `unavailable`, `secret_agent_required`, `failed`,
`timeout` ou `invalid`. Nenhuma senha aparece em resposta, exceção ou log.

## Estado do visualizer

O processo CAVA continua sendo controlado exclusivamente por `helpers/visualizer.py`.
O backend calcula `carregando`, `aguardando_reproducao`, `reproduzindo`, `sem_audio`,
`indisponivel` e `erro` quando a informação é do processo. O frontend/MPRIS calcula
`pausado`, `sem_player` e `sem_audio` quando a ausência vem do player; nesses casos
ele pode manter o CAVA parado. A contagem de demanda, o processo único e o
encerramento SIGTERM→2 s→SIGKILL permanecem no backend.
