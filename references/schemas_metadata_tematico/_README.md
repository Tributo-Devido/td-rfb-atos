# Schemas de metadata_tematico

Cada arquivo `{TEMA_ESPECIFICO}.json` define o JSON Schema esperado em `ato_materia.metadata_tematico` quando o `tema_especifico` for o nome do arquivo.

**Como funciona:**
1. O LLM categorizador (Haiku) recebe o schema do tema apropriado e DEVE preencher `metadata_tematico` conforme.
2. Hook PreToolUse pode validar o JSONB contra o schema antes de inserir.
3. Pesquisa pode filtrar por campos do schema (`WHERE metadata_tematico->>'ncm_definido' LIKE '21%'`).

**Convenção de naming:** `{TEMA_MACRO}.{TEMA_ESPECIFICO}.json` — espelha exatamente o `tema_especifico` em uppercase com ponto.

**Quando criar um schema novo:**
- O tema é frequente nos atos (>50 matérias estimadas)
- Tem campos estruturados específicos que mudam o retrieval (NCM, percentuais, prazos, dispositivos chave)
- Vale a pena pra usuário filtrar/agregar por esses campos

**Quando NÃO criar:**
- Tema raro (< 10 atos)
- Campos genéricos já cobertos por `tags[]` + `tributos[]` + `dispositivos[]`
- Categoria muito subjetiva sem campos enumeráveis

Os schemas atuais são partida — o usuário valida e expande conforme uso real do retrieval.
