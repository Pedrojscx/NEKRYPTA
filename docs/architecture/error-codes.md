# Códigos de erro do backend

Os métodos JSON-RPC devolvem `error.code` como um identificador técnico estável, sempre em inglês e `snake_case`. O backend mantém as mensagens em inglês; o frontend deve traduzi-las via `core/i18n.js` usando o código como chave. Mensagens de sucesso e dados de estado não são códigos de erro.

| Código | Significado | Sugestão de texto pt-BR |
|---|---|---|
| `invalid` | Parâmetros, dados ou resposta não respeitam o contrato | Entrada inválida. |
| `failed` | Operação ou serviço falhou sem uma categoria mais específica | Não foi possível concluir a operação. |
| `timeout` | O processo ou serviço excedeu o prazo | A operação demorou demais. |
| `unavailable` | Dependência ausente ou serviço indisponível | Serviço indisponível. |
| `cancelled` | Operação cancelada | Operação cancelada. |
| `permission` | Serviço recusou a operação por permissão | Permissão negada. |
| `authentication` | Falha de autenticação | Falha de autenticação. |
| `busy` | Outra operação exclusiva está em andamento | Já existe uma operação em andamento. |
| `cli_missing` | CLI do Mullvad não está instalada | Mullvad não está instalado. |
| `daemon_down` | Daemon do Mullvad não respondeu | O serviço Mullvad não está em execução. |
| `daemon_unavailable` | Daemon ou serviço da Mullvad indisponível | Não foi possível consultar a Mullvad. |
| `logged_out` | A CLI não encontrou uma sessão autenticada | Entre na Mullvad para continuar. |
| `incompatible` | Comando ou sintaxe da CLI não é compatível | A versão instalada não oferece este recurso. |
| `not_logged_in` | Nenhuma conta Mullvad autenticada | Faça login no Mullvad primeiro. |
| `version_unsupported` | A versão instalada não é compatível | A versão do Mullvad não é compatível. |
| `manual` | Não há snapshot seguro; recuperação exige ação manual | Restauração manual necessária. |
| `weather` | Consulta do serviço meteorológico falhou | Clima indisponível no momento. |
| `process_failed` | O processo CAVA não pôde ser iniciado ou reiniciado | Não foi possível iniciar o visualizador. |
| `restart_limited` | O CAVA falhou repetidamente dentro da janela de proteção | O visualizador foi desativado após falhas repetidas. |
| `dependency_missing` | Dependência necessária para a verificação não está instalada | Não foi possível verificar: dependência ausente. |
| `permission_denied` | A operação exige permissão ou confirmação explícita | Permissão ou confirmação necessária. |
| `unsupported` | O método não é suportado pelo host ou pela integração disponível | Recurso não suportado neste sistema. |
| `verification_failed` | A releitura não confirmou o estado esperado | Não foi possível verificar o estado. |
| `secret_agent_required` | O NetworkManager precisa de um agente para solicitar a credencial Wi-Fi | Autorize a senha pelo agente de rede do sistema. |
| `tailscale_exception` | O Tailscale usa um Exit Node ou rota padrão; a exceção para o Minecraft só vale sem eles | Desligue o Exit Node do Tailscale e tente de novo. |
| `security_inactive` | O modo de Segurança não está ativo | Modo de Segurança desativado. |

## Métodos cobertos

- `security.status`, `security.enable`, `security.restore` e `security.relays`: usam os códigos gerais e Mullvad acima; `security.restore` também pode retornar `manual: true` como resultado quando não há snapshot.
- `exposure.audit`: `unavailable`, `timeout`, `failed`.
- `weather.search` e `weather.get`: `weather`.
- `calculator`, `units.convert`: `invalid`.
- `files.search`: `invalid`, `unavailable`, `timeout` ou `failed`.
- `visualizer.start`, `visualizer.stop` e `visualizer.status`: `dependency_missing`, `process_failed`, `restart_limited`, `timeout` ou `failed`.
- `wifi.connect`: `secret_agent_required` quando uma rede protegida não tem agente de segredos do NetworkManager disponível.
- `security.overview`, `security.verify`, `security.activate`, `security.deactivate`, `security.dns.audit`, `security.ports.list`, `security.media.status`, `security.media.revoke`, `security.split.list`, `security.lan.get` e `security.lan.set`: `dependency_missing`, `permission_denied`, `timeout`, `unsupported`, `verification_failed` e `busy`, além dos códigos Mullvad compatíveis.

O código `vpn` legado e outros códigos de métodos fora deste escopo não são renomeados por esta tarefa.
