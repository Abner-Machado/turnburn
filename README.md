# turnburn

Onde a cota queima numa sessão do Claude Code.

O transcript de cada sessão fica em `~/.claude/projects/<projeto>/*.jsonl`. Lá dentro,
turno a turno, está a contagem de tokens: quanto foi input novo, quanto foi reler o
prefixo em cache, quanto foi gerar a resposta. O `turnburn` lê esse arquivo e mostra
para onde o gasto foi de verdade.

O detalhe que muda tudo: token cru engana. Reler o prefixo em cache custa 0,1x por token;
gerar output custa 5x. Uma sessão pode parecer 96% cache no volume e mesmo assim estar
queimando metade da cota em output. Só contar token não te conta isso. Pesar por custo, sim.

## Rodar

Sem instalar nada, só a biblioteca padrão do Python:

```
python turnburn.py caminho/da/sessao.jsonl
```

Ou pega a sessão mais recente sozinho:

```
python turnburn.py --latest
```

## O que sai

```
categoria        tokens crus     % cru      cota efetiva    % cota
----------------------------------------------------------------------
cache relido      23.593.169    96,6%      2.359.317    53,8%
cache 1h             706.906     2,9%      1.413.812    32,2%
cache 5m                   0     0,0%              0     0,0%
input novo               334     0,0%            334     0,0%
output               123.059     0,5%        615.295    14,0%
----------------------------------------------------------------------
total             24.423.468   100,0%      4.388.758   100,0%

cache relido: 96,6% do volume, mas 53,8% da cota.
output: 14,0% da cota.
turno mais caro: #163 (17:00) = 8,3% da cota da sessao.
```

A coluna que importa é a da direita. É ela que diz onde a cota foi.

## Outras saídas

Tabela por turno, ordenada pelo mais caro:

```
python turnburn.py sessao.jsonl --by-turn
python turnburn.py sessao.jsonl --by-turn --top 10
```

JSON, para jogar em outro script:

```
python turnburn.py sessao.jsonl --json
```

## Pesos

Os multiplicadores vêm das razões de preço publicadas da Anthropic, relativas ao input
não-cacheado (= 1,0): cache relido 0,1, cache 5m 1,25, cache 1h 2,0, output 5,0. Não é
dólar. A moeda que interessa aqui é cota, e cota escala com esses pesos, não com contagem
crua. Se o teu modelo tem outra razão, troca na mão:

```
python turnburn.py sessao.jsonl --weight output=8 --weight cache_read=0.2
```

## Por que

Eu já tinha medido que a maior parte do gasto de uma sessão é o prefixo relido a cada
turno, não o texto que sai. Faltava uma ferramenta que abrisse isso por turno e mostrasse
qual momento inflou o prefixo. É isso.

## Licença

MIT.
