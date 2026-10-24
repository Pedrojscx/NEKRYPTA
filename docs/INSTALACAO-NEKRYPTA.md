# Instalação do NEKRYPTA

## Dependências

As dependências estão em [`dependencies.txt`](../dependencies.txt): Arch Linux, Hyprland, Quickshell, Python,
PipeWire/WirePlumber e os utilitários Wayland e de desktop usados pelo shell. O instalador só mostra os pacotes de que
a máquina precisa e pede confirmação antes de usar `sudo pacman`.

- A CLI da Mullvad é necessária para o Modo de Segurança (versão compatível com o backend, atualmente 2026.4) e o
  daemon precisa estar em execução. Sem ela o resto do shell funciona.
- `ss` (pacote `iproute2`) é necessário para `exposure.audit`, somente leitura.
- O Tailscale é opcional e usado só para o Minecraft; o Modo de Segurança nunca o desliga.

## Instalar

```sh
git clone https://github.com/Pedrojscx/NEKRYPTA.git ~/src/nekrypta
cd ~/src/nekrypta
./install.sh                 # com --no-packages para instalar somente o shell
python3 scripts/install.py   # prévia: mostra o que mudaria, sem escrever nada
```

O instalador copia o shell para `~/.config/quickshell/nekrypta`, instala o comando `~/.local/bin/nekrypta` e a unidade
`~/.config/systemd/user/nekrypta.service`, escreve `nekrypta.lua`, `nekrypta-hypridle.conf` e
`nekrypta-hyprlock.conf` em `~/.config/hypr` e acrescenta uma linha em `hyprland.lua` que carrega `nekrypta.lua`.
Não clone diretamente em `~/.config/quickshell/nekrypta`: esse é o destino da instalação.

Depois: `nekrypta start` (ou `systemctl --user start nekrypta.service`) e `hyprctl reload`.

## Migrar uma instalação feita com o nome anterior

Se o instalador encontrar a instalação antiga, ele a migra de forma explícita, nesta ordem:

1. para e desabilita a unidade de usuário antiga;
2. copia só o estado necessário para `~/.local/state/nekrypta` (escolhas de papel de parede,
   preferências operacionais e o snapshot do Modo de Segurança, este com permissão 0600);
3. renomeia, em `hyprland.lua`, as linhas que o instalador antigo escreveu (guardando uma cópia ao lado);
4. remove o comando, a unidade, os arquivos de integração, o diretório do shell e os diretórios de configuração, estado,
   cache e runtime antigos;
5. executa `systemctl --user daemon-reload`, confirma que nenhum processo antigo continua ativo e instala o NEKRYPTA;
6. inicia `nekrypta.service`.

A prévia (`python3 scripts/install.py` sem `--apply`) lista o que será migrado. O código que conhece o nome anterior
está isolado em `scripts/legacy_migration.py`.

## Restauração manual do Modo de Segurança

Se o shell não puder restaurar a conexão (por exemplo, falha fechada sem snapshot), faça manualmente e só depois de
verificar o estado da rede:

```sh
mullvad disconnect
mullvad lockdown-mode set off
mullvad lan set allow     # somente se a rede local estava liberada antes
```

O Tailscale não é tocado em nenhum desses passos.

## Wallpaper padrão

O wallpaper `gwde5e` é uma escolha do seletor de wallpapers, baixada somente quando solicitada; não é empacotada neste
repositório.
