# pygame e SDL no TorchBridge

Este documento explica como o projeto usa o **pygame-ce** como interface para o
**SDL2**, e o que cada chamada significa — para editar o código de controle sem
precisar caçar a documentação toda vez. O código comentado está em
`src/torchbridge/controller.py` (leitura) e `src/torchbridge/calibrate.py`
(calibração).

## O que é o que

- **SDL2**: biblioteca C multiplataforma de entrada/mídia. O TorchBridge usa só
  o subsistema de *joystick/game controller*.
- **pygame-ce**: fork mantido do pygame; empacota o SDL2 e expõe
  `pygame.joystick` e `pygame._sdl2.controller`.
- **GameController (SDL)**: camada que usa um banco de mapas
  (`SDL_GameControllerDB`) para traduzir qualquer controle em **nomes
  lógicos**: `A`, `B`, `X`, `Y`, `lb`, `lx`, `dpad_up` etc.
- **Joystick (raw)**: leitura crua por *índice*: `get_axis(0)`,
  `get_button(2)`, `get_hat(0)`. Não tem significado — por isso o assistente de
  calibração registra qual índice é qual comando.

## Inicialização

```python
import pygame
from pygame._sdl2 import controller as sdl_controller

pygame.display.init()   # SDL exige display/eventos para o joystick em várias plataformas
pygame.joystick.init()  # liga o subsistema de joystick
sdl_controller.init()   # liga o GameController (mapa SDL)
```

Encerramento é o espelho: `device.quit()` (ou `joystick.quit()`), depois
`sdl_controller.quit()`, `pygame.joystick.quit()`, `pygame.display.quit()`.

## Eventos

O pygame (e o SDL) entrega tudo por uma fila de eventos. O estado do controle
só atualiza quando a fila é processada:

```python
pygame.event.pump()  # processa a fila interna (sem bloquear)
```

No TorchBridge o `ControllerHub.poll()` chama `pump()` uma vez por tick, a
120 Hz, em vez de ficar consumindo `pygame.event.get()`. É o suficiente para
ler o estado atual. Sem `pump()` frequente, `get_button`/`get_axis` ficam com o
último valor antigo e a troca de conexão não é percebida.

## GameController: como ler

```python
sdl_controller.is_controller(index)   # o joystick 'index' tem mapa no banco SDL?
controller = sdl_controller.Controller(index)
joy = controller.as_joystick()        # o 'gamepad' também é um Joystick
```

- `joy.get_name()` → nome do fabricante/modelo.
- `controller.get_button(CONTROLLER_BUTTON_A)` → bool (**A = botão físico
  inferior**, não a letra gravada).
- `controller.get_axis(CONTROLLER_AXIS_LEFTX)` → int16 (-32768..32767) nos
  analógicos; os gatilhos costumam vir 0..32767 (repouso em 0).
- O SDL cuida da normalização do mapa: `A` é a posição inferior independente
  do controle (Xbox A, DualShock ✕, Switch B).

Normalização usada no projeto (`_normalized_axis`): divide por `32768` quando o
valor é negativo e por `32767` quando positivo — o int16 nunca passa de
±32767 na prática, e `-32768` existe só no limite teórico.

## Joystick raw: como ler

```python
joy = pygame.joystick.Joystick(index)
joy.get_guid()          # identificador estável do dispositivo (reconhece a calibração salva)
joy.get_name()
joy.get_numaxes()       # quantos eixos
joy.get_axis(i)         # float -1..1 nos eixos analógicos
joy.get_numbuttons()
joy.get_button(i)       # bool
joy.get_numhats()
joy.get_hat(i)          # (x, y) ∈ {-1, 0, 1}² — direcional digital
joy.rumble(low, high, ms)  # vibração (nem todo hardware suporta)
```

## Constantes SDL usadas

`CONTROLLER_AXIS_*` e `CONTROLLER_BUTTON_*` são IDs **lógicos** do
GameController. O `controller.py` os guarda em `AXES`/`BUTTONS` com
`getattr(..., fallback)` para não quebrar em versões do pygame que não exponham
alguma constante.

## Variáveis de ambiente

```python
os.environ.setdefault("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1")  # ler o controle com o jogo em foco
os.environ.setdefault("SDL_JOYSTICK_HIDAPI_PS5", "1")               # DualSense via USB/Bluetooth precisa do driver HIDAPI
os.environ.setdefault("SDL_JOYSTICK_HIDAPI_PS4", "1")               # DualShock 4 idem
```

Precisam ser setadas **antes** do `import pygame` (o SDL as lê na
inicialização).

## Caminhos no projeto

| Arquivo | O que faz com pygame |
| --- | --- |
| `controller.py` | Lê o controle a 120 Hz: tenta SDL GameController; sem mapa, usa joystick raw com a calibração do perfil. Toca vibração. |
| `calibrate.py` | Assistente CLI: usa joystick raw para descobrir eixos/botões/hats e grava o descritor no perfil (GUID + índices). |

## Pegadinhas

1. `get_axis` retorna **int16** no GameController (não float): divida como o
   `_normalized_axis` faz.
2. Gatilhos no modo SDL: 0 = solto, 32767 = fundo (o projeto normaliza para
   0..1).
3. `A/B/X/Y` são **posições físicas** (inferior/direita/esquerda/superior), não
   letras da carcaça.
4. Hat muda por *estado discreto*: `(0, 1)` = cima, `(-1, 0)` = esquerda.
5. Sem `pump()` frequente, o SDL não percebe dispositivos novos/removidos e o
   estado fica congelado.
6. `rumble()` pode levantar `pygame.error` em hardware sem motor — sempre em
   try/except (o `ControllerHub.rumble` já trata).
