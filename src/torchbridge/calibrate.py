from __future__ import annotations

from copy import deepcopy
import os
import time
from typing import Any, Callable

import pygame

from .config import ConfigManager, DEFAULT_CONFIG


def _pump() -> None:
    pygame.event.pump()
    time.sleep(0.008)


def _axis_values(joystick: pygame.joystick.JoystickType) -> list[float]:
    _pump()
    return [float(joystick.get_axis(index)) for index in range(joystick.get_numaxes())]


def _settle(joystick: pygame.joystick.JoystickType) -> None:
    deadline = time.monotonic() + 0.45
    while time.monotonic() < deadline:
        _pump()
    while any(joystick.get_button(i) for i in range(joystick.get_numbuttons())):
        _pump()


def _capture_axis(
    joystick: pygame.joystick.JoystickType,
    instruction: str,
    timeout: float = 15.0,
) -> dict[str, Any]:
    input(f"\n{instruction}\nDeixe todos os comandos soltos e pressione ENTER para armar...")
    _settle(joystick)
    baseline_samples = [_axis_values(joystick) for _ in range(20)]
    baseline = [sum(values) / len(values) for values in zip(*baseline_samples)]
    print("Agora mova o comando indicado e mantenha por um instante.")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        values = _axis_values(joystick)
        deltas = [value - rest for value, rest in zip(values, baseline)]
        if deltas:
            index = max(range(len(deltas)), key=lambda item: abs(deltas[item]))
            if abs(deltas[index]) >= 0.55:
                result = {"index": index, "invert": deltas[index] < 0}
                print(f"Detectado: eixo {index}.")
                _settle(joystick)
                return result
    raise TimeoutError("Nenhum eixo foi detectado.")


def _capture_control(
    joystick: pygame.joystick.JoystickType,
    instruction: str,
    allow_axis: bool = False,
    timeout: float = 15.0,
) -> dict[str, Any]:
    input(f"\n{instruction}\nSolte tudo e pressione ENTER para armar...")
    _settle(joystick)
    baseline_axes = _axis_values(joystick)
    print("Agora pressione o comando indicado.")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _pump()
        for index in range(joystick.get_numbuttons()):
            if joystick.get_button(index):
                result = {"type": "button", "index": index}
                print(f"Detectado: botão {index}.")
                _settle(joystick)
                return result
        for index in range(joystick.get_numhats()):
            value = joystick.get_hat(index)
            if value != (0, 0):
                result = {"type": "hat", "index": index, "value": [value[0], value[1]]}
                print(f"Detectado: direcional {index}, valor {value}.")
                _settle(joystick)
                return result
        if allow_axis:
            values = _axis_values(joystick)
            deltas = [value - rest for value, rest in zip(values, baseline_axes)]
            if deltas:
                index = max(range(len(deltas)), key=lambda item: abs(deltas[item]))
                if abs(deltas[index]) >= 0.50:
                    result = {
                        "type": "axis",
                        "index": index,
                        "rest": round(baseline_axes[index], 4),
                        "active": round(values[index], 4),
                    }
                    print(f"Detectado: eixo {index}.")
                    _settle(joystick)
                    return result
    raise TimeoutError("Nenhum comando foi detectado.")


def _choose_joystick() -> pygame.joystick.JoystickType:
    count = pygame.joystick.get_count()
    if not count:
        raise RuntimeError("Nenhum controle foi encontrado.")
    devices = [pygame.joystick.Joystick(i) for i in range(count)]
    print("\nControles encontrados:")
    for index, device in enumerate(devices):
        print(f"  [{index}] {device.get_name()}")
    if len(devices) == 1:
        return devices[0]
    selected = int(input("Escolha o número do controle: ").strip())
    if not 0 <= selected < len(devices):
        raise ValueError("Seleção inválida.")
    return devices[selected]


def main() -> int:
    if os.name != "nt":
        print("A calibração do TorchBridge deve ser executada no Windows.")
        return 2
    pygame.display.init()
    pygame.joystick.init()
    print(
        "\nTORCHBRIDGE — CALIBRAÇÃO DE CONTROLE GENÉRICO\n"
        "Este assistente é necessário somente quando o controle não possui um mapa SDL válido.\n"
        "Durante cada etapa, mexa apenas no comando solicitado. Ctrl+C cancela sem salvar."
    )
    try:
        joystick = _choose_joystick()
        axes = {
            "left_x": _capture_axis(joystick, "Mova o ANALÓGICO ESQUERDO para a DIREITA."),
            "left_y": _capture_axis(joystick, "Mova o ANALÓGICO ESQUERDO para BAIXO."),
            "right_x": _capture_axis(joystick, "Mova o ANALÓGICO DIREITO para a DIREITA."),
            "right_y": _capture_axis(joystick, "Mova o ANALÓGICO DIREITO para BAIXO."),
        }
        triggers = {
            "left": _capture_control(joystick, "Pressione o GATILHO ESQUERDO (LT/L2).", True),
            "right": _capture_control(joystick, "Pressione o GATILHO DIREITO (RT/R2).", True),
        }
        prompts: list[tuple[str, str]] = [
            ("a", "Pressione A / X (inferior)."),
            ("b", "Pressione B / CÍRCULO (direita)."),
            ("x", "Pressione X / QUADRADO (esquerda)."),
            ("y", "Pressione Y / TRIÂNGULO (superior)."),
            ("lb", "Pressione LB / L1."),
            ("rb", "Pressione RB / R1."),
            ("back", "Pressione BACK / CREATE / SELECT."),
            ("start", "Pressione START / OPTIONS."),
            ("l3", "Clique o ANALÓGICO ESQUERDO (L3)."),
            ("r3", "Clique o ANALÓGICO DIREITO (R3)."),
            ("dpad_up", "Pressione o DIRECIONAL PARA CIMA."),
            ("dpad_right", "Pressione o DIRECIONAL PARA A DIREITA."),
            ("dpad_down", "Pressione o DIRECIONAL PARA BAIXO."),
            ("dpad_left", "Pressione o DIRECIONAL PARA A ESQUERDA."),
        ]
        buttons = {
            name: _capture_control(joystick, prompt, allow_axis=name.startswith("dpad_"))
            for name, prompt in prompts
        }
        raw = deepcopy(DEFAULT_CONFIG["raw_controller"])
        raw.update(
            {
                "force_raw": True,
                "device_guid": joystick.get_guid(),
                "device_name": joystick.get_name(),
                "axes": axes,
                "triggers": triggers,
                "buttons": buttons,
            }
        )
        manager = ConfigManager()
        manager.update_raw_mapping(raw)
        print(
            f"\nCalibração salva em:\n{manager.path}\n\n"
            "Feche e abra o TorchBridge. O perfil genérico calibrado será usado automaticamente."
        )
        return 0
    except KeyboardInterrupt:
        print("\nCalibração cancelada; nada foi alterado.")
        return 130
    except (RuntimeError, ValueError, TimeoutError, pygame.error) as exc:
        print(f"\nNão foi possível concluir: {exc}")
        return 1
    finally:
        pygame.joystick.quit()
        pygame.display.quit()


if __name__ == "__main__":
    raise SystemExit(main())
