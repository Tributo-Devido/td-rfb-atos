---
name: td:tax-intelligence:rfb-atos:stats
description: Volumetria, frescor, cobertura — health check da base
model: haiku
---

# td:tax-intelligence:rfb-atos:stats

Dashboard rápido do estado da base.

## Uso

```bash
cd C:/td-rfb-atos/scripts
py stats.py
```

## O que mostra

### Volumetria por tipo_ato
| tipo_ato | total | com_pdf | com_content | analisados |
|---|---|---|---|---|
| SOLUCAO_CONSULTA | 14055 | 8309 | 8309 | 14055 |
| SOLUCAO_DIVERGENCIA | 250 | 200 | 200 | 250 |
| ... | | | | |

### Frescor
Última `data_publicacao` por tipo de ato. Permite detectar pipeline parado.

### Embeddings
- `n_materias` total vs `embedded` (quantas têm embedding)
- `n_chunks` total

### Grafo de relações
Distribuição de `ato_relacao.tipo_relacao`:
- revoga, altera, regulamenta, cita, uniformiza, interpretado_por, ...

## Quando rodar

- Diariamente após `/td:tax-intelligence:rfb-atos:pipeline` para validar
- Antes de pesquisar para conferir cobertura
- Após mudanças no schema/pipeline para detectar regressões
