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
    },
    # Movimento direto: âncora = centro do personagem em fração da janela; raio = alcance do cursor.
    # O raio é sempre uma fração da ALTURA da janela (mesmo valor nos eixos x e y → área circular).
    "movement": {
        "initial_mode": "direct",
        "anchor_x": 0.50,
        "anchor_y": 0.47,
        "movement_radius_percent": 0.16,
        # Fração do raio onde o cursor começa ao sair da deadzone: o clique do click-to-move
        # cai perto do personagem (longe de NPCs/inimigos) e o cursor cresce até o raio
        # cheio conforme o stick é empurrado (o botão fica segurado e o herói segue).
        "click_center_fraction": 0.15,
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
        # Zonas de calibração dos painéis (caixas de fechar/central) visíveis no overlay.
        "show_calibration": True,
    },
    # Botões → teclas do Torchlight; radial_slots são os atalhos da roda (1..N, N = tamanho da lista).
    # Mapa do overworld (spec docs/REMAP-BOTOES): A/X = cliques de mouse; Y/B/RB/RT tocam
    # 1/2/3/4 na borda de subida; LT+botão toca 5..0. D-pad fica sem ação no overworld
    # (a tela inicial usará, mas o rastreamento dela ainda não existe).
    "bindings": {
        # A e X são os cliques de mouse (esquerdo/direito) — sem tecla associada.
        "a": "",
        "b": "2",
        "x": "",
        "y": "1",
        # D-pad: sem binding no overworld (vazio = ignorado).
        "dpad_up": "",
        "dpad_right": "",
        "dpad_down": "",
        "dpad_left": "",
        "r3": "TAB",
        "start": "ESC",
        # Ombros/gatilhos: toques na borda de subida (RB=3, RT=4).
        "rb": "3",
        "rt": "4",
        # Combos LT (L2/ZL) + botão = 5..0.
        "lt_a": "5",
        "lt_x": "6",
        "lt_y": "7",
        "lt_b": "8",
        "lt_rb": "9",
        "lt_rt": "0",
        "radial_slots": ["I", "S", "Q", "J", "P", "C", "A"],
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
        # Âncora do personagem limitada a 5%–95% da janela (nunca fora da tela).
        data["movement"]["anchor_x"] = clamp(float(data["movement"]["anchor_x"]), 0.05, 0.95)
        data["movement"]["anchor_y"] = clamp(float(data["movement"]["anchor_y"]), 0.05, 0.95)
        # Perfil antigo (radius_x_percent / radius_y_percent): migra para o raio circular.
        # O _deep_merge já injetou movement_radius_percent do default, então a detecção
        # é feita pela presença das chaves legado no dado mesclado.
        legacy_x = data["movement"].get("radius_x_percent")
        legacy_y = data["movement"].get("radius_y_percent")
        if isinstance(legacy_x, (int, float)) or isinstance(legacy_y, (int, float)):
            values = [float(v) for v in (legacy_x, legacy_y) if isinstance(v, (int, float))]
            data["movement"]["movement_radius_percent"] = sum(values) / len(values)
        data["movement"].pop("radius_x_percent", None)
        data["movement"].pop("radius_y_percent", None)
        data["movement"]["movement_radius_percent"] = clamp(
            # Raio do movimento limitado a 3%–45% da altura da janela.
            float(data["movement"]["movement_radius_percent"]), 0.03, 0.45
        )
        data["movement"]["click_center_fraction"] = clamp(
            # O clique inicial fica entre 5% e 80% do raio da âncora (nunca no ponto cego).
            float(data["movement"]["click_center_fraction"]), 0.05, 0.80
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
        # Marcadores visuais: tipos booleanos, senão volta ao padrão da seção.
        for flag in ("enabled", "show_aim_marker", "show_mode_badge", "show_calibration"):
            if not isinstance(data["overlay"].get(flag), bool):
                data["overlay"][flag] = deepcopy(DEFAULT_CONFIG["overlay"][flag])
        # Slots da roda precisam ser uma lista de strings.
        radial_slots = data["bindings"].get("radial_slots")
        if not isinstance(radial_slots, list) or not all(isinstance(item, str) for item in radial_slots):
            data["bindings"]["radial_slots"] = deepcopy(
                DEFAULT_CONFIG["bindings"]["radial_slots"]
            )
        # Migração do mapa do overworld (spec docs/REMAP-BOTOES): perfis escritos antes
        # do remap têm chaves que morreram (rb_hold/l3_hold) e valores antigos (Y="4",
        # d-pad 5-8). O marcador é o flag _legacy_bindings posto no reload (detecção
        # pelas chaves rb_hold/l3_hold — presentes em todo perfil do formato antigo).
        # Sobrescreve os HERDADOS que contradizem o novo mapa, mas preserva qualquer
        # binding customizado nas chaves que sobrevivem (start, r3, radial_slots e
        # os cliques de mouse).
        if data.pop("_legacy_bindings", None):
            data["bindings"]["b"] = "2"
            data["bindings"]["y"] = "1"
            data["bindings"]["rb"] = "3"
            data["bindings"]["rt"] = "4"
            for combo_key, default in (
                ("lt_a", "5"), ("lt_x", "6"), ("lt_y", "7"),
                ("lt_b", "8"), ("lt_rb", "9"), ("lt_rt", "0"),
            ):
                data["bindings"][combo_key] = default
        data["bindings"].pop("rb_hold", None)
        data["bindings"].pop("l3_hold", None)
        # D-pad: nunca mais dispara no overworld — vira "" mesmo em perfil antigo.
        for dpad_key in ("dpad_up", "dpad_right", "dpad_down", "dpad_left"):
            data["bindings"][dpad_key] = ""
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
            # Perfil escrito ANTES do remap do overworld? Marca para o _validate migrar
            # os defaults antigos (Y="4", d-pad 5-8) para o novo mapa. O marcador são
            # as chaves que SÓ o formato antigo tinha (rb_hold/l3_hold) — presentes em
            # todo perfil gerado pelo código antigo — assim um "y": "4" customizado a
            # posteriori nunca é pisado. Perfis novos não têm essas chaves → não migram.
            old_b = incoming.get("bindings")
            if isinstance(old_b, dict) and ("rb_hold" in old_b or "l3_hold" in old_b):
                incoming["_legacy_bindings"] = True
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
