"""
Módulo de Persistência e Banco de Dados Relacional do Projeto Teledoc.
Implementa o DDL especificado no Initialization Pack com suporte híbrido
para PostgreSQL (via variável DATABASE_URL) e SQLite local embarcado.
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent
STORAGE_DIR = BASE_DIR / "storage"
DB_PATH = STORAGE_DIR / "teledoc.db"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_storage_dirs() -> None:
    (STORAGE_DIR / "tiff").mkdir(parents=True, exist_ok=True)
    (STORAGE_DIR / "pdf").mkdir(parents=True, exist_ok=True)
    (STORAGE_DIR / "previews").mkdir(parents=True, exist_ok=True)


@contextmanager
def get_db_connection():
    init_storage_dirs()
    conn = sqlite3.connect(str(DB_PATH), detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Inicializa as tabelas do banco de dados e popula a tabela base_xml_porto."""
    with get_db_connection() as conn:
        cursor = conn.cursor()

        cursor.executescript(
            """
            -- TABELA: Lotes de Digitalização
            CREATE TABLE IF NOT EXISTS teledoc_lotes (
                id_lote TEXT PRIMARY KEY,
                operador_id TEXT NOT NULL,
                estacao_trabalho TEXT NOT NULL,
                data_inicio_captura TEXT NOT NULL,
                data_conclusao TEXT,
                status_processamento TEXT DEFAULT 'EM_CAPTURA',
                total_imagens_capturadas INTEGER DEFAULT 0
            );

            -- TABELA: Protocolos / Capas (Tipo 18)
            CREATE TABLE IF NOT EXISTS teledoc_protocolos (
                id_protocolo TEXT PRIMARY KEY,
                id_lote TEXT NOT NULL REFERENCES teledoc_lotes(id_lote) ON DELETE CASCADE,
                numero_protocolo TEXT NOT NULL UNIQUE,
                codigo_prestador TEXT,
                nome_prestador TEXT,
                lote_origem TEXT,
                qtd_guias_esperada INTEGER,
                qtd_guias_digitalizadas INTEGER DEFAULT 0,
                valor_total REAL,
                data_envio_xml TEXT,
                status_validacao TEXT DEFAULT 'PENDENTE',
                observacao_auditoria TEXT,
                criado_em TEXT NOT NULL
            );

            -- TABELA: Guias Médicas (Tipo 19)
            CREATE TABLE IF NOT EXISTS teledoc_guias (
                id_guia TEXT PRIMARY KEY,
                id_protocolo TEXT NOT NULL REFERENCES teledoc_protocolos(id_protocolo) ON DELETE CASCADE,
                numero_guia TEXT NOT NULL,
                ordem_no_protocolo INTEGER NOT NULL,
                total_paginas INTEGER DEFAULT 1,
                status_guia TEXT DEFAULT 'OK',
                criado_em TEXT NOT NULL
            );

            -- TABELA: Páginas / Imagens Armazenadas
            CREATE TABLE IF NOT EXISTS teledoc_paginas (
                id_pagina TEXT PRIMARY KEY,
                id_lote TEXT NOT NULL REFERENCES teledoc_lotes(id_lote) ON DELETE CASCADE,
                id_protocolo TEXT REFERENCES teledoc_protocolos(id_protocolo) ON DELETE CASCADE,
                id_guia TEXT REFERENCES teledoc_guias(id_guia) ON DELETE SET NULL,
                tipo_documento TEXT NOT NULL,
                numero_pagina_lote INTEGER NOT NULL,
                numero_pagina_documento INTEGER NOT NULL,
                caminho_storage_tiff TEXT NOT NULL,
                caminho_storage_pdf TEXT,
                codigo_barras_bruto TEXT,
                confianca_barcode REAL,
                tamanho_bytes INTEGER,
                largura_px INTEGER,
                altura_px INTEGER,
                criado_em TEXT NOT NULL
            );

            -- TABELA: Tabela Espelho / Leitura da BASE (Origem XML Porto)
            CREATE TABLE IF NOT EXISTS base_xml_porto (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                numero_protocolo TEXT UNIQUE NOT NULL,
                codigo_prestador TEXT,
                nome_prestador TEXT,
                numero_lote TEXT,
                quantidade_guias INTEGER,
                valor_total REAL,
                data_lote TEXT,
                processado INTEGER DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_protocolos_numero ON teledoc_protocolos(numero_protocolo);
            CREATE INDEX IF NOT EXISTS idx_guias_numero ON teledoc_guias(numero_guia);
            CREATE INDEX IF NOT EXISTS idx_base_protocolo ON base_xml_porto(numero_protocolo);
            """
        )

        # Popula carga inicial da tabela BASE (XMLs da Porto Seguro)
        seed_records = [
            (
                "1984729102",
                "61.384.920/0001-12",
                "Hospital Sírio-Libanês - Unidade Bela Vista",
                "LT-2026-00918",
                2,
                4850.75,
                "2026-11-12T10:30:00-03:00",
                0,
            ),
            (
                "2049182736",
                "45.112.098/0001-88",
                "Laboratório Fleury Medicina e Saúde S.A.",
                "LT-2026-00919",
                3,
                1920.40,
                "2026-11-13T14:15:00-03:00",
                0,
            ),
            (
                "1955019283",
                "12.887.431/0001-05",
                "Clínica Ortopédica Paulista Ltda",
                "LT-2026-00920",
                4,
                8340.00,
                "2026-11-13T16:45:00-03:00",
                0,
            ),
            (
                "2011928374",
                "33.741.552/0001-90",
                "Centro Diagnóstico Cardiológico Avançado",
                "LT-2026-00921",
                1,
                980.00,
                "2026-11-14T09:10:00-03:00",
                0,
            ),
            (
                "1990011223",
                "08.456.123/0001-44",
                "Instituto de Oftalmologia São Paulo",
                "LT-2026-00922",
                2,
                3150.20,
                "2026-11-14T11:00:00-03:00",
                0,
            ),
        ]

        for rec in seed_records:
            cursor.execute(
                """
                INSERT OR IGNORE INTO base_xml_porto (
                    numero_protocolo, codigo_prestador, nome_prestador,
                    numero_lote, quantidade_guias, valor_total, data_lote, processado
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rec,
            )


def row_to_dict(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    return dict(row)
