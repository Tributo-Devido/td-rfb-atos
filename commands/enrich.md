---
name: td:tax-intelligence:rfb-atos:enrich
description: Pipeline de enriquecimento — download PDF, extração via MarkItDown, categorização via Haiku 4.5
model: haiku
---

# td:tax-intelligence:rfb-atos:enrich

Etapas de enriquecimento DEPOIS do crawl. Executa em ordem:

1. **Download de PDFs** (`download_pdf.py`) — para atos com link e `pdf_disponivel IS NULL`
2. **Extração de texto** (`extract_content.py`) — MarkItDown PDF → texto → tabela `ato_content`
3. **Categorização LLM** (`categorize_with_llm.py`) — Haiku 4.5 estrutura matérias + tributos + dispositivos + CNAEs + relações

## Uso

```bash
cd C:/td-rfb-atos/scripts

# Pipeline de enriquecimento completo (incremental)
py download_pdf.py
py extract_content.py
py categorize_with_llm.py

# Cada etapa pode ser limitada
py categorize_with_llm.py --limit 100
py categorize_with_llm.py --tipo SOLUCAO_CONSULTA
```

## Volumetria e custo

- **Download PDF:** rede + I/O (sem custo). ~50% dos atos têm PDF (heurística, depende do tipo)
- **MarkItDown:** local, gratuito. Velocidade ~2-5s/PDF
- **Categorização Haiku 4.5:** $0.001-0.01 por ato dependendo do tamanho. Backfill 14k atos com PDF + 143k ementa-only ≈ $250-280

## Output

Atos enriquecidos vão ter:
- `pdf_disponivel = TRUE/FALSE` em `atos`
- `content_disponivel = TRUE` em `atos` (se PDF foi extraído)
- `analise_completa = TRUE` em `atos` (após categorização)
- 1+ linhas em `ato_materia` por ato
- 0+ linhas em `materia_tributo`, `materia_dispositivo`, `materia_cnae`
- 0+ linhas em `ato_relacao` (grafo de revogação/citação)

## Pré-requisitos

- `/td:tax-intelligence:rfb-atos:crawl` rodado (atos populados)
- `ANTHROPIC_API_KEY` em `scripts/.env`

## Próximo passo

`/td:tax-intelligence:rfb-atos:embed` para gerar embeddings.
