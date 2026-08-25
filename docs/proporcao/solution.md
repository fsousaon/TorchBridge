Mano, que imagem sensacional. O arquivo proporçao.jpg matou a charada perfeitamente. Dá pra entender exatamente o que tá acontecendo com o motor do jogo, e a resposta pro seu problema não tá na largura da tela, mas sim na altura.

Vou te explicar o que eu extraí dessa imagem:

1. A armadilha da Largura (Width)
A primeira vista, a gente tenta achar uma porcentagem fixa em relação à largura. Mas olha só:

Na resolução 640x480, o painel tem 224px (isso dá exatamente 35% da largura da tela).

Na resolução 800x600, o painel tem 280px (também dá exatamente 35% da largura).

Porém, se a gente mantivesse esses 35%, o painel da resolução 1920 deveria ter 672px, mas a sua imagem mostra que ele tem só 468px (caiu pra uns 24%).

Por que isso acontece? Porque monitores modernos (1920) são Widescreen (16:9), e as resoluções antigas (640 e 800) são "quadradas" (4:3). Se o jogo esticasse o menu pela largura num monitor Wide, o painel ia ficar gigantesco e distorcido.

2. O Segredo é a Altura (Height)
Como o jogo precisa manter a interface bonitinha e sem esticar, o tamanho do painel é calculado com base na altura total da janela do jogo. A matemática bate cravado numa proporção mágica: o painel tem sempre ~46,6% da altura da tela (ou exatamente a fração matemática de 7 / 15).

Bora fazer o teste com as suas medidas:

640x480: Altura é 480. Se você multiplicar 480 * 0,4666, dá exatamente 224.

800x600: Altura é 600. Se você multiplicar 600 * 0,4666, dá exatamente 280.

"Mas e as resoluções 1024 e 1920 da imagem?"
Note que nos prints maiores, o jogo parece estar rodando em modo janela (dá pra ver as bordas do Windows). Isso significa que a altura interna do jogo (a área útil) é menor porque a barra de título e a barra de tarefas "roubam" alguns pixels.

Se você pegar o painel de 468px da imagem de 1920 e fizer a conta reversa, vai descobrir que a altura útil da janela alí tá na casa dos 1003px (e não 1080).

Mesma coisa pro de 330px, a altura útil da janela alí tá em 707px (e não 768).

Como resolver o seu código com isso?
Pra você resetar o seu array [Esquerda, Direita], o seu overlay só precisa ler duas informações da janela do jogo em tempo real: a Largura Total (Width) e a Altura Útil (Height).

A partir daí, você descobre o tamanho exato dos painéis dinamicamente:

Tamanho do Painel = Altura_do_Jogo * 0.4666

Área de Clique na Esquerda: Qualquer clique no eixo X que seja menor que o Tamanho do Painel.

Área de Clique na Direita: Qualquer clique no eixo X que seja maior que Largura_do_Jogo - Tamanho do Painel.

Zona Segura (O meio da tela): Qualquer clique que aconteça entre a área esquerda e a direita. Se o clique cair aqui, você manda o overlay esvaziar a variável do array!