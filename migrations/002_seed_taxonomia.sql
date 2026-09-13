-- ===========================================================================
-- 002_seed_taxonomia.sql
--
-- Seeds mínimos das tabelas de taxonomia. Para popular completamente, rode
-- depois: `py scripts/seed_taxonomia.py` (lê references/taxonomia.json).
--
-- Esta migration é apenas para garantir que o schema esteja semanticamente
-- válido na primeira subida do Docker (caso o user pule o script Python).
-- ===========================================================================

-- Eficácias e status_vigencia são declarados como STRINGS no schema
-- (não como enums) para permitir evolução sem ALTER TABLE. Os valores
-- aceitos vivem em references/taxonomia.json.

-- Tipos de ato — minimum viable set (resto vem do script Python)
INSERT INTO taxonomia_tipo_ato (codigo, nome, sigla, sijut_value, eficacia_default, ativo) VALUES
    ('SOLUCAO_CONSULTA',         'Solução de Consulta',           'SC',    72, 'vinculante_local',  TRUE),
    ('SOLUCAO_DIVERGENCIA',      'Solução de Divergência',        'SD',    73, 'vinculante_geral',  TRUE),
    ('SOLUCAO_CONSULTA_INTERNA', 'Solução de Consulta Interna',   'SCI',   75, 'vinculante_geral',  TRUE),
    ('INSTRUCAO_NORMATIVA',      'Instrução Normativa',           'IN',    42, 'vinculante_geral',  TRUE),
    ('PORTARIA',                 'Portaria',                      'Port.', 57, 'vinculante_geral',  TRUE),
    ('ATO_DECLARATORIO_INTERPRETATIVO', 'Ato Declaratório Interpretativo', 'ADI', 10, 'vinculante_geral', TRUE),
    ('PARECER_NORMATIVO',        'Parecer Normativo',             'Parec. Norm.', 59, 'vinculante_geral', TRUE),
    ('ACORDAO_CARF',             'Acórdão do CARF',               'Acórdão', NULL, 'inter_partes',    TRUE)
ON CONFLICT (codigo) DO NOTHING;

-- Órgãos emissores — minimum viable set
INSERT INTO taxonomia_orgao_emissor (codigo, nome, hierarquia, eficacia_default) VALUES
    ('RFB',           'Receita Federal do Brasil (cúpula)', 1, 'vinculante_geral'),
    ('COSIT',         'Coordenação-Geral de Tributação',     2, 'vinculante_geral'),
    ('COANA',         'Coordenação-Geral de Administração Aduaneira', 2, 'vinculante_geral'),
    ('DISIT_SRRF01',  'Disit/SRRF01',  3, 'vinculante_local'),
    ('DISIT_SRRF02',  'Disit/SRRF02',  3, 'vinculante_local'),
    ('DISIT_SRRF03',  'Disit/SRRF03',  3, 'vinculante_local'),
    ('DISIT_SRRF04',  'Disit/SRRF04',  3, 'vinculante_local'),
    ('DISIT_SRRF05',  'Disit/SRRF05',  3, 'vinculante_local'),
    ('DISIT_SRRF06',  'Disit/SRRF06',  3, 'vinculante_local'),
    ('DISIT_SRRF07',  'Disit/SRRF07',  3, 'vinculante_local'),
    ('DISIT_SRRF08',  'Disit/SRRF08',  3, 'vinculante_local'),
    ('DISIT_SRRF09',  'Disit/SRRF09',  3, 'vinculante_local'),
    ('DISIT_SRRF10',  'Disit/SRRF10',  3, 'vinculante_local')
ON CONFLICT (codigo) DO NOTHING;

-- Tributos
INSERT INTO taxonomia_tributo (codigo, nome) VALUES
    ('IRPJ',  'Imposto de Renda Pessoa Jurídica'),
    ('IRPF',  'Imposto de Renda Pessoa Física'),
    ('IRRF',  'Imposto de Renda Retido na Fonte'),
    ('CSLL',  'Contribuição Social sobre o Lucro Líquido'),
    ('PIS',   'Contribuição para o PIS/Pasep'),
    ('COFINS','Contribuição para o Financiamento da Seguridade Social'),
    ('IPI',   'Imposto sobre Produtos Industrializados'),
    ('II',    'Imposto de Importação'),
    ('IE',    'Imposto de Exportação'),
    ('IOF',   'Imposto sobre Operações Financeiras'),
    ('ITR',   'Imposto sobre a Propriedade Territorial Rural'),
    ('CIDE',  'Contribuição de Intervenção no Domínio Econômico'),
    ('CONTRIB_PREV',          'Contribuições Sociais Previdenciárias'),
    ('CONTRIB_PREV_PATRONAL', 'Contribuição Previdenciária Patronal'),
    ('CONTRIB_PREV_SEGURADO', 'Contribuição Previdenciária do Segurado'),
    ('GILRAT', 'Contribuição ao GILRAT/SAT/RAT'),
    ('CONTRIB_TERCEIROS', 'Contribuições a Terceiros'),
    ('SIMPLES', 'Simples Nacional'),
    ('IBS',  'Imposto sobre Bens e Serviços'),
    ('CBS',  'Contribuição sobre Bens e Serviços'),
    ('IS',   'Imposto Seletivo')
ON CONFLICT (codigo) DO NOTHING;

-- Aviso: rodar `py scripts/seed_taxonomia.py` para popular taxonomia COMPLETA
-- (todos os 37 tipos de ato, 23 órgãos, 80+ temas específicos com schemas).
