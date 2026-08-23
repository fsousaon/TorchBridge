# Arquitetura do TorchBridge

## Fluxo

1. `ControllerHub` lê o dispositivo pelo SDL GameController.
2. Se o SDL não tiver um mapa, o backend RAW usa a calibração salva pelo GUID.
3. `BridgeEngine` processa deadzone, curva e transições em 120 Hz.
4. `WindowLocator` confirma que `Torchlight.exe` está em primeiro plano.
5. `InputInjector` envia scan codes e eventos de mouse pelo Win32 `SendInput`.
6. `SharedOverlayState` entrega apenas o estado visual para a thread do Qt.
7. `GameOverlay` desenha mira, modo, avisos e roda sem receber cliques ou foco.

## Princípios de segurança operacional

- Nenhum comando é enviado com o jogo fora de foco.
- Todo clique ou modificador mantido é liberado na perda de foco, pausa ou saída.
- O overlay usa `NOACTIVATE` e é transparente a cliques.
- Não há hook, patch, DLL injetada, leitura de memória ou escrita na instalação.
- O perfil inválido nunca substitui a última configuração válida em memória.

## Extensões previstas

- editor visual de bindings;
- perfis por classe/personagem;
- action layers para cidade, combate e inventário;
- ícones adaptativos para Xbox e PlayStation;
- telemetria local opcional de latência;
- assinatura de código e instalador MSI para distribuição pública.

