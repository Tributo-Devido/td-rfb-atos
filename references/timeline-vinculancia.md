# Timeline de vinculância e detecção de supersedências

**Status:** v1 (2026-04-30) — pipeline funcional com classificador heurístico (gera falsos positivos).

## Premissa jurídica

Solução de Consulta Cosit é vinculante para o **período de 60 meses anteriores à publicação** (janela de prescrição tributária — art. 173, I, do CTN combinado com efeitos retroativos da SC). Uma SC mais recente sobre o mesmo fato pode **sobrepor** entendimento anterior, mesmo sem ato formal de revogação.

Consequência operacional: para qualquer fato gerador na data D, é vinculante a SC publicada em [D - 60 meses, D].

## Comandos disponíveis

### Filtro temporal na busca

```bash
py retrieve.py search "credito combustivel veiculo industrial" \
  --modo consultivo --tributos PIS COFINS \
  --vinculante-em 2022-08-15 --limit 10
```

Aplica `data_publicacao BETWEEN (data - 60 meses, data)` + `eficacia LIKE 'vinculante%'`.

### Linha temporal de um tema

```bash
py timeline.py tema CREDITAMENTO.COMBUSTIVEL --tributos PIS COFINS
py timeline.py tema CREDITAMENTO.FRETE --vinculante-em 2024-01-01
```

Renderiza markdown com SCs agrupadas por ano + bloco "⚠️ Possíveis Supersedências".

### Top conflitos a revisar

```bash
py timeline.py conflitos --top 50
```

Lista pares com sinais opostos sem relação formal de revogação.

## Pipeline de detecção

1. Migration `007_timeline_conflitos.sql` cria tabela `conflito_temporal`.
2. Script `detectar_conflitos.py` para cada `tema_especifico`:
   - Pega matérias com embedding de SCs/SDs/SCIs vinculantes
   - Compara par-a-par via cosine similarity (limiar default 0.85)
   - Classifica `solucao` por keywords (AUTORIZADO/VEDADO/CONDICIONADO)
   - Marca par como conflito quando sinais opostos
   - Mais recente recebe `ato_b_supera=TRUE`
   - Verifica `ato_relacao` formal pra distinguir supersedência implícita

Comando: `py detectar_conflitos.py --limiar 0.85` (todos) ou `--tema X --dry-run`.

## Limitações conhecidas (v1)

### Classificador heurístico produz falsos positivos
- Keywords como "impossibilidade", "vedação" podem aparecer em SCs que **autorizam** o crédito mas mencionam outras vedações no texto.
- Ex.: SC 244/2019 COSIT foi rotulada VEDADO por conter "por impossibilidade de montagem prévia", mas o conteúdo principal AUTORIZA.

### Solução proposta (v2 — não implementada)
- Substituir classificador por Haiku 4.5 com prompt específico:
  "Esta matéria autoriza o crédito de PIS/COFINS para o fato consultado? Responda apenas: AUTORIZA | VEDA | CONDICIONA."
- Rodar uma vez sobre 41k matérias (~$8 batch)
- Persistir em `ato_materia.sinal` e regenerar conflitos

### Sub-aspectos dentro do mesmo tema
- Matérias do mesmo `tema_especifico` podem tratar de subaspectos diferentes. Ex.: dentro de CREDITAMENTO.FRETE pode haver "frete na aquisição de insumo" (autorizado) e "frete na revenda monofásica" (vedado), e ambas são corretas — não é supersedência.
- Solução: `subtema` mais granular (já existe no schema), ou clusterização adicional por embedding dentro do `tema_especifico`.

### Modulação de efeitos
- Algumas SCs têm modulação prospectiva ("produz efeitos a partir de DD/MM/AAAA") que altera a janela de 60 meses retroativa.
- Hoje não capturado. Necessário extrair `data_efeito` do JSON do extrator e aplicar como filtro.

## Estado atual da base

```sql
SELECT COUNT(*) FROM conflito_temporal;                           -- 20.928
SELECT COUNT(*) FROM conflito_temporal WHERE ato_b_supera;        -- ~17k pares
SELECT COUNT(*) FROM conflito_temporal WHERE formal_relacao_existe; -- baixo (poucas relações formais)
SELECT tema_especifico, COUNT(*) FROM conflito_temporal GROUP BY 1 ORDER BY 2 DESC LIMIT 10;
```

Top temas com mais conflitos (após exclusão de matérias do mesmo ato):
- CREDITAMENTO.CONCEITO_INSUMO
- CREDITAMENTO.ATIVO_IMOBILIZADO (1099)
- CREDITAMENTO.FRETE (843)
- CREDITAMENTO.COMPENSACAO (124)
- CREDITAMENTO.ALUGUEIS (103)
- CREDITAMENTO.ENERGIA_ELETRICA (68)
- CREDITAMENTO.CREDITO_PRESUMIDO (55)

## Roadmap

1. ✅ Filtro `--vinculante-em` no retrieve
2. ✅ Migration + script de detecção
3. ✅ Comando `timeline.py` para rendering
4. ⏳ Classificador via Haiku 4.5 (substitui keyword)
5. ⏳ Sub-clusterização por embedding dentro do tema
6. ⏳ Extração de `data_efeito` modulada
7. ⏳ Integração com `retrieve.py`: ao retornar resultado, anexar "supera/é superada por" do `conflito_temporal`
