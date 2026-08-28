# Ainda nao mapeamos os botoes da tela inicial, desconsiderar esse bloco por enquanto
## Tela inicial(somente esses botoes devem funcionar outras ações devem ser desconsideradas)

### Universal
```
Stick esquerdo = movimento do mouse livre pela tela
d-pad = navega pelas opções da tela
```

### Playsation
```
Cruz = clique esquerdo
```

### Xbox/Genérco
```
A = clique esquerdo
```

### Nintendo
```
A = clique esquerdo
```

##### OBS: na tela inicial existem inumeros botoes em posições fixas na janela sempre espeitando a mesmo proporção de altura da tela, mas ainda nao mapeamos os mesmos isso será feito no futoro

# Overworld com paineis fechados

### Playstation 
```
Cruz = clique esquerdo
Quadrado = clique direito
Triangulo = 1
Circulo = 2
R1 = 3
R2 = 4
L2+Cruz = 5
L2+Quadrado = 6
L2+Triangulo = 7
L2+Circulo = 8
L2+R1 = 9
L2+R2 = 0
```

### Xbox/Genérico
```
A = clique esquerdo
X = clique direito
Y = 1
B = 2
RB = 3
RT = 4
LT+A = 5
LT+X = 6
LT+Y = 7
LT+B = 8
LT+RB = 9
LT+RT = 0
```

### Nintendo
```
A = clique esquerdo
X = clique direito
Y = 1
B = 2
R = 3
ZR = 4
ZL+A = 5
ZL+X = 6
ZL+B = 7
ZL+Y = 8
ZL+R = 9
ZL+ZR = 0
```

# Overworld com active_panels=[ P . I ] ou [ P .  ] ou [  . I ] (os seguintes botoes mudam)

###  Playstation 
``` Triangulo = Shift+clique esquerdo ```
	
``` Circulo = ESC(active_panels=[ . ] e fecha todos os paineis voltando ao estado de overworld)```

``` L2+Cruz = Ctrl+clique esquerdo ```

### Xbox/Genérico
``` Y = Shift+clique esquerdo``` 

``` B = ESC(active_panels=[ . ] e fecha todos os paineis voltando ao estado de overworld)```

``` LT+A = Ctrl+clique esquerdo``` 

###	Nintendo
``` Y = Shift+clique esquerdo ```
    
``` B = ESC(active_panels=[ . ] e fecha todos os paineis voltando ao estado de overworld)``` 

``` ZL+A = Ctrl+clique esquerdo``` 

##### OBS: a ação Triangulo/Y vai funcionar assim: 
    active_panels for igual a um desses [P . ] ou [ . I] se Triangulo/Y for pressionado no lado da tela com o painel aberto, o jogo abre o outro automaticamente
