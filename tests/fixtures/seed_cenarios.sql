-- Seed de teste -- um ato por cenario que a revisao precisa distinguir.
--
-- Os ato_id de 4 digitos+ sao os reais citados no diagnostico do td-legislacao
-- (D-19/D-20, 01/09/2026); os de 1-2 digitos sao sinteticos. Manter os reais faz
-- o teste falhar se o holofote da secao 4 do relatorio parar de funcionar.

SET search_path = rfb_atos, public;

-- `id_portal` e `link` estao preenchidos em alguns atos de proposito: e assim que
-- o backfill descobre o que tentar, e o fixture precisa exercitar as duas vias
-- (URL gravada e URL montada do id_portal) alem do caso sem via nenhuma.
-- 127905 e o idAto real da IN 2.121 no SIJUT.
INSERT INTO rfb_atos.ato
    (id, tipo_ato, numero, ano, emissor, data_publicacao, ementa,
     status_vigencia, pdf_disponivel, content_disponivel, analise_completa,
     id_portal, link)
VALUES
    -- (A) caminho feliz: texto + materia + embedding
    (1, 'SOLUCAO_CONSULTA', '1', 2023, 'COSIT', '2023-01-10',
     'Ato completo, do PDF ao vetor.', 'vigente_nunca_alterado', true, true, true,
     NULL, NULL),

    -- (B) D5: texto, nenhuma materia (nunca categorizado)
    (2, 'SOLUCAO_CONSULTA', '2', 2023, 'COSIT', '2023-02-10',
     'Tem teor, nunca passou pela categorizacao.', 'vigente_nunca_alterado',
     true, true, false,
     NULL, NULL),

    -- (C) D6: materias, uma sem embedding
    (3, 'SOLUCAO_CONSULTA', '3', 2024, 'COSIT', '2024-03-10',
     'Categorizado pela metade no embedding.', 'vigente_nunca_alterado',
     true, true, true,
     NULL, NULL),

    -- (D) D4: PDF baixado (pdf_path), extracao nunca produziu texto
    (4, 'PORTARIA', '4', 2022, 'RFB', '2022-04-10',
     'PDF em maos, texto ausente.', 'vigente_nunca_alterado', true, false, false,
     NULL, NULL),

    -- (E) D3: tem texto, flag content_disponivel = false (mente contra)
    (5, 'ATO_DECLARATORIO_EXECUTIVO', '5', 2021, 'RFB', '2021-05-10',
     'Teor presente, flag rebaixado.', 'vigente_nunca_alterado', true, false, false,
     NULL, NULL),

    -- (F) D2: flag content_disponivel = true, sem texto (mente a favor)
    (6, 'PARECER_NORMATIVO', '6', 2020, 'COSIT', '2020-06-10',
     'Flag promovido sem teor.', 'vigente_nunca_alterado', true, true, false,
     NULL, NULL),

    -- (G) D7 puro: sem texto, sem tentativa, sem flag nenhum
    (7, 'NOTA', '7', 2019, 'RFB', '2019-07-10',
     'Ninguem nunca tentou coletar.', 'vigente_nunca_alterado', false, false, false,
     NULL, 'https://normas.receita.fazenda.gov.br/sijut2consulta/link.action?idAto=7'),

    -- (H) IN 2.121/2022 -- D1 + D8: analisada sem teor, coleta falhou no portal
    (16630, 'INSTRUCAO_NORMATIVA', '2121', 2022, 'RFB', '2022-12-15',
     'Consolida as normas sobre a Contribuicao para o PIS/Pasep e a Cofins.',
     'nao_disponivel_portal', false, false, true,
     127905, NULL),

    -- (I) IN 2.152/2023 -- tem texto e analise_completa = false. Coerente:
    --     "tem teor, ainda nao analisado". NAO e defeito, e o controle negativo.
    (16707, 'INSTRUCAO_NORMATIVA', '2152', 2023, 'RFB', '2023-07-20',
     'Altera a Instrucao Normativa RFB no 2.121, de 2022.',
     'vigente_nunca_alterado', true, true, false,
     NULL, NULL),

    -- (J) SC COSIT 110/2025 -- fonte indireta do art. 171
    (14575, 'SOLUCAO_CONSULTA', '110', 2025, 'COSIT', '2025-06-01',
     'Creditos. Imobilizado. ICMS e IPI na aquisicao.',
     'vigente_nunca_alterado', true, true, true,
     NULL, NULL),

    -- (K) SC COSIT 267/2023 -- fonte indireta dos arts. 179 e 185
    (12100, 'SOLUCAO_CONSULTA', '267', 2023, 'COSIT', '2023-11-01',
     'Creditos. Bens destinados a locacao a terceiros.',
     'vigente_nunca_alterado', true, true, true,
     NULL, NULL),

    -- (L) D1 adicional em outro tipo, para o recorte por tipo_ato ter o que mostrar
    (20, 'ATO_DECLARATORIO', '20', 2018, 'RFB', '2018-08-10',
     'Analisado sem teor.', 'nao_disponivel_portal', false, false, true,
     NULL, NULL);

INSERT INTO rfb_atos.ato_content (ato_id, texto_completo, caracteres, pdf_path)
VALUES
    (1,     'Teor integral do ato 1.',   24,   '/pdfs/1.pdf'),
    (2,     'Teor integral do ato 2.',   24,   '/pdfs/2.pdf'),
    (3,     'Teor integral do ato 3.',   24,   '/pdfs/3.pdf'),
    -- (D) linha existe, mas texto vazio: cobertura aparente sem cobertura real.
    (4,     '   ',                       0,    '/pdfs/4.pdf'),
    (5,     'Teor integral do ato 5.',   24,   '/pdfs/5.pdf'),
    -- (F) linha existe com texto NULL -- mesmo caso, outra forma.
    (6,     NULL,                        NULL, NULL),
    (16707, 'Teor integral da IN 2.152.', 27,  '/pdfs/16707.pdf'),
    (14575, 'Teor integral da SC 110.',   25,  '/pdfs/14575.pdf'),
    (12100, 'Teor integral da SC 267.',   25,  '/pdfs/12100.pdf');

INSERT INTO rfb_atos.ato_materia (ato_id, ordem, tema_macro, ementa_trecho, embedding)
VALUES
    (1,     1, 'CREDITOS', 'materia do ato 1',  '[0.1]'),
    (3,     1, 'CREDITOS', 'materia 1 do ato 3','[0.1]'),
    (3,     2, 'CREDITOS', 'materia 2 do ato 3', NULL),   -- D6
    (5,     1, 'CREDITOS', 'materia do ato 5',  '[0.1]'),
    (14575, 1, 'CREDITOS', 'ICMS/IPI na aquisicao de imobilizado', '[0.1]'),
    (12100, 1, 'CREDITOS', 'bens para locacao a terceiros',        '[0.1]');
