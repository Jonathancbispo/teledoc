"""
Motor de Ingestão, Separação Documental, Enriquecimento (BASE XML) e Auditoria
------------------------------------------------------------------------------
Implementa integralmente as regras das Seções 3.1 e 3.2 do Initialization Pack:
- Separação automática por Code 128 em Capa (TIPO_18), Guia (TIPO_19) e Anexo (TIPO_20)
- Cruzamento automático com a tabela `base_xml_porto`
- Validação de consistência física vs. XML:
  * STATUS_INDEXADO_COM_SUCESSO
  * STATUS_DIVERGENCIA_GUIAS
  * STATUS_PROTOCOLO_NAO_ENCONTRADO
- Conciliação e tratamento manual de exceções na Central de Auditoria
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.barcode_engine import (
    TeledocBarcodeParser,
    build_synthetic_tiff_with_barcode,
    classify_barcode,
)
from backend.database import STORAGE_DIR, get_db_connection, row_to_dict, utc_now_iso
from backend.pdf_tiff_service import generate_consolidated_protocol_pdf


def recalculate_protocol_status(conn, id_protocolo: str, force_approve: bool = False) -> Dict[str, Any]:
    """
    Recalcula contagens de páginas/guias e revalida o protocolo contra a tabela `base_xml_porto`.
    """
    cur = conn.cursor()
    cur.execute("SELECT * FROM teledoc_protocolos WHERE id_protocolo = ?", (id_protocolo,))
    prot = row_to_dict(cur.fetchone())
    if not prot:
        raise ValueError(f"Protocolo {id_protocolo} não encontrado.")

    numero_protocolo = prot["numero_protocolo"]

    # Reconta páginas em cada guia do protocolo
    cur.execute(
        "SELECT id_guia FROM teledoc_guias WHERE id_protocolo = ? ORDER BY ordem_no_protocolo ASC",
        (id_protocolo,),
    )
    guias_rows = cur.fetchall()
    for idx, g_row in enumerate(guias_rows, start=1):
        gid = g_row["id_guia"]
        cur.execute("SELECT COUNT(*) AS cnt FROM teledoc_paginas WHERE id_guia = ?", (gid,))
        total_pags = cur.fetchone()["cnt"]
        cur.execute(
            "UPDATE teledoc_guias SET ordem_no_protocolo = ?, total_paginas = ? WHERE id_guia = ?",
            (idx, max(1, total_pags), gid),
        )

    qtd_guias_dig = len(guias_rows)

    # Consulta a tabela BASE (XMLs Porto Seguro)
    cur.execute(
        "SELECT * FROM base_xml_porto WHERE numero_protocolo = ?",
        (numero_protocolo,),
    )
    base_row = row_to_dict(cur.fetchone())

    if base_row:
        codigo_prestador = base_row["codigo_prestador"]
        nome_prestador = base_row["nome_prestador"]
        lote_origem = base_row["numero_lote"]
        qtd_esperada = int(base_row["quantidade_guias"] or 0)
        valor_total = float(base_row["valor_total"] or 0.0)
        data_envio_xml = base_row["data_lote"]

        if force_approve or qtd_guias_dig == qtd_esperada:
            status_val = "STATUS_INDEXADO_COM_SUCESSO"
            cur.execute(
                "UPDATE base_xml_porto SET processado = 1 WHERE numero_protocolo = ?",
                (numero_protocolo,),
            )
        else:
            status_val = "STATUS_DIVERGENCIA_GUIAS"
    else:
        codigo_prestador = prot.get("codigo_prestador")
        nome_prestador = prot.get("nome_prestador")
        lote_origem = prot.get("lote_origem")
        qtd_esperada = prot.get("qtd_guias_esperada")
        valor_total = prot.get("valor_total")
        data_envio_xml = prot.get("data_envio_xml")
        status_val = "STATUS_INDEXADO_COM_SUCESSO" if force_approve else "STATUS_PROTOCOLO_NAO_ENCONTRADO"

    cur.execute(
        """
        UPDATE teledoc_protocolos
        SET codigo_prestador = ?,
            nome_prestador = ?,
            lote_origem = ?,
            qtd_guias_esperada = ?,
            qtd_guias_digitalizadas = ?,
            valor_total = ?,
            data_envio_xml = ?,
            status_validacao = ?
        WHERE id_protocolo = ?
        """,
        (
            codigo_prestador,
            nome_prestador,
            lote_origem,
            qtd_esperada,
            qtd_guias_dig,
            valor_total,
            data_envio_xml,
            status_val,
            id_protocolo,
        ),
    )

    # Atualiza PDF consolidado do protocolo
    cur.execute("SELECT * FROM teledoc_protocolos WHERE id_protocolo = ?", (id_protocolo,))
    updated_prot = row_to_dict(cur.fetchone())
    cur.execute(
        "SELECT * FROM teledoc_guias WHERE id_protocolo = ? ORDER BY ordem_no_protocolo ASC",
        (id_protocolo,),
    )
    guias = [dict(r) for r in cur.fetchall()]
    cur.execute(
        "SELECT * FROM teledoc_paginas WHERE id_protocolo = ? ORDER BY numero_pagina_lote ASC",
        (id_protocolo,),
    )
    paginas = [dict(r) for r in cur.fetchall()]

    if updated_prot and paginas:
        pdf_bytes = generate_consolidated_protocol_pdf(updated_prot, guias, paginas)
        pdf_rel_path = f"storage/pdf/{updated_prot['numero_protocolo']}.pdf"
        pdf_abs_path = STORAGE_DIR / "pdf" / f"{updated_prot['numero_protocolo']}.pdf"
        pdf_abs_path.write_bytes(pdf_bytes)
        cur.execute(
            "UPDATE teledoc_paginas SET caminho_storage_pdf = ? WHERE id_protocolo = ?",
            (pdf_rel_path, id_protocolo),
        )

    return updated_prot


def recalculate_batch_status(conn, id_lote: str) -> Dict[str, Any]:
    """Atualiza total de páginas e status geral do lote."""
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS cnt FROM teledoc_paginas WHERE id_lote = ?", (id_lote,))
    total_imgs = cur.fetchone()["cnt"]

    cur.execute("SELECT status_validacao FROM teledoc_protocolos WHERE id_lote = ?", (id_lote,))
    statuses = [r["status_validacao"] for r in cur.fetchall()]

    cur.execute("SELECT status_processamento FROM teledoc_lotes WHERE id_lote = ?", (id_lote,))
    lote_row = cur.fetchone()
    if not lote_row:
        raise ValueError(f"Lote {id_lote} não encontrado.")

    if not statuses:
        novo_status = lote_row["status_processamento"]
    elif any(s in ("STATUS_DIVERGENCIA_GUIAS", "STATUS_PROTOCOLO_NAO_ENCONTRADO") for s in statuses):
        novo_status = "COM_EXCECAO"
    elif all(s == "STATUS_INDEXADO_COM_SUCESSO" for s in statuses):
        novo_status = "CONCLUIDO"
    else:
        novo_status = "PROCESSANDO"

    cur.execute(
        "UPDATE teledoc_lotes SET total_imagens_capturadas = ?, status_processamento = ? WHERE id_lote = ?",
        (total_imgs, novo_status, id_lote),
    )
    cur.execute("SELECT * FROM teledoc_lotes WHERE id_lote = ?", (id_lote,))
    return row_to_dict(cur.fetchone())


def create_batch(operador_id: str, estacao_trabalho: str) -> Dict[str, Any]:
    id_lote = str(uuid.uuid4())
    now = utc_now_iso()
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO teledoc_lotes (
                id_lote, operador_id, estacao_trabalho, data_inicio_captura,
                status_processamento, total_imagens_capturadas
            ) VALUES (?, ?, ?, ?, 'EM_CAPTURA', 0)
            """,
            (id_lote, operador_id, estacao_trabalho, now),
        )
        cur.execute("SELECT * FROM teledoc_lotes WHERE id_lote = ?", (id_lote,))
        return row_to_dict(cur.fetchone())


def ingest_scanned_page(
    id_lote: str,
    image_bytes: Optional[bytes] = None,
    barcode_hint: Optional[str] = None,
    rotation_deg: int = 0,
) -> Dict[str, Any]:
    """
    Recebe uma página escaneada do lote, executa o parser Code 128, aplica as regras
    de separação (TIPO_18 Capa, TIPO_19 Guia, TIPO_20 Folha Anexa) e persiste o TIFF.
    """
    if not image_bytes:
        image_bytes = build_synthetic_tiff_with_barcode(
            barcode_text=barcode_hint,
            rotation_deg=rotation_deg,
        )

    parse_res = TeledocBarcodeParser.parse_image(image_bytes, barcode_hint=barcode_hint)
    now = utc_now_iso()

    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM teledoc_lotes WHERE id_lote = ?", (id_lote,))
        lote = row_to_dict(cur.fetchone())
        if not lote:
            raise ValueError(f"Lote {id_lote} não encontrado.")

        cur.execute(
            "SELECT COALESCE(MAX(numero_pagina_lote), 0) AS max_pag FROM teledoc_paginas WHERE id_lote = ?",
            (id_lote,),
        )
        numero_pagina_lote = cur.fetchone()["max_pag"] + 1

        id_pagina = str(uuid.uuid4())
        tiff_rel_path = f"storage/tiff/{id_lote}_p{numero_pagina_lote:03d}_{id_pagina[:8]}.tif"
        tiff_abs_path = STORAGE_DIR / "tiff" / f"{id_lote}_p{numero_pagina_lote:03d}_{id_pagina[:8]}.tif"
        tiff_abs_path.write_bytes(image_bytes)

        tipo_doc = parse_res.tipo_documento
        codigo_bruto = parse_res.codigo_barras_bruto

        active_protocol_id: Optional[str] = None
        active_guia_id: Optional[str] = None
        numero_pagina_doc = 1

        if tipo_doc == "TIPO_18" and codigo_bruto:
            # Inicia uma nova pasta/protocolo documental (ou reutiliza se já existir)
            cur.execute(
                "SELECT * FROM teledoc_protocolos WHERE numero_protocolo = ?",
                (codigo_bruto,),
            )
            existing_prot = row_to_dict(cur.fetchone())

            if existing_prot:
                active_protocol_id = existing_prot["id_protocolo"]
                cur.execute(
                    "UPDATE teledoc_protocolos SET id_lote = ? WHERE id_protocolo = ?",
                    (id_lote, active_protocol_id),
                )
            else:
                active_protocol_id = str(uuid.uuid4())
                # Enriquecimento imediato com a tabela BASE
                cur.execute(
                    "SELECT * FROM base_xml_porto WHERE numero_protocolo = ?",
                    (codigo_bruto,),
                )
                base_info = row_to_dict(cur.fetchone())
                if base_info:
                    cur.execute(
                        """
                        INSERT INTO teledoc_protocolos (
                            id_protocolo, id_lote, numero_protocolo, codigo_prestador,
                            nome_prestador, lote_origem, qtd_guias_esperada,
                            qtd_guias_digitalizadas, valor_total, data_envio_xml,
                            status_validacao, criado_em
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, 'PENDENTE', ?)
                        """,
                        (
                            active_protocol_id,
                            id_lote,
                            codigo_bruto,
                            base_info["codigo_prestador"],
                            base_info["nome_prestador"],
                            base_info["numero_lote"],
                            base_info["quantidade_guias"],
                            base_info["valor_total"],
                            base_info["data_lote"],
                            now,
                        ),
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO teledoc_protocolos (
                            id_protocolo, id_lote, numero_protocolo, status_validacao, criado_em
                        ) VALUES (?, ?, ?, 'STATUS_PROTOCOLO_NAO_ENCONTRADO', ?)
                        """,
                        (active_protocol_id, id_lote, codigo_bruto, now),
                    )
            active_guia_id = None
            numero_pagina_doc = 1

        else:
            # Busca a última Capa (Protocolo) ativa neste lote
            cur.execute(
                """
                SELECT id_protocolo FROM teledoc_paginas
                WHERE id_lote = ? AND id_protocolo IS NOT NULL
                ORDER BY numero_pagina_lote DESC LIMIT 1
                """,
                (id_lote,),
            )
            last_prot_row = cur.fetchone()
            if last_prot_row:
                active_protocol_id = last_prot_row["id_protocolo"]
            else:
                # Caso o operador escaneie uma guia/folha sem Capa no início do lote:
                # Cria um protocolo temporário de exceção para auditoria
                active_protocol_id = str(uuid.uuid4())
                orphan_num = f"99{str(uuid.uuid4().int)[:8]}"
                cur.execute(
                    """
                    INSERT INTO teledoc_protocolos (
                        id_protocolo, id_lote, numero_protocolo, nome_prestador,
                        status_validacao, observacao_auditoria, criado_em
                    ) VALUES (?, ?, ?, 'Lote Iniciado Sem Capa (Exceção Operacional)',
                              'STATUS_PROTOCOLO_NAO_ENCONTRADO',
                              'Protocolo provisório criado pois o lote iniciou sem Capa TIPO_18.', ?)
                    """,
                    (active_protocol_id, id_lote, orphan_num, now),
                )

            if tipo_doc == "TIPO_19" and codigo_bruto:
                # Nova Guia Médica vinculada à última Capa ativa
                cur.execute(
                    "SELECT COUNT(*) AS cnt FROM teledoc_guias WHERE id_protocolo = ?",
                    (active_protocol_id,),
                )
                ordem_guia = cur.fetchone()["cnt"] + 1
                active_guia_id = str(uuid.uuid4())
                cur.execute(
                    """
                    INSERT INTO teledoc_guias (
                        id_guia, id_protocolo, numero_guia, ordem_no_protocolo,
                        total_paginas, status_guia, criado_em
                    ) VALUES (?, ?, ?, ?, 1, 'OK', ?)
                    """,
                    (active_guia_id, active_protocol_id, codigo_bruto, ordem_guia, now),
                )
                numero_pagina_doc = 1
            else:
                # TIPO_20: Folha Anexa agregada à última Guia ativa (ou à Capa se anteceder a 1ª guia)
                cur.execute(
                    """
                    SELECT id_guia FROM teledoc_guias
                    WHERE id_protocolo = ?
                    ORDER BY ordem_no_protocolo DESC LIMIT 1
                    """,
                    (active_protocol_id,),
                )
                last_guia_row = cur.fetchone()
                if last_guia_row:
                    active_guia_id = last_guia_row["id_guia"]
                    cur.execute(
                        "SELECT COUNT(*) AS cnt FROM teledoc_paginas WHERE id_guia = ?",
                        (active_guia_id,),
                    )
                    numero_pagina_doc = cur.fetchone()["cnt"] + 1
                    cur.execute(
                        "UPDATE teledoc_guias SET total_paginas = ? WHERE id_guia = ?",
                        (numero_pagina_doc, active_guia_id),
                    )
                else:
                    active_guia_id = None
                    cur.execute(
                        "SELECT COUNT(*) AS cnt FROM teledoc_paginas WHERE id_protocolo = ? AND id_guia IS NULL",
                        (active_protocol_id,),
                    )
                    numero_pagina_doc = cur.fetchone()["cnt"] + 1

        # Insere registro na tabela `teledoc_paginas`
        cur.execute(
            """
            INSERT INTO teledoc_paginas (
                id_pagina, id_lote, id_protocolo, id_guia, tipo_documento,
                numero_pagina_lote, numero_pagina_documento, caminho_storage_tiff,
                codigo_barras_bruto, confianca_barcode, tamanho_bytes,
                largura_px, altura_px, criado_em
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                id_pagina,
                id_lote,
                active_protocol_id,
                active_guia_id,
                tipo_doc,
                numero_pagina_lote,
                numero_pagina_doc,
                tiff_rel_path,
                codigo_bruto,
                parse_res.confianca_barcode,
                len(image_bytes),
                parse_res.largura_px,
                parse_res.altura_px,
                now,
            ),
        )

        # Atualiza contagem de guias digitalizadas no protocolo
        cur.execute(
            "SELECT COUNT(*) AS cnt FROM teledoc_guias WHERE id_protocolo = ?",
            (active_protocol_id,),
        )
        qtd_guias_atuais = cur.fetchone()["cnt"]
        cur.execute(
            "UPDATE teledoc_protocolos SET qtd_guias_digitalizadas = ? WHERE id_protocolo = ?",
            (qtd_guias_atuais, active_protocol_id),
        )

        # Atualiza contador de imagens do lote
        cur.execute(
            """
            UPDATE teledoc_lotes
            SET total_imagens_capturadas = ?, status_processamento = 'PROCESSANDO'
            WHERE id_lote = ?
            """,
            (numero_pagina_lote, id_lote),
        )

        cur.execute("SELECT * FROM teledoc_paginas WHERE id_pagina = ?", (id_pagina,))
        pagina_salva = row_to_dict(cur.fetchone())
        pagina_salva["descricao_tipo"] = parse_res.descricao_tipo
        pagina_salva["orientacao_detectada"] = parse_res.orientacao_detectada
        return pagina_salva


def finalize_batch(id_lote: str) -> Dict[str, Any]:
    """
    Fecha o lote (`/api/v1/lotes/{id_lote}/concluir`), aciona a validação completa
    de todos os protocolos contra a tabela `base_xml_porto` e gera os PDFs consolidados.
    """
    now = utc_now_iso()
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM teledoc_lotes WHERE id_lote = ?", (id_lote,))
        lote = row_to_dict(cur.fetchone())
        if not lote:
            raise ValueError(f"Lote {id_lote} não encontrado.")

        cur.execute(
            "SELECT id_protocolo FROM teledoc_protocolos WHERE id_lote = ?",
            (id_lote,),
        )
        prot_ids = [r["id_protocolo"] for r in cur.fetchall()]

        protocolos_validados = []
        for pid in prot_ids:
            p_val = recalculate_protocol_status(conn, pid)
            protocolos_validados.append(p_val)

        cur.execute(
            "UPDATE teledoc_lotes SET data_conclusao = ? WHERE id_lote = ?",
            (now, id_lote),
        )
        lote_atualizado = recalculate_batch_status(conn, id_lote)
        lote_atualizado["protocolos"] = protocolos_validados
        return lote_atualizado


def resolve_audit_divergence(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Aplica correção manual de metadados ou reindexação assistida na Central de Auditoria
    (`PATCH /api/v1/auditoria/divergencias`).
    Ações suportadas:
    - `corrigir_protocolo`: Atualiza o `numero_protocolo` e reexecuta o cruzamento com a BASE
    - `excluir_pagina`: Remove página indevida/em branco ou guia duplicada e revalida o protocolo
    - `dividir_guia`: Promove uma Folha Anexa (`TIPO_20`) para uma nova Guia Médica (`TIPO_19`)
    - `aprovar_manual`: Concilia e aprova manualmente a divergência com justificativa de auditoria
    """
    acao = payload.get("acao", "corrigir_protocolo")
    id_protocolo = payload.get("id_protocolo")
    observacao = payload.get("observacao") or "Correção aplicada via Central de Auditoria Web Hub."

    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM teledoc_protocolos WHERE id_protocolo = ?", (id_protocolo,))
        prot = row_to_dict(cur.fetchone())
        if not prot:
            raise ValueError("Protocolo informado não existe.")

        id_lote = prot["id_lote"]

        if acao == "corrigir_protocolo":
            novo_numero = str(payload.get("novo_numero_protocolo") or "").strip()
            if not novo_numero:
                raise ValueError("Informe o novo número de protocolo (10 dígitos).")
            cur.execute(
                "UPDATE teledoc_protocolos SET numero_protocolo = ?, observacao_auditoria = ? WHERE id_protocolo = ?",
                (novo_numero, observacao, id_protocolo),
            )
            cur.execute(
                """
                UPDATE teledoc_paginas
                SET codigo_barras_bruto = ?
                WHERE id_protocolo = ? AND tipo_documento = 'TIPO_18'
                """,
                (novo_numero, id_protocolo),
            )
            prot_atualizado = recalculate_protocol_status(conn, id_protocolo)

        elif acao == "excluir_pagina":
            id_pagina = payload.get("id_pagina")
            if not id_pagina:
                raise ValueError("Informe `id_pagina` para exclusão.")
            cur.execute("SELECT * FROM teledoc_paginas WHERE id_pagina = ?", (id_pagina,))
            pag = row_to_dict(cur.fetchone())
            if not pag:
                raise ValueError("Página não encontrada.")

            id_guia = pag.get("id_guia")
            cur.execute("DELETE FROM teledoc_paginas WHERE id_pagina = ?", (id_pagina,))

            # Se era a única página de uma guia ou a cabeça da guia, remove a guia vazia
            if id_guia:
                cur.execute("SELECT COUNT(*) AS cnt FROM teledoc_paginas WHERE id_guia = ?", (id_guia,))
                if cur.fetchone()["cnt"] == 0:
                    cur.execute("DELETE FROM teledoc_guias WHERE id_guia = ?", (id_guia,))

            cur.execute(
                "UPDATE teledoc_protocolos SET observacao_auditoria = ? WHERE id_protocolo = ?",
                (observacao, id_protocolo),
            )
            prot_atualizado = recalculate_protocol_status(conn, id_protocolo)

        elif acao == "dividir_guia":
            id_pagina = payload.get("id_pagina")
            novo_codigo_guia = str(payload.get("numero_guia") or "").strip()
            if not novo_codigo_guia.startswith("G") or len(novo_codigo_guia) != 14:
                novo_codigo_guia = f"G{str(uuid.uuid4().int)[:13]}"

            cur.execute("SELECT * FROM teledoc_paginas WHERE id_pagina = ?", (id_pagina,))
            pag = row_to_dict(cur.fetchone())
            if not pag:
                raise ValueError("Página não encontrada para divisão de guia.")

            cur.execute("SELECT COUNT(*) AS cnt FROM teledoc_guias WHERE id_protocolo = ?", (id_protocolo,))
            nova_ordem = cur.fetchone()["cnt"] + 1
            nova_guia_id = str(uuid.uuid4())
            cur.execute(
                """
                INSERT INTO teledoc_guias (
                    id_guia, id_protocolo, numero_guia, ordem_no_protocolo,
                    total_paginas, status_guia, criado_em
                ) VALUES (?, ?, ?, ?, 1, 'REINDEXADA', ?)
                """,
                (nova_guia_id, id_protocolo, novo_codigo_guia, nova_ordem, utc_now_iso()),
            )
            cur.execute(
                """
                UPDATE teledoc_paginas
                SET id_guia = ?, tipo_documento = 'TIPO_19', codigo_barras_bruto = ?, numero_pagina_documento = 1
                WHERE id_pagina = ?
                """,
                (nova_guia_id, novo_codigo_guia, id_pagina),
            )
            cur.execute(
                "UPDATE teledoc_protocolos SET observacao_auditoria = ? WHERE id_protocolo = ?",
                (observacao, id_protocolo),
            )
            prot_atualizado = recalculate_protocol_status(conn, id_protocolo)

        elif acao == "aprovar_manual":
            cur.execute(
                "UPDATE teledoc_protocolos SET observacao_auditoria = ? WHERE id_protocolo = ?",
                (observacao, id_protocolo),
            )
            prot_atualizado = recalculate_protocol_status(conn, id_protocolo, force_approve=True)

        else:
            raise ValueError(f"Ação de auditoria desconhecida: {acao}")

        recalculate_batch_status(conn, id_lote)
        return prot_atualizado
