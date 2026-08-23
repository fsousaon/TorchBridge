# Perfil do usuário: arquivo JSON editável em %APPDATA%\TorchBridge\perfil.json (Windows).
# Responsabilidades: criar com padrões, validar valores, mesclar com o padrão e recarregar a quente.
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
from threading import RLock
from typing import Any

from .mathutils import clamp


DEFAULT_CONFIG: dict[str, Any] = {
    # Versão do esquema do arquivo; reservada para migrações futuras.
    "version": 1,
    # Alvo da janela: só envia entrada quando um processo/título destes está em primeiro plano.
    "target": {
        "process_names": ["Torchlight.exe"],
        "window_titles": ["Torchlight"],
    },
    # Configuração do loop e dos analógicos (veja o README para o efeito de cada um).
    "input": {
        "poll_hz": 120,
        "deadzone": 0.18,
        "response_curve": 1.6,
        "trigger_threshold": 0.32,
    },
    # Movimento direto: âncora = centro do personagem em fração da janela; raios = alcance do cursor.
    "movement": {
        "initial_mode": "direct",
        "anchor_x": 0.50,
        "anchor_y": 0.47,
        "radius_x_percent": 0.16,
        "radius_y_percent": 0.13,
    },
    # Velocidade máxima do cursor analógico, em pixels por segundo.
    "cursor": {
        "speed_pixels_per_second": 1450,
    },
    # Elementos visuais do Qt: liga/desliga, escala e marcadores individuais.
    "overlay": {
        "enabled": True,
        "scale": 1.0,
        "show_aim_marker": True,
        "show_mode_badge": True,
    },
    # Identificação de cena: amostra a janela do jogo e bloqueia a roda de
    # habilidades em telas de menu (título, pausa, carregamento, cinemáticas),
    # onde ela não faz sentido. Valores em luminância média 0..255 por célula.
    "scenes": {
        "enabled": True,
        # Intervalo entre capturas/amostras da janela do jogo, em segundos.
        "sample_interval_s": 0.25,
        # Resolução da grade de luminância capturada (colunas × linhas).
        "grid_cols": 48,
        "grid_rows": 27,
        # Acima deste movimento médio a cena é considerada viva (gameplay).
        "motion_gameplay": 1.0,
        # Abaixo deste movimento médio a cena é considerada estática (menu).
        "motion_menu": 0.5,
        # Amostras consecutivas concordando para trocar de cena (histerese).
        "confirm_samples": 3,
        # Sinal de cor (frações 0..1): o HUD do Torchlight é azul-dominante no
        # topo (retrato/mana/minimapa) — acima deste valor a cena tem HUD e é
        # tratada como gameplay MESMO com o jogador parado (movimento ~0).
        "top_blue_menu": 0.5,
        # Acima deste valor de células QUENTES (vermelho/bege) na faixa
        # central (meio da tela, colunas centrais — onde o painel "Options"
        # fica) há o menu de pausa. O mundo do jogo é frio (azul-ardósia),
        # então "quente" é assinatura do painel — medido ao vivo: painel
        # 0.69 x mundo 0.06–0.17.
        "panel_warm_menu": 0.4,
        # Regiões ignoradas na análise, em frações 0..1 da área do jogo
        # (x0, y0, x1, y1): a cena 3D central anima até em menus (personagem,
        # fogo, lanternas), então não serve de sinal de cena. Valor medido do
        # print de referência do usuário (a área verde de title-screen.png).
        "ignore_rects": [[0.0104, 0.1667, 0.9901, 0.8713]],
    },
    # Botões → teclas do Torchlight; radial_slots são os atalhos da roda (1..6).
    "bindings": {
        "a": "1",
        "b": "2",
        "x": "3",
        "y": "4",
        "dpad_up": "5",
        "dpad_right": "6",
        "dpad_down": "7",
        "dpad_left": "8",
        "r3": "TAB",
        "start": "ESC",
        "rb_hold": "SHIFT",
        "l3_hold": "ALT",
        "radial_slots": ["I", "S", "Q", "J", "P", "C"],
    },
    # Mapa bruto para controles sem SDL (preenchido pelo assistente de calibração, indexado por GUID).
    "raw_controller": {
        "force_raw": False,
        "axes": {
            "left_x": {"index": 0, "invert": False},
            "left_y": {"index": 1, "invert": False},
            "right_x": {"index": 2, "invert": False},
            "right_y": {"index": 3, "invert": False},
        },
        "triggers": {
            "left": {"type": "axis", "index": 4, "rest": -1.0, "active": 1.0},
            "right": {"type": "axis", "index": 5, "rest": -1.0, "active": 1.0},
        },
        "buttons": {
            "a": {"type": "button", "index": 0},
            "b": {"type": "button", "index": 1},
            "x": {"type": "button", "index": 2},
            "y": {"type": "button", "index": 3},
            "lb": {"type": "button", "index": 4},
            "rb": {"type": "button", "index": 5},
            "back": {"type": "button", "index": 6},
            "start": {"type": "button", "index": 7},
            "l3": {"type": "button", "index": 8},
            "r3": {"type": "button", "index": 9},
            "dpad_up": {"type": "hat", "index": 0, "value": [0, 1]},
            "dpad_right": {"type": "hat", "index": 0, "value": [1, 0]},
            "dpad_down": {"type": "hat", "index": 0, "value": [0, -1]},
            "dpad_left": {"type": "hat", "index": 0, "value": [-1, 0]},
        },
    },
}


# Mescla o perfil do usuário sobre o padrão: dicionários combinam nível a nível; o resto é substituído.
# Chaves ausentes no arquivo continuam com o padrão — não é preciso listar tudo.
def _deep_merge(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


# Diretório do perfil: %APPDATA%\TorchBridge no Windows; ~/.config/TorchBridge nos demais.
def user_config_dir() -> Path:
    if os.name == "nt":
        root = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "TorchBridge"


class ConfigManager:
    # Caminho padrão ou alternativo (--perfil); carrega o estado inicial já validado.
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or user_config_dir() / "perfil.json"
        self._lock = RLock()
        self._data = deepcopy(DEFAULT_CONFIG)
        self._mtime_ns = 0
        # Carrega um perfil que já exista na inicialização.
        # Garante o arquivo antes do primeiro reload.
        self.ensure_exists()
        self.reload(force=True)

    # Primeira execução: cria a pasta e grava o perfil padrão.
    def ensure_exists(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write(DEFAULT_CONFIG)

    # Gravação atômica: escreve em '.json.tmp' e renomeia por cima — um crash no meio não corrompe o perfil.
    def _write(self, data: dict[str, Any]) -> None:
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)

    # Sanitização do perfil: tipos corretos e valores dentro de faixas seguras.
    def _validate(self, data: dict[str, Any]) -> dict[str, Any]:
        # Cada seção principal precisa ser um dicionário; senão volta ao padrão (nunca derruba o programa).
        for section in (
            "target",
            "input",
            "movement",
            "cursor",
            "overlay",
            "scenes",
            "bindings",
            "raw_controller",
        ):
            if not isinstance(data.get(section), dict):
                data[section] = deepcopy(DEFAULT_CONFIG[section])
        # Listas de processos/títulos precisam ser listas de strings.
        for target_key in ("process_names", "window_titles"):
            values = data["target"].get(target_key)
            if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
                data["target"][target_key] = deepcopy(DEFAULT_CONFIG["target"][target_key])
        # Frequência do loop limitada a 30–240 Hz.
        data["input"]["poll_hz"] = int(clamp(float(data["input"]["poll_hz"]), 30, 240))
        # Deadzone entre 2% e 60% (valores extremos quebrariam a jogabilidade).
        data["input"]["deadzone"] = clamp(float(data["input"]["deadzone"]), 0.02, 0.60)
        # Curva de resposta limitada a 0.2–4.0.
        data["input"]["response_curve"] = clamp(float(data["input"]["response_curve"]), 0.2, 4.0)
        # Limiar dos gatilhos (quando viram clique) limitado a 5%–95%.
        data["input"]["trigger_threshold"] = clamp(
            float(data["input"]["trigger_threshold"]), 0.05, 0.95
        )
        # Âncora do personagem limitada a 5%–95% da janela (nunca fora da tela).
        data["movement"]["anchor_x"] = clamp(float(data["movement"]["anchor_x"]), 0.05, 0.95)
        data["movement"]["anchor_y"] = clamp(float(data["movement"]["anchor_y"]), 0.05, 0.95)
        data["movement"]["radius_x_percent"] = clamp(
            # Raios do movimento limitados a 3%–45% da janela.
            float(data["movement"]["radius_x_percent"]), 0.03, 0.45
        )
        data["movement"]["radius_y_percent"] = clamp(
            float(data["movement"]["radius_y_percent"]), 0.03, 0.45
        )
        # Modo inicial só aceita valores conhecidos.
        if data["movement"].get("initial_mode") not in {"direct", "cursor"}:
            data["movement"]["initial_mode"] = "direct"
        # Velocidade do cursor limitada a 150–4000 px/s.
        data["cursor"]["speed_pixels_per_second"] = int(
            clamp(float(data["cursor"]["speed_pixels_per_second"]), 150, 4000)
        )
        # Escala do overlay entre 0.6× e 2.0×.
        data["overlay"]["scale"] = clamp(float(data["overlay"]["scale"]), 0.6, 2.0)
        # Amostragem da cena: intervalo de 0.1–2 s, grade de 16×9 até 96×54.
        data["scenes"]["sample_interval_s"] = clamp(
            float(data["scenes"]["sample_interval_s"]), 0.1, 2.0
        )
        data["scenes"]["grid_cols"] = int(clamp(float(data["scenes"]["grid_cols"]), 16, 96))
        data["scenes"]["grid_rows"] = int(clamp(float(data["scenes"]["grid_rows"]), 9, 54))
        # Limiares de movimento em luminância média por célula (0..255).
        data["scenes"]["motion_gameplay"] = clamp(
            float(data["scenes"]["motion_gameplay"]), 0.1, 20.0
        )
        data["scenes"]["motion_menu"] = clamp(
            float(data["scenes"]["motion_menu"]), 0.05, 10.0
        )
        # A zona de histerese depende de menu < gameplay; senão, ajusta para valer.
        if data["scenes"]["motion_menu"] >= data["scenes"]["motion_gameplay"]:
            data["scenes"]["motion_menu"] = data["scenes"]["motion_gameplay"] / 2.0
        # Confirmações consecutivas entre 1 e 8 amostras.
        data["scenes"]["confirm_samples"] = int(
            clamp(float(data["scenes"]["confirm_samples"]), 1, 8)
        )
        # Frações de cor das features (0..1; o HUD fica acima de 0.5 e o
        # painel do pause bem acima de 0.4 — limites generosos, só para não
        # aceitar lixo).
        data["scenes"]["top_blue_menu"] = clamp(
            float(data["scenes"]["top_blue_menu"]), 0.0, 1.0
        )
        data["scenes"]["panel_warm_menu"] = clamp(
            float(data["scenes"]["panel_warm_menu"]), 0.0, 1.0
        )
        # Máscara de análise: cada retângulo vira [x0, y0, x1, y1] normalizado
        # (0..1, x0 < x1, y0 < y1); entradas inválidas/degeneradas são descartadas.
        rects: list[list[float]] = []
        for rect in data["scenes"].get("ignore_rects", []):
            if not isinstance(rect, (list, tuple)) or len(rect) != 4:
                continue
            try:
                x0, y0, x1, y1 = (float(value) for value in rect)
            except (TypeError, ValueError):
                continue
            x0, x1 = sorted((min(1.0, max(0.0, x0)), min(1.0, max(0.0, x1))))
            y0, y1 = sorted((min(1.0, max(0.0, y0)), min(1.0, max(0.0, y1))))
            if x1 - x0 < 0.01 or y1 - y0 < 0.01:
                continue  # retângulo degenerado: não mascara nada útil
            rects.append([x0, y0, x1, y1])
        data["scenes"]["ignore_rects"] = rects
        # Slots da roda precisam ser uma lista de strings.
        radial_slots = data["bindings"].get("radial_slots")
        if not isinstance(radial_slots, list) or not all(isinstance(item, str) for item in radial_slots):
            data["bindings"]["radial_slots"] = deepcopy(
                DEFAULT_CONFIG["bindings"]["radial_slots"]
            )
        return data

    # Recarrega o arquivo quando o mtime muda (ou force). Qualquer erro mantém a última config válida.
    def reload(self, force: bool = False) -> bool:
        try:
            mtime_ns = self.path.stat().st_mtime_ns
            # Arquivo inalterado desde a última leitura: nada a fazer.
            if not force and mtime_ns == self._mtime_ns:
                return False
            # Falha aqui (JSON inválido ou erro de I/O) cai no except e mantém o perfil atual em memória.
            incoming = json.loads(self.path.read_text(encoding="utf-8"))
            # Raiz precisa ser objeto JSON (dict).
            if not isinstance(incoming, dict):
                raise ValueError("A raiz do perfil precisa ser um objeto JSON.")
            merged = self._validate(_deep_merge(DEFAULT_CONFIG, incoming))
        except (OSError, TypeError, KeyError, ValueError, json.JSONDecodeError):
            return False
        with self._lock:
            # Publica o novo estado validado e registra o mtime lido.
            self._data = merged
            self._mtime_ns = mtime_ns
        return True

    # Cópia profunda sob lock: o chamador lê sem travar e sem poder mutar o estado interno.
    def get(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._data)

    # Calibração do centro (combo Back+Start no jogo): grava a nova âncora e recarrega.
    def update_anchor(self, x: float, y: float) -> None:
        with self._lock:
            data = deepcopy(self._data)
            data["movement"]["anchor_x"] = round(clamp(x, 0.05, 0.95), 4)
            data["movement"]["anchor_y"] = round(clamp(y, 0.05, 0.95), 4)
            self._write(data)
        self.reload(force=True)

    # Grava o mapa bruto produzido pelo assistente de calibração (calibrate.py).
    def update_raw_mapping(self, mapping: dict[str, Any]) -> None:
        with self._lock:
            data = deepcopy(self._data)
            data["raw_controller"] = mapping
            self._write(data)
        self.reload(force=True)
