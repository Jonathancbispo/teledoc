-- =============================================================================
-- PROJETO TELEDOC - CARGA INICIAL DA TABELA BASE (XMLs PORTO SEGURO)
-- =============================================================================

INSERT INTO base_xml_porto (
    numero_protocolo,
    codigo_prestador,
    nome_prestador,
    numero_lote,
    quantidade_guias,
    valor_total,
    data_lote,
    processado
) VALUES
    ('1984729102', '61.384.920/0001-12', 'Hospital Sírio-Libanês - Unidade Bela Vista', 'LT-2026-00918', 2, 4850.75, '2026-11-12T10:30:00-03:00', FALSE),
    ('2049182736', '45.112.098/0001-88', 'Laboratório Fleury Medicina e Saúde S.A.', 'LT-2026-00919', 3, 1920.40, '2026-11-13T14:15:00-03:00', FALSE),
    ('1955019283', '12.887.431/0001-05', 'Clínica Ortopédica Paulista Ltda', 'LT-2026-00920', 4, 8340.00, '2026-11-13T16:45:00-03:00', FALSE),
    ('2011928374', '33.741.552/0001-90', 'Centro Diagnóstico Cardiológico Avançado', 'LT-2026-00921', 1, 980.00, '2026-11-14T09:10:00-03:00', FALSE),
    ('1990011223', '08.456.123/0001-44', 'Instituto de Oftalmologia São Paulo', 'LT-2026-00922', 2, 3150.20, '2026-11-14T11:00:00-03:00', FALSE)
ON CONFLICT (numero_protocolo) DO NOTHING;
