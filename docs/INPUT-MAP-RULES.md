# Mapa de botões — TorchBridge

Padrão do overworld: cada botão dispara uma entrada REAL (SendInput). A/X são
os cliques de mouse (esquerdo/direito); o resto vira teclas (1..0) ou comandos
contextuais. O d-pad fica **sem ação** no overworld — o rastreamento da tela
inicial, que é quem vai usá-lo, ainda não existe.

---

## Tela inicial

> **AINDA NÃO MAPEADA (futuro).** Quando existir, apenas estes botões
> funcionam; o resto é ignorado.

- **Universal**
  - Stick esquerdo = movimento do mouse livre pela tela
  - d-pad = navega pelas opções da tela
- **PlayStation** — Cruz = clique esquerdo
- **Xbox/Genérico** — A = clique esquerdo
- **Nintendo** — A = clique esquerdo

OBS: na tela inicial existem inúmeros botões em posições fixas na janela, sempre
respeitando a mesma proporção de altura da tela, mas ainda não mapeamos os
mesmos — isso será feito no futuro.

---

## Overworld com painéis fechados

### PlayStation
```
Cruz       = clique esquerdo (mouse)
Quadrado   = clique direito (mouse)
Triângulo  = 1
Círculo    = 2
R1         = 3
R2         = 4
L2+Cruz       = 5
L2+Quadrado   = 6
L2+Triângulo  = 7
L2+Círculo    = 8
L2+R1         = 9
L2+R2         = 0
d-pad      = sem ação
```

### Xbox/Genérico
```
A      = clique esquerdo (mouse)
X      = clique direito (mouse)
Y      = 1
B      = 2
RB     = 3
RT     = 4
LT+A   = 5
LT+X   = 6
LT+Y   = 7
LT+B   = 8
LT+RB  = 9
LT+RT  = 0
d-pad  = sem ação
```

### Nintendo
```
A      = clique esquerdo (mouse)
X      = clique direito (mouse)
Y      = 1
B      = 2
R      = 3
ZR     = 4
ZL+A   = 5
ZL+X   = 6
ZL+Y   = 7
ZL+B   = 8
ZL+R   = 9
ZL+ZR  = 0
d-pad  = sem ação
```

**Notas técnicas (fechado)**
- 1..4 (Triângulo/Y, Círculo/B, R1/RB, R2/RT) são **toques na borda de subida**
  do botão/gatilho — segurar não repete.
- RB/R1/R e R2/ZR/RT **não seguram mais Shift/Alt**. Os holds antigos
  (`rb_hold`/`l3_hold`) foram removidos; se um dia voltarem, voltam como
  binding novo, não reusando as chaves velhas.
- LT/L2/ZL é o **modificador** do combo 5..0: enquanto o gatilho está ativo
  (≥ 0.50), o botão face dispara o combo em vez do toque/clique comum.
- O d-pad é **botão morto** no overworld.

---

## Overworld com painéis abertos

Com um ou mais painéis laterais abertos, **estes** botões mudam (o resto —
A/X cliques, RB=3, RT=4, combos LT=5..0 — não muda):

### PlayStation
```
Triângulo  = Shift + clique esquerdo
Círculo    = ESC  (active_panels = [ . ] — fecha todos, volta ao overworld)
L2+Cruz    = Ctrl + clique esquerdo
```

### Xbox/Genérico
```
Y     = Shift + clique esquerdo
B     = ESC  (active_panels = [ . ] — fecha todos, volta ao overworld)
LT+A  = Ctrl + clique esquerdo
```

### Nintendo
```
Y     = Shift + clique esquerdo
B     = ESC  (active_panels = [ . ] — fecha todos, volta ao overworld)
ZL+A  = Ctrl + clique esquerdo
```

**OBS (comportamento do jogo):** quando `active_panels` é `[P . ]` ou `[ . I]`,
pressionar Triângulo/Y do **lado da tela onde o painel está aberto** faz o jogo
abrir o outro painel automaticamente. O engine apenas **acompanha** esse estado
(âncora/roda/pet clicam certo); a ação em si é do jogo.

**Nota técnica:** o remap contextual usa o estado do tick **anterior**. Abrir um
painel pela roda e apertar B no **mesmo** tick toca o 2, não a ESC — o "aberto"
só conta quando estável por um tick.
