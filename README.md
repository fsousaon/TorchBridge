# TorchBridge

Controle de PS5, Xbox e controles genéricos no **Torchlight 1 para PC**, com
movimento direto, cursor analógico, roda de habilidades e overlay transparente.

O TorchBridge não modifica arquivos do jogo, não injeta DLL e não lê a memória
do processo. Ele reconhece o controle pelo SDL, acompanha a janela do Torchlight
e envia somente entradas normais de teclado e mouse pelo `SendInput` do Windows.

## O que já está implementado

- DualSense/PS5 via USB ou Bluetooth;
- controles Xbox 360, One e Series via USB, receptor ou Bluetooth;
- controles genéricos reconhecidos pelo banco de mapeamento SDL;
- assistente de calibração para controles genéricos não reconhecidos;
- conexão e desconexão do controle com o programa aberto;
- loop de entrada configurado em 120 Hz;
- movimento direto no analógico esquerdo;
- mira/cursor com aceleração no analógico direito;
- modo separado para inventário, lojas e menus;
- roda visual de seis atalhos (uma por posição do perfil);
- detecção automática das versões Steam e GOG pelo `Torchlight.exe`;
- rastreador de painéis abertos pela roda (C/P à esquerda, I/S/Q/J à direita), com
  indicador discreto no topo central do overlay;
- envio de comandos somente quando o jogo está em primeiro plano;
- liberação automática de cliques e teclas ao trocar de janela;
- calibração do ponto central do personagem dentro do jogo;
- perfil JSON editável e recarregado automaticamente;
- script para gerar um executável portátil com PyInstaller.

## Instalação rápida

Requisitos: Windows 10/11, Torchlight 1 e Python 3.11 ou mais recente.

1. Extraia a pasta em um local permanente.
2. Execute `INSTALAR.bat` uma única vez.
3. Execute `INICIAR_TORCHBRIDGE.bat`.
4. Conecte o controle e abra o Torchlight.

O programa fica no ícone **TB** ao lado do relógio do Windows. Pelo menu desse
ícone é possível pausar, abrir o perfil, recarregar alterações ou sair.

### Se o jogo for aberto como administrador

O Windows bloqueia entradas enviadas por um programa com privilégio menor.
Nesse caso, use `INICIAR_COMO_ADMIN.bat`. Não é necessário usar essa opção se o
Torchlight estiver rodando normalmente.

### Steam

Nas propriedades do Torchlight, desative o Steam Input para esse jogo enquanto
usar o TorchBridge. Deixar os dois mapeadores ativos ao mesmo tempo pode gerar
cliques ou teclas duplicados.

### Overlay

O controle funciona em tela cheia, mas overlays externos não aparecem de forma
confiável no modo exclusivo. Para ver a roda de habilidades e os avisos, use o
Torchlight em modo janela ou janela sem bordas.

## Mapeamento padrão

| Controle | Ação no Torchlight |
| --- | --- |
| Analógico esquerdo | Movimento direto; mantém o clique na direção escolhida |
| Analógico direito | Mira e cursor |
| RT / R2 | Clique esquerdo: atacar, mover, coletar e confirmar |
| LT / L2 | Clique direito: habilidade secundária |
| A / X inferior | Atalho 1 |
| B / Círculo | Atalho 2 |
| X / Quadrado | Atalho 3 |
| Y / Triângulo | Atalho 4 |
| Direcional cima/direita/baixo/esquerda | Atalhos 5/6/7/8 |
| Segurar LB / L1 + analógico direito | Roda de atalhos 1–6 |
| RB / R1 | Segura Shift, para atacar sem avançar |
| L3 | Segura Alt, para exibir itens |
| R3 | Tab, mapa/minimapa |
| Back / Create / Select | Alterna entre movimento direto e modo cursor |
| Start / Options | Esc, menus e retorno |

Na roda, a posição superior é o atalho 1 e as demais seguem no sentido horário.
Soltar LB/L1 confirma a seleção. Uma vibração curta indica a troca de posição.

As opções da roda também abrem os painéis do jogo: **C** e **P** no lado
esquerdo da tela; **I**, **S**, **Q** e **J** no lado direito. Escolher a mesma
opção de novo fecha o painel, e um indicador discreto no topo central do overlay
mostra, em tempo real, quais painéis estão abertos (esquerdo | direito).

## Modos de uso

### Movimento direto

É o modo padrão para exploração e combate. Ao inclinar o analógico esquerdo, o
TorchBridge coloca o cursor em um raio ao redor do herói e mantém o clique
esquerdo. Isso aproveita o próprio sistema de `click-to-move` do Torchlight e dá
sensação de controle direto sem alterar o jogo.

O analógico direito assume o cursor quando é movimentado. Use os gatilhos para
atacar ou lançar uma habilidade na posição indicada.

### Cursor / menus

Pressione Back/Create/Select para entrar no modo cursor. Os dois analógicos
podem movimentar a seta e os gatilhos funcionam como os dois botões do mouse.
Use esse modo no inventário, árvore de habilidades, lojas e diálogos. Pressione
Back/Create/Select novamente para retornar ao movimento direto.

## Calibrar o centro do personagem

O herói normalmente fica próximo ao centro da tela. Caso o movimento pareça
deslocado:

1. coloque o cursor do mouse exatamente sobre o personagem;
2. segure **Back/Create + Start/Options** por 0,8 segundo;
3. aguarde a mensagem `Centro do personagem calibrado`.

O ponto é salvo no perfil e continuará válido nas próximas execuções. Repita a
calibração se mudar resolução, proporção de tela ou escala da interface.

## Controle genérico

Primeiro teste sem calibrar: o SDL já normaliza a maioria dos controles atuais.
Se os eixos ou botões estiverem trocados:

1. feche o TorchBridge;
2. execute `CALIBRAR_CONTROLE_GENERICO.bat`;
3. siga as instruções, mexendo somente no comando solicitado;
4. abra novamente pelo `INICIAR_TORCHBRIDGE.bat`.

A calibração grava o GUID do dispositivo, portanto outro controle conectado não
deve tomar o lugar daquele que foi configurado.

## Personalizar sensibilidade e botões

Clique com o botão direito no ícone **TB** e escolha **Abrir perfil de
controles**. O arquivo fica, por padrão, em:

```text
%APPDATA%\TorchBridge\perfil.json
```

Os campos mais úteis são:

```json
{
  "input": {
    "poll_hz": 120,
    "deadzone": 0.18,
    "response_curve": 1.6,
    "trigger_threshold": 0.32
  },
  "movement": {
    "anchor_x": 0.5,
    "anchor_y": 0.47,
    "radius_x_percent": 0.16,
    "radius_y_percent": 0.13
  },
  "cursor": {
    "speed_pixels_per_second": 1450
  }
}
```

- `deadzone`: aumente se o personagem andar sozinho; reduza para maior resposta.
- `response_curve`: maior deixa o começo do analógico mais preciso.
- `radius_x_percent` e `radius_y_percent`: aumente se o personagem andar pouco.
- `speed_pixels_per_second`: velocidade do cursor analógico.
- `bindings`: troca as teclas ligadas aos botões e à roda 1–6.
- `overlay.enabled`: use `false` para desligar somente os elementos visuais.

O perfil é verificado a cada segundo. Salve o JSON e aguarde a mensagem de
recarregamento. Se o JSON estiver inválido, a última configuração válida
continua ativa. O log técnico fica em `%APPDATA%\TorchBridge\torchbridge.log`.

## Gerar um executável

Em um computador Windows, execute `GERAR_EXE.bat`. O resultado será criado em:

```text
dist\TorchBridge\TorchBridge.exe
```

O formato de pasta inicia mais rápido que um executável único e mantém juntos
os componentes do SDL e do Qt. Distribua a pasta `dist\TorchBridge` inteira.

## Verificação para uma primeira versão pública

Antes de distribuir, faça uma rodada real nas combinações abaixo:

- Steam + Windows 11 + controle Xbox via XInput;
- Steam + Windows 11 + DualSense USB e Bluetooth;
- GOG + Windows 10 + controle DirectInput genérico calibrado;
- 1920×1080, 2560×1440 e monitor com escala do Windows em 125%/150%;
- jogo em janela, janela sem bordas e tela cheia;
- inventário, lojas, pesca, portais, pet e combate com muita movimentação.

Os testes automatizados validam deadzone, curva, setores da roda, normalização de
gatilhos e persistência do perfil. No diretório do projeto:

```text
set PYTHONPATH=src
python -m unittest discover -s tests -v
```

## Limite técnico honesto

A edição de PC do Torchlight foi desenhada para apontar e clicar; a edição de
Xbox recebeu uma interface própria. Uma camada externa consegue deixar o
controle fluido e consistente, mas não substitui todos os menus do jogo por uma
UI de console. Fazer isso exigiria portar a interface da edição de Xbox ou
alterar profundamente o executável, o que seria frágil e incompatível entre
versões. O TorchBridge concentra o ganho onde ele é maior: movimento, combate,
habilidades e navegação sem abandonar o controle.

## Referências técnicas

- Microsoft `SendInput`: <https://learn.microsoft.com/windows/win32/api/winuser/nf-winuser-sendinput>
- pygame-ce Controller/SDL: <https://pyga.me/docs/ref/sdl2_controller.html>
- pygame-ce Joystick: <https://pyga.me/docs/ref/joystick.html>
- Guia interno: [docs/PYGAME.md](docs/PYGAME.md) — como o TorchBridge usa pygame/SDL2.
- Torchlight no Steam: <https://store.steampowered.com/app/41500/Torchlight/>

Torchlight é marca de seus respectivos proprietários. Este projeto é uma
ferramenta independente e não afiliada ao desenvolvedor ou à publicadora.

