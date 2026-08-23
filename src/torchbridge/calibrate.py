# Assistente CLI de calibração para controles genéricos sem mapa SDL.
# Roteiro: escolhe o joystick, captura analógicos → gatilhos → botões/direcional e
# grava o mapeamento (GUID + índices) no perfil — o motor usa em modo RAW.
from __future__ import annotations

from copy import deepcopy
import os
import time
from typing import Any, Callable

import pygame

from .config import ConfigManager, DEFAULT_CONFIG


# Processa a fila de eventos do SDL e espera 8 ms — deixa o estado atualizar sem travar a CPU.
def _pump() -> None:
    pygame.event.pump()
    time.sleep(0.008)


# Leitura de todos os eixos crus do joystick em um instante.
def _axis_values(joystick: pygame.joystick.JoystickType) -> list[float]:
    _pump()
    return [float(joystick.get_axis(index)) for index in range(joystick.get_numaxes())]


# Espera o usuário soltar tudo (0,45 s) antes de medir a linha de base.
def _settle(joystick: pygame.joystick.JoystickType) -> None:
    deadline = time.monotonic() + 0.45
    while time.monotonic() < deadline:
        _pump()
    # Também espera enquanto houver botão pressionado.
    while any(joystick.get_button(i) for i in range(joystick.get_numbuttons())):
        _pump()


# Captura um analógico: mede o repouso, pede o movimento e identifica o eixo com maior deslocamento.
def _capture_axis(
    joystick: pygame.joystick.JoystickType,
    instruction: str,
    timeout: float = 15.0,
) -> dict[str, Any]:
    input(f"\n{instruction}\nDeixe todos os comandos soltos e pressione ENTER para armar...")
    _settle(joystick)
    # Amostra 20 leituras em repouso; a média é a linha de base (elimina ruído).
    baseline_samples = [_axis_values(joystick) for _ in range(20)]
    baseline = [sum(values) / len(values) for values in zip(*baseline_samples)]
    # Orientação ao usuário: mexer só no comando pedido.
    print("Agora mova o comando indicado e mantenha por um instante.")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        values = _axis_values(joystick)
        # Procura o eixo que mais se afastou do repouso...
        deltas = [value - rest for value, rest in zip(values, baseline)]
        if deltas:
            index = max(range(len(deltas)), key=lambda item: abs(deltas[item]))
            # ...e confirma apenas com deslocamento grande; invert sinaliza sentido invertido.
            if abs(deltas[index]) >= 0.55:
                result = {"index": index, "invert": deltas[index] < 0}
                print(f"Detectado: eixo {index}.")
                _settle(joystick)
                return result
    # Tempo esgotado: a etapa (e a calibração) é cancelada.
    raise TimeoutError("Nenhum eixo foi detectado.")


# Captura um comando digital (botão/hat) ou eixo-analógico (gatilho).
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
        # Procura botões pressionados...
        for index in range(joystick.get_numbuttons()):
            # ...primeiro botão apertado vira o descritor 'button'.
            if joystick.get_button(index):
                result = {"type": "button", "index": index}
                print(f"Detectado: botão {index}.")
                _settle(joystick)
                return result
        # Depois procura direcionais (hats)...
        for index in range(joystick.get_numhats()):
            value = joystick.get_hat(index)
            # ...e guarda o estado (x, y) do direcional acionado.
            if value != (0, 0):
                result = {"type": "hat", "index": index, "value": [value[0], value[1]]}
                print(f"Detectado: direcional {index}, valor {value}.")
                _settle(joystick)
                return result
        # Gatilhos aceitam eixo: mede repouso/ativo reais em vez de fixos.
        if allow_axis:
            values = _axis_values(joystick)
            deltas = [value - rest for value, rest in zip(values, baseline_axes)]
            if deltas:
                index = max(range(len(deltas)), key=lambda item: abs(deltas[item]))
                # Eixo confirmado como gatilho: salva rest (repouso) e active (acionado).
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


# Escolhe o dispositivo físico a calibrar (único ou por número).
def _choose_joystick() -> pygame.joystick.JoystickType:
    count = pygame.joystick.get_count()
    # Nenhum controle conectado: aborta com mensagem clara.
    if not count:
        raise RuntimeError("Nenhum controle foi encontrado.")
    devices = [pygame.joystick.Joystick(i) for i in range(count)]
    print("\nControles encontrados:")
    for index, device in enumerate(devices):
        print(f"  [{index}] {device.get_name()}")
    # Um só controle: nem pergunta.
    if len(devices) == 1:
        return devices[0]
    selected = int(input("Escolha o número do controle: ").strip())
    # Número fora da lista: aborta.
    if not 0 <= selected < len(devices):
        raise ValueError("Seleção inválida.")
    return devices[selected]


# Fluxo principal da calibração: inicializa SDL, captura tudo e grava no perfil.
def main() -> int:
    # Calibração (como o jogo) só roda no Windows.
    if os.name != "nt":
        print("A calibração do TorchBridge deve ser executada no Windows.")
        return 2
    # Inicializa SDL (display + joystick) para o assistente.
    pygame.display.init()
    pygame.joystick.init()
    print(
        "\nTORCHBRIDGE — CALIBRAÇÃO DE CONTROLE GENÉRICO\n"
        "Este assistente é necessário somente quando o controle não possui um mapa SDL válido.\n"
        "Durante cada etapa, mexa apenas no comando solicitado. Ctrl+C cancela sem salvar."
    )
    try:
        # Dispositivo alvo.
        joystick = _choose_joystick()
        # Captura os 4 analógicos: direções conhecidas revelam índice e sinal de cada eixo.
        axes = {
            "left_x": _capture_axis(joystick, "Mova o ANALÓGICO ESQUERDO para a DIREITA."),
            "left_y": _capture_axis(joystick, "Mova o ANALÓGICO ESQUERDO para BAIXO."),
            "right_x": _capture_axis(joystick, "Mova o ANALÓGICO DIREITO para a DIREITA."),
            "right_y": _capture_axis(joystick, "Mova o ANALÓGICO DIREITO para BAIXO."),
        }
        # Gatilhos: aceita eixo (analógico) ou botão (digital).
        triggers = {
            "left": _capture_control(joystick, "Pressione o GATILHO ESQUERDO (LT/L2).", True),
            "right": _capture_control(joystick, "Pressione o GATILHO DIREITO (RT/R2).", True),
        }
        # Botões e direcional: pares (nome lógico, instrução).
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
        # Monta o mapa final: força o modo RAW e prende o GUID deste controle.
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
        # Abre o perfil padrão; a gravação abaixo faz o motor passar a usar este mapeamento.
        manager = ConfigManager()
        # Persiste o mapeamento completo (força recarga do perfil).
        manager.update_raw_mapping(raw)
        print(
            f"\nCalibração salva em:\n{manager.path}\n\n"
            "Feche e abra o TorchBridge. O perfil genérico calibrado será usado automaticamente."
        )
        return 0
    # Ctrl+C a qualquer momento: nada foi gravado.
    except KeyboardInterrupt:
        print("\nCalibração cancelada; nada foi alterado.")
        return 130
    except (RuntimeError, ValueError, TimeoutError, pygame.error) as exc:
        print(f"\nNão foi possível concluir: {exc}")
        return 1
    # Sempre devolve os subsistemas SDL, mesmo em erro.
    finally:
        pygame.joystick.quit()
        pygame.display.quit()


if __name__ == "__main__":
    raise SystemExit(main())
