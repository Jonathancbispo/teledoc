"""
Teledoc Engine & REST API Server (Módulo 2)
-------------------------------------------
Implementa integralmente o Contrato REST da Seção 6 do Initialization Pack:
- POST   /api/v1/lotes
- GET    /api/v1/lotes
- POST   /api/v1/lotes/{id_lote}/paginas  (suporta multipart/form-data e JSON)
- POST   /api/v1/lotes/{id_lote}/concluir
- GET    /api/v1/protocolos
- GET    /api/v1/protocolos/{numero_protocolo}
- GET    /api/v1/protocolos/{numero_protocolo}/pdf
- GET    /api/v1/auditoria/divergencias
- PATCH  /api/v1/auditoria/divergencias
- GET    /api/v1/paginas/{id_pagina}/preview.svg
- GET    /api/v1/paginas/{id_pagina}/tiff
- GET    /api/v1/base-xml
- POST   /api/v1/simulador/cenario
"""

from __future__ import annotations

import email.parser
import email.policy
import json
import re
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from backend.database import BASE_DIR, STORAGE_DIR, get_db_connection, init_db, row_to_dict
from backend.enrichment_service import (
    create_batch,
    finalize_batch,
    ingest_scanned_page,
    resolve_audit_divergence,
)
from backend.pdf_tiff_service import (
    generate_consolidated_protocol_pdf,
    generate_page_svg_preview,
)

FRONTEND_DIR = BASE_DIR / "frontend"


def parse_multipart_form_data(content_type: str, body: bytes) -> Tuple[Optional[bytes], Dict[str, str]]:
    """
    Faz o parsing de requisições `multipart/form-data` enviadas pelo Teledoc Capture (WPF)
    extraindo o binário da imagem TIFF/PNG e campos de metadados (`barcode_hint`, `rotation_deg`).
    """
    headers_raw = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("latin-1")
    msg = email.parser.BytesParser(policy=email.policy.default).parsebytes(headers_raw + body)
    file_bytes: Optional[bytes] = None
    fields: Dict[str, str] = {}

    if msg.is_multipart():
        for part in msg.iter_parts():
            disp = part.get("Content-Disposition", "")
            filename = part.get_filename()
            payload = part.get_payload(decode=True)
            if filename or "name=\"file\"" in disp or "name=\"imagem\"" in disp or "name=\"pagina\"" in disp:
                file_bytes = payload
            else:
                m = re.search(r'name="([^"]+)"', disp)
                if m and payload is not None:
                    fields[m.group(1)] = payload.decode("utf-8", errors="replace").strip()
    return file_bytes, fields


def get_protocol_full_tree(numero_protocolo: str) -> Optional[Dict[str, Any]]:
    """
    Monta os detalhes completos de um protocolo, suas guias associadas, a árvore
    documental (Capa -> Guias -> Folhas Anexas) e os metadados da tabela `base_xml_porto`.
    """
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM teledoc_protocolos WHERE numero_protocolo = ?",
            (numero_protocolo,),
        )
        prot = row_to_dict(cur.fetchone())
        if not prot:
            return None

        id_protocolo = prot["id_protocolo"]
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

        cur.execute(
            "SELECT * FROM base_xml_porto WHERE numero_protocolo = ?",
            (numero_protocolo,),
        )
        base_xml = row_to_dict(cur.fetchone())

        # Estrutura em árvore para o Visualizador Web Hub (Capa -> Guias -> Folhas)
        paginas_capa = [p for p in paginas if p["id_guia"] is None]
        guias_tree = []
        for g in guias:
            g_copy = dict(g)
            g_copy["paginas"] = [p for p in paginas if p["id_guia"] == g["id_guia"]]
            guias_tree.append(g_copy)

        prot["base_xml"] = base_xml
        prot["paginas_capa"] = paginas_capa
        prot["guias"] = guias_tree
        prot["todas_paginas"] = paginas
        prot["total_paginas_protocolo"] = len(paginas)
        return prot


def run_simulation_scenario(cenario: str) -> Dict[str, Any]:
    """
    Executa cenários de lote de digitalização no scanner Ricoh fi-8170:
    - `passo5_10_paginas`: Lote de 10 páginas (1 Capa 1984729102 + 2 Guias + 7 Folhas Anexas) -> VALIDADO
    - `divergencia_guias`: Lote com protocolo 2049182736 (espera 3 guias no XML, mas envia apenas 2 físicas) -> DIVERGÊNCIA
    - `protocolo_inexistente`: Lote com protocolo 1900099988 não constante na BASE XML -> PROTOCOLO NÃO ENCONTRADO
    """
    if cenario == "divergencia_guias":
        lote = create_batch(operador_id="OP_CARLOS_02", estacao_trabalho="SCAN-FI8170-EST02")
        id_lote = lote["id_lote"]
        seq = [
            ("2049182736", 0),        # Pág 1: Capa (Protocolo Fleury - espera 3 guias na BASE)
            ("G2026000011111", 0),    # Pág 2: Guia 1
            (None, 0),                # Pág 3: Anexo da Guia 1
            ("G2026000011112", 180),  # Pág 4: Guia 2 (escaneada invertida 180° - corrigida pelo parser!)
            (None, 0),                # Pág 5: Anexo da Guia 2
        ]
        for code, rot in seq:
            ingest_scanned_page(id_lote, barcode_hint=code, rotation_deg=rot)
        return finalize_batch(id_lote)

    elif cenario == "protocolo_inexistente":
        lote = create_batch(operador_id="OP_MARINA_03", estacao_trabalho="SCAN-FI8170-EST03")
        id_lote = lote["id_lote"]
        seq = [
            ("1977788899", 0),       # Pág 1: Capa com erro de impressão / ausente na BASE XML
            ("G8881112223331", 0),   # Pág 2: Guia 1
            (None, 0),               # Pág 3: Anexo da Guia 1
            ("G8881112223332", 0),   # Pág 4: Guia 2
        ]
        for code, rot in seq:
            ingest_scanned_page(id_lote, barcode_hint=code, rotation_deg=rot)
        return finalize_batch(id_lote)

    else:
        # Cenário Padrão (Passo 5 do Roteiro): Lote de 10 páginas com 1 Capa, 2 Guias e Folhas Anexas
        lote = create_batch(operador_id="OP_JONATHAN_01", estacao_trabalho="SCAN-FI8170-EST01")
        id_lote = lote["id_lote"]
        seq = [
            ("1984729102", 0),       # Pág 1: Capa TIPO_18 (Hospital Sírio-Libanês - espera 2 guias)
            (None, 0),               # Pág 2: Folha Anexa TIPO_20 agregada à Capa (borderô físico)
            ("G9821827361829", 0),   # Pág 3: Guia 1 TIPO_19
            (None, 0),               # Pág 4: Folha Anexa TIPO_20 da Guia 1 (Laudo Laboratorial)
            (None, 90),              # Pág 5: Folha Anexa TIPO_20 da Guia 1 (Receituário escaneado a 90°)
            (None, 0),               # Pág 6: Folha Anexa TIPO_20 da Guia 1 (Termo de Consentimento)
            ("G9821827361830", 180), # Pág 7: Guia 2 TIPO_19 (escaneada a 180° - lida automaticamente)
            (None, 0),               # Pág 8: Folha Anexa TIPO_20 da Guia 2 (Exame de Imagem)
            (None, 0),               # Pág 9: Folha Anexa TIPO_20 da Guia 2 (Evolução Clínica)
            (None, 0),               # Pág 10: Folha Anexa TIPO_20 da Guia 2 (Comprovante Atendimento)
        ]
        for code, rot in seq:
            ingest_scanned_page(id_lote, barcode_hint=code, rotation_deg=rot)
        return finalize_batch(id_lote)


class TeledocRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        # Log conciso no console
        pass

    def _send_json(self, status_code: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(
        self,
        status_code: int,
        data: bytes,
        content_type: str,
        filename: Optional[str] = None,
        inline: bool = True,
    ) -> None:
        self.send_response(status_code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        if filename:
            disp_type = "inline" if inline else "attachment"
            self.send_header("Content-Disposition", f'{disp_type}; filename="{filename}"')
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(length) if length > 0 else b""

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        try:
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            query = parse_qs(parsed.query)

            # 1. Frontend SPA (Teledoc Web Hub)
            if path in ("/", "/index.html"):
                index_file = FRONTEND_DIR / "index.html"
                if index_file.exists():
                    self._send_bytes(200, index_file.read_bytes(), "text/html; charset=utf-8")
                else:
                    self._send_json(404, {"erro": "Frontend index.html não encontrado."})
                return

            # 1b. Download direto do Vídeo MP4 Narrado do Piloto
            if path == "/api/v1/video-demo.mp4":
                mp4_file = BASE_DIR / "Teledoc_Demo_Piloto_Narrado.mp4"
                if mp4_file.exists():
                    self._send_bytes(
                        200,
                        mp4_file.read_bytes(),
                        "video/mp4",
                        filename="Teledoc_Demo_Piloto_Narrado.mp4",
                        inline=False,
                    )
                else:
                    self._send_json(404, {"erro": "Arquivo de vídeo MP4 ainda não gerado."})
                return

            # 2. Healthcheck & Resumo Operacional
            if path == "/api/v1/health":
                with get_db_connection() as conn:
                    cur = conn.cursor()
                    cur.execute("SELECT COUNT(*) AS cnt FROM teledoc_lotes")
                    total_lotes = cur.fetchone()["cnt"]
                    cur.execute("SELECT COUNT(*) AS cnt FROM teledoc_protocolos")
                    total_protocolos = cur.fetchone()["cnt"]
                    cur.execute("SELECT COUNT(*) AS cnt FROM teledoc_guias")
                    total_guias = cur.fetchone()["cnt"]
                    cur.execute("SELECT COUNT(*) AS cnt FROM teledoc_paginas")
                    total_paginas = cur.fetchone()["cnt"]
                    cur.execute(
                        """
                        SELECT COUNT(*) AS cnt FROM teledoc_protocolos
                        WHERE status_validacao IN ('STATUS_DIVERGENCIA_GUIAS', 'STATUS_PROTOCOLO_NAO_ENCONTRADO')
                        """
                    )
                    total_divergencias = cur.fetchone()["cnt"]

                self._send_json(
                    200,
                    {
                        "status": "ONLINE",
                        "servico": "Teledoc Engine & API v1.0",
                        "scanner_profile": "004 : BPO (Ricoh/Fujitsu fi-8170 - 300 DPI)",
                        "metricas": {
                            "total_lotes": total_lotes,
                            "total_protocolos": total_protocolos,
                            "total_guias": total_guias,
                            "total_paginas": total_paginas,
                            "total_divergencias": total_divergencias,
                        },
                    },
                )
                return

            # 3. GET /api/v1/lotes
            if path == "/api/v1/lotes":
                with get_db_connection() as conn:
                    cur = conn.cursor()
                    cur.execute("SELECT * FROM teledoc_lotes ORDER BY data_inicio_captura DESC")
                    lotes = [dict(r) for r in cur.fetchall()]
                    for l in lotes:
                        cur.execute(
                            "SELECT * FROM teledoc_protocolos WHERE id_lote = ? ORDER BY criado_em ASC",
                            (l["id_lote"],),
                        )
                        l["protocolos"] = [dict(p) for p in cur.fetchall()]
                self._send_json(200, {"total": len(lotes), "items": lotes})
                return

            # 4. GET /api/v1/protocolos (com filtros por prestador, protocolo, guia, status, data)
            if path == "/api/v1/protocolos":
                q_search = (query.get("q", [""])[0] or "").strip()
                f_status = (query.get("status", [""])[0] or "").strip()
                f_prestador = (query.get("prestador", [""])[0] or "").strip()
                f_guia = (query.get("guia", [""])[0] or "").strip()

                sql = "SELECT DISTINCT p.* FROM teledoc_protocolos p LEFT JOIN teledoc_guias g ON g.id_protocolo = p.id_protocolo WHERE 1=1"
                params: List[Any] = []

                if q_search:
                    sql += " AND (p.numero_protocolo LIKE ? OR p.nome_prestador LIKE ? OR p.lote_origem LIKE ? OR g.numero_guia LIKE ?)"
                    like_q = f"%{q_search}%"
                    params.extend([like_q, like_q, like_q, like_q])
                if f_status:
                    sql += " AND p.status_validacao = ?"
                    params.append(f_status)
                if f_prestador:
                    sql += " AND (p.nome_prestador LIKE ? OR p.codigo_prestador LIKE ?)"
                    params.extend([f"%{f_prestador}%", f"%{f_prestador}%"])
                if f_guia:
                    sql += " AND g.numero_guia LIKE ?"
                    params.append(f"%{f_guia}%")

                sql += " ORDER BY p.criado_em DESC"

                with get_db_connection() as conn:
                    cur = conn.cursor()
                    cur.execute(sql, params)
                    rows = [dict(r) for r in cur.fetchall()]
                    for r in rows:
                        cur.execute(
                            "SELECT COUNT(*) AS cnt FROM teledoc_paginas WHERE id_protocolo = ?",
                            (r["id_protocolo"],),
                        )
                        r["total_paginas"] = cur.fetchone()["cnt"]
                self._send_json(200, {"total": len(rows), "items": rows})
                return

            # 5. GET /api/v1/protocolos/{numero_protocolo}/pdf
            m_pdf = re.match(r"^/api/v1/protocolos/([^/]+)/pdf$", path)
            if m_pdf:
                numero_prot = m_pdf.group(1)
                tree = get_protocol_full_tree(numero_prot)
                if not tree:
                    self._send_json(404, {"erro": f"Protocolo {numero_prot} não encontrado."})
                    return

                pdf_bytes = generate_consolidated_protocol_pdf(
                    tree, tree["guias"], tree["todas_paginas"]
                )
                inline = query.get("download", ["0"])[0] != "1"
                self._send_bytes(
                    200,
                    pdf_bytes,
                    "application/pdf",
                    filename=f"Protocolo_{numero_prot}_Consolidado.pdf",
                    inline=inline,
                )
                return

            # 6. GET /api/v1/protocolos/{numero_protocolo}
            m_prot = re.match(r"^/api/v1/protocolos/([^/]+)$", path)
            if m_prot:
                numero_prot = m_prot.group(1)
                tree = get_protocol_full_tree(numero_prot)
                if not tree:
                    self._send_json(404, {"erro": f"Protocolo {numero_prot} não encontrado."})
                    return
                self._send_json(200, tree)
                return

            # 7. GET /api/v1/auditoria/divergencias
            if path == "/api/v1/auditoria/divergencias":
                with get_db_connection() as conn:
                    cur = conn.cursor()
                    cur.execute(
                        """
                        SELECT * FROM teledoc_protocolos
                        WHERE status_validacao IN ('STATUS_DIVERGENCIA_GUIAS', 'STATUS_PROTOCOLO_NAO_ENCONTRADO')
                        ORDER BY criado_em DESC
                        """
                    )
                    divergencias = []
                    for r in cur.fetchall():
                        d = get_protocol_full_tree(r["numero_protocolo"])
                        if d:
                            divergencias.append(d)

                    cur.execute("SELECT * FROM base_xml_porto ORDER BY id ASC")
                    catalogo_base = [dict(b) for b in cur.fetchall()]

                self._send_json(
                    200,
                    {
                        "total_divergencias": len(divergencias),
                        "items": divergencias,
                        "catalogo_base_xml": catalogo_base,
                    },
                )
                return

            # 8. GET /api/v1/base-xml
            if path == "/api/v1/base-xml":
                with get_db_connection() as conn:
                    cur = conn.cursor()
                    cur.execute("SELECT * FROM base_xml_porto ORDER BY id ASC")
                    items = [dict(r) for r in cur.fetchall()]
                self._send_json(200, {"total": len(items), "items": items})
                return

            # 9. GET /api/v1/paginas/{id_pagina}/preview.svg
            m_svg = re.match(r"^/api/v1/paginas/([^/]+)/preview\.svg$", path)
            if m_svg:
                id_pagina = m_svg.group(1)
                with get_db_connection() as conn:
                    cur = conn.cursor()
                    cur.execute("SELECT * FROM teledoc_paginas WHERE id_pagina = ?", (id_pagina,))
                    pag = row_to_dict(cur.fetchone())
                    if not pag:
                        self._send_json(404, {"erro": "Página não encontrada."})
                        return
                    cur.execute(
                        "SELECT * FROM teledoc_protocolos WHERE id_protocolo = ?",
                        (pag["id_protocolo"],),
                    )
                    prot = row_to_dict(cur.fetchone()) or {}
                    guia = None
                    if pag.get("id_guia"):
                        cur.execute(
                            "SELECT * FROM teledoc_guias WHERE id_guia = ?",
                            (pag["id_guia"],),
                        )
                        guia = row_to_dict(cur.fetchone())

                svg_str = generate_page_svg_preview(pag, prot, guia)
                self._send_bytes(200, svg_str.encode("utf-8"), "image/svg+xml; charset=utf-8")
                return

            # 10. GET /api/v1/paginas/{id_pagina}/tiff
            m_tiff = re.match(r"^/api/v1/paginas/([^/]+)/tiff$", path)
            if m_tiff:
                id_pagina = m_tiff.group(1)
                with get_db_connection() as conn:
                    cur = conn.cursor()
                    cur.execute("SELECT * FROM teledoc_paginas WHERE id_pagina = ?", (id_pagina,))
                    pag = row_to_dict(cur.fetchone())
                    if not pag:
                        self._send_json(404, {"erro": "Página não encontrada."})
                        return
                tiff_file = BASE_DIR / pag["caminho_storage_tiff"]
                if not tiff_file.exists():
                    self._send_json(404, {"erro": "Arquivo TIFF físico não encontrado."})
                    return
                self._send_bytes(
                    200,
                    tiff_file.read_bytes(),
                    "image/tiff",
                    filename=tiff_file.name,
                    inline=False,
                )
                return

            self._send_json(404, {"erro": f"Rota não encontrada: {path}"})
        except Exception as exc:
            traceback.print_exc()
            self._send_json(500, {"erro": str(exc)})

    def do_POST(self) -> None:
        try:
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/")
            content_type = self.headers.get("Content-Type", "")
            raw_body = self._read_body()

            # 1. POST /api/v1/lotes
            if path == "/api/v1/lotes":
                payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
                operador_id = payload.get("operador_id") or "OP_PADRAO_01"
                estacao = payload.get("estacao_trabalho") or "SCAN-FI8170-01"
                novo_lote = create_batch(operador_id=operador_id, estacao_trabalho=estacao)
                self._send_json(201, novo_lote)
                return

            # 2. POST /api/v1/lotes/{id_lote}/paginas
            m_pag = re.match(r"^/api/v1/lotes/([^/]+)/paginas$", path)
            if m_pag:
                id_lote = m_pag.group(1)
                image_bytes: Optional[bytes] = None
                barcode_hint: Optional[str] = None
                rotation_deg: int = 0

                if "multipart/form-data" in content_type:
                    image_bytes, fields = parse_multipart_form_data(content_type, raw_body)
                    barcode_hint = fields.get("barcode_hint") or fields.get("codigo_barras") or None
                    rotation_deg = int(fields.get("rotation_deg") or "0")
                elif "application/json" in content_type:
                    payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
                    barcode_hint = payload.get("barcode_hint") or payload.get("codigo_barras")
                    rotation_deg = int(payload.get("rotation_deg") or 0)
                else:
                    image_bytes = raw_body if raw_body else None

                pagina = ingest_scanned_page(
                    id_lote=id_lote,
                    image_bytes=image_bytes,
                    barcode_hint=barcode_hint,
                    rotation_deg=rotation_deg,
                )
                self._send_json(
                    200,
                    {
                        "mensagem": "Imagem recebida, classificada por Code 128 e indexada no lote.",
                        "pagina": pagina,
                    },
                )
                return

            # 3. POST /api/v1/lotes/{id_lote}/concluir
            m_conc = re.match(r"^/api/v1/lotes/([^/]+)/concluir$", path)
            if m_conc:
                id_lote = m_conc.group(1)
                resultado = finalize_batch(id_lote)
                self._send_json(200, resultado)
                return

            # 4. POST /api/v1/simulador/cenario
            if path == "/api/v1/simulador/cenario":
                payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
                cenario = payload.get("cenario") or "passo5_10_paginas"
                resultado = run_simulation_scenario(cenario)
                self._send_json(200, resultado)
                return

            # 5. POST /api/v1/base-xml (Permite cadastrar/importar novo XML na tabela BASE)
            if path == "/api/v1/base-xml":
                payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
                num_prot = str(payload.get("numero_protocolo") or "").strip()
                if not num_prot:
                    self._send_json(400, {"erro": "O campo numero_protocolo é obrigatório."})
                    return
                with get_db_connection() as conn:
                    cur = conn.cursor()
                    cur.execute(
                        """
                        INSERT OR REPLACE INTO base_xml_porto (
                            numero_protocolo, codigo_prestador, nome_prestador,
                            numero_lote, quantidade_guias, valor_total, data_lote, processado
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                        """,
                        (
                            num_prot,
                            payload.get("codigo_prestador") or "00.000.000/0001-00",
                            payload.get("nome_prestador") or "Prestador Credenciado Porto Seguro",
                            payload.get("numero_lote") or "LT-2026-XML",
                            int(payload.get("quantidade_guias") or 1),
                            float(payload.get("valor_total") or 1000.0),
                            payload.get("data_lote") or "2026-11-14T12:00:00-03:00",
                        ),
                    )
                self._send_json(201, {"mensagem": f"Protocolo {num_prot} inserido na tabela BASE com sucesso."})
                return

            self._send_json(404, {"erro": f"Endpoint POST não encontrado: {path}"})
        except Exception as exc:
            traceback.print_exc()
            self._send_json(500, {"erro": str(exc)})

    def do_PATCH(self) -> None:
        try:
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/")
            raw_body = self._read_body()
            payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}

            if path == "/api/v1/auditoria/divergencias":
                atualizado = resolve_audit_divergence(payload)
                tree = get_protocol_full_tree(atualizado["numero_protocolo"])
                self._send_json(
                    200,
                    {
                        "mensagem": "Conciliação e reprocessamento executados com sucesso.",
                        "protocolo": tree,
                    },
                )
                return

            self._send_json(404, {"erro": f"Endpoint PATCH não encontrado: {path}"})
        except Exception as exc:
            traceback.print_exc()
            self._send_json(400, {"erro": str(exc)})


def start_server(host: str = "127.0.0.1", port: int = 8000) -> None:
    init_db()
    server = ThreadingHTTPServer((host, port), TeledocRequestHandler)
    print(f"[TELEDOC ENGINE] Servidor ativo em http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    start_server()
