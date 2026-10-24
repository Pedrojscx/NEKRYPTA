# Modo de Segurança

O Modo de Segurança integra o shell ao cliente Mullvad para colocar a conexão em um estado fail-closed. Ele é uma automação de configuração local, não uma promessa de anonimato, de segurança absoluta ou de funcionamento independente do daemon da Mullvad.

## Estados

Os estados enviados pelo backend são:

| Estado | Significado |
|---|---|
| `desativado` | O modo não está aplicado. |
| `preparando` | CLI, daemon, login, versão e snapshot estão sendo verificados. |
| `bloqueando` | O lockdown está sendo ativado e a LAN está sendo bloqueada. |
| `conectando` | Relay, DNS e conexão Mullvad estão sendo preparados. |
| `verificando` | O estado pós-conexão está sendo relido e conferido. |
| `protegido` | Todas as invariantes foram confirmadas: lockdown, LAN, DNS padrão, túnel ativo, conexão e relay aprovado; o Tailscale não está em uso. |
| `protegido_com_excecao` | Como `protegido`, e o Tailscale está ativo para o Minecraft: sem Exit Node, sem rota padrão e com o MagicDNS restrito à tailnet. A interface mostra “Tailscale permitido para Minecraft”. |
| `protecao_parcial` | O split tunnel está ativo, o DNS não foi comprovado ou o isolamento do Tailscale não pôde ser comprovado. |
| `indisponivel` | O daemon da Mullvad não respondeu no prazo; a interface mostra “Indisponível” com o motivo. |
| `falha_fechada` | Uma falha ocorreu depois do bloqueio; o modo não libera o lockdown automaticamente. |
| `restaurando` | O snapshot está sendo restaurado, com confirmação por leitura após cada mudança. |
| `erro` | A operação terminou com erro ou com alguma configuração preservada por mudança manual. |

`protegido`, `protegido_com_excecao` e `protecao_parcial` só podem aparecer depois de uma nova leitura coerente do status. Um campo desconhecido ou nulo impede a confirmação.

## O que o modo altera

Ao habilitar, o backend pode executar, nesta ordem:

1. `mullvad lockdown-mode set on` — mantém a rede bloqueada até a proteção ser verificada.
2. `mullvad lan set block` — bloqueia o acesso à rede local.
3. `mullvad relay set location <cc>` — escolhe um país da lista D3.1.
4. `mullvad dns set default` — usa o DNS padrão da Mullvad.
5. `mullvad connect` — conecta a VPN.

Cada mudança relevante é conferida por uma nova leitura. A conexão tem timeout de 45 segundos.

## O que o modo não altera

- Não altera `auto-connect`; esse valor é apenas capturado no snapshot para rastreabilidade.
- Não altera regras de firewall do sistema, serviços de firewall ou políticas fora da CLI da Mullvad.
- Não usa `sudo`, `sh -c` nem comandos arbitrários.
- Não altera split tunnel, DNS customizado, lockdown ou relay por fora dos comandos definidos pelo modo.

## Falha fechada

Qualquer falha depois de iniciar o bloqueio leva a `falha_fechada`. O lockdown permanece ativo. Se a leitura não conseguir confirmar se ele está ativo, o estado continua fail-closed e o campo correspondente é reportado como `unknown`; o software nunca interpreta essa incerteza como rede liberada.

O watcher relê periodicamente as mesmas invariantes. Se o túnel, o relay, o lockdown, a LAN, o DNS ou outra condição necessária deixar de ser confirmável, o estado volta para `falha_fechada`.

## Restauração

`security.restore` exige `{ "confirm": true }`. O snapshot é gravado em `~/.local/state/nekrypta/security-session.json` (0600) e guarda as configurações anteriores por chave; ele permite recuperar a sessão se o shell reiniciar. Não é um backup de configuração. O backend restaura apenas uma chave que ainda contenha o valor aplicado pelo modo; se o usuário a mudou depois, preserva a mudança e registra a chave em `preserved`/`incompleteRestore`.

O lockdown é sempre a última chave restaurada e só é desligado depois que as anteriores forem confirmadas. Se a VPN já estava conectada antes da ativação, ela não é desconectada. Se não estava, o restore pode executar `mullvad disconnect` e confirmar a queda do túnel antes de liberar o lockdown.

Se o processo reiniciar em `falha_fechada` sem snapshot, não há como adivinhar configurações anteriores: o resultado é `manual`, o estado continua fail-closed e o desbloqueio precisa ser feito pelo usuário.

## Desbloqueio manual

Só faça isso depois de verificar conscientemente o estado da conexão e aceitar a consequência de liberar a rede:

```sh
mullvad lockdown-mode set off
```

O comando é uma ação manual do usuário; o Modo de Segurança não o executa como fallback automático de uma falha.

## Relays D3.1

A lista canônica de países/códigos aceita pelo modo é:

`al` Albânia · `bg` Bulgária · `hr` Croácia · `cy` Chipre · `cz` República Tcheca · `ee` Estônia · `fi` Finlândia · `gr` Grécia · `hk` Hong Kong · `hu` Hungria · `id` Indonésia · `ie` Irlanda · `il` Israel · `jp` Japão · `my` Malásia · `ng` Nigéria · `ph` Filipinas · `pl` Polônia · `rs` Sérvia · `sg` Singapura · `sk` Eslováquia · `si` Eslovênia · `za` África do Sul · `th` Tailândia.

A seleção de um relay, a conexão Mullvad e o lockdown não garantem anonimato, privacidade perfeita, imunidade jurídica ou ausência de registros. A lista D3.1 é apenas uma allowlist técnica de seleção; não constitui recomendação jurídica ou promessa de proteção contra identificação.

## Tailscale e o Minecraft

A política `allowTailscaleMinecraft=true` é fixa em `core/personal.json`. A ativação não executa `tailscale down`, não
desliga `tailscaled`, não altera `--accept-dns`, não habilita Exit Node e não torna o Tailscale a rota padrão: a internet
pública continua pela Mullvad. Se houver Exit Node, a ativação é recusada antes de qualquer alteração. O DNS é auditado por
interface (resolvedor local, Mullvad, rede local, MagicDNS, Docker/VM): o MagicDNS (`100.100.100.100` e
`fd7a:115c:a1e0::53`) só é aceito limitado a `tailscale0`, aos domínios da tailnet e sem rota padrão.

Nota operacional: com o lockdown da Mullvad, o tráfego que sai por `tailscale0` precisa ser aceito pelo firewall da
Mullvad. Isso exige uma regra `nftables` de exclusão (documentada pela Mullvad), instalada uma vez como root; o shell não
usa privilégios e, se o isolamento não puder ser comprovado, o estado é `protecao_parcial`.
