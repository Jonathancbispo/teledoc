-- =============================================================================
-- PROJETO TELEDOC - MODELAGEM DE DADOS RELACIONAL (PostgreSQL DDL)
-- Substituição integral do legado E2scan + E2doc
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- TABELA: Lotes de Digitalização
CREATE TABLE IF NOT EXISTS teledoc_lotes (
    id_lote UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    operador_id VARCHAR(50) NOT NULL,
    estacao_trabalho VARCHAR(50) NOT NULL,
    data_inicio_captura TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    data_conclusao TIMESTAMP WITH TIME ZONE,
    status_processamento VARCHAR(30) DEFAULT 'EM_CAPTURA', -- 'EM_CAPTURA', 'PROCESSANDO', 'CONCLUIDO', 'COM_EXCECAO'
    total_imagens_capturadas INT DEFAULT 0
);

-- TABELA: Protocolos / Capas (Tipo 18)
CREATE TABLE IF NOT EXISTS teledoc_protocolos (
    id_protocolo UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    id_lote UUID NOT NULL REFERENCES teledoc_lotes(id_lote) ON DELETE CASCADE,
    numero_protocolo VARCHAR(20) NOT NULL UNIQUE,
    codigo_prestador VARCHAR(30),
    nome_prestador VARCHAR(200),
    lote_origem VARCHAR(50),
    qtd_guias_esperada INT,
    qtd_guias_digitalizadas INT DEFAULT 0,
    valor_total NUMERIC(15, 2),
    data_envio_xml TIMESTAMP WITH TIME ZONE,
    status_validacao VARCHAR(40) DEFAULT 'PENDENTE', -- 'STATUS_INDEXADO_COM_SUCESSO', 'STATUS_DIVERGENCIA_GUIAS', 'STATUS_PROTOCOLO_NAO_ENCONTRADO', 'PENDENTE'
    observacao_auditoria TEXT,
    criado_em TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- TABELA: Guias Médicas (Tipo 19)
CREATE TABLE IF NOT EXISTS teledoc_guias (
    id_guia UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    id_protocolo UUID NOT NULL REFERENCES teledoc_protocolos(id_protocolo) ON DELETE CASCADE,
    numero_guia VARCHAR(30) NOT NULL, -- Ex: G1234567890123
    ordem_no_protocolo INT NOT NULL,
    total_paginas INT DEFAULT 1,
    status_guia VARCHAR(20) DEFAULT 'OK',
    criado_em TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- TABELA: Páginas / Imagens Armazenadas
CREATE TABLE IF NOT EXISTS teledoc_paginas (
    id_pagina UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    id_lote UUID NOT NULL REFERENCES teledoc_lotes(id_lote) ON DELETE CASCADE,
    id_protocolo UUID REFERENCES teledoc_protocolos(id_protocolo) ON DELETE CASCADE,
    id_guia UUID REFERENCES teledoc_guias(id_guia) ON DELETE SET NULL, -- Nulo se for folha anexa à capa ou a própria capa
    tipo_documento VARCHAR(10) NOT NULL, -- 'TIPO_18', 'TIPO_19', 'TIPO_20'
    numero_pagina_lote INT NOT NULL,
    numero_pagina_documento INT NOT NULL,
    caminho_storage_tiff VARCHAR(500) NOT NULL,
    caminho_storage_pdf VARCHAR(500),
    codigo_barras_bruto VARCHAR(100),
    confianca_barcode NUMERIC(5,2),
    tamanho_bytes BIGINT,
    largura_px INT,
    altura_px INT,
    criado_em TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- TABELA: Tabela Espelho / Leitura da BASE (Origem XML Porto Seguro)
CREATE TABLE IF NOT EXISTS base_xml_porto (
    id BIGSERIAL PRIMARY KEY,
    numero_protocolo VARCHAR(20) UNIQUE NOT NULL,
    codigo_prestador VARCHAR(30),
    nome_prestador VARCHAR(200),
    numero_lote VARCHAR(50),
    quantidade_guias INT,
    valor_total NUMERIC(15,2),
    data_lote TIMESTAMP WITH TIME ZONE,
    processado BOOLEAN DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_protocolos_numero ON teledoc_protocolos(numero_protocolo);
CREATE INDEX IF NOT EXISTS idx_guias_numero ON teledoc_guias(numero_guia);
CREATE INDEX IF NOT EXISTS idx_base_protocolo ON base_xml_porto(numero_protocolo);
CREATE INDEX IF NOT EXISTS idx_paginas_protocolo ON teledoc_paginas(id_protocolo);
CREATE INDEX IF NOT EXISTS idx_paginas_guia ON teledoc_paginas(id_guia);
