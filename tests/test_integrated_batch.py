"""
Passo 5: Suite de Testes de Lote Integrados do Projeto Teledoc
--------------------------------------------------------------
Valida de ponta a ponta:
1. Leitura óptica Code 128 em imagens TIFF reais nas orientações 0°, 90°, 180° e 270°.
2. Entrada de um lote de 10 páginas via HTTP multipart/form-data (1 Capa TIPO_18,
   2 Guias TIPO_19 e 7 Folhas Anexas TIPO_20) sem dicas de texto, lendo apenas os pixels do TIFF.
3. Criação da árvore documental hierárquica (Capa -> Guias -> Folhas).
4. Enriquecimento automático com a tabela `base_xml_porto` (XML Porto Seguro).
5. Detecção e tratamento de divergências (`STATUS_DIVERGENCIA_GUIAS` e `STATUS_PROTOCOLO_NAO_ENCONTRADO`).
6. Geração do PDF consolidado multi-página (`/api/v1/protocolos/{numero_protocolo}/pdf`).
"""

from __future__ import annotations

import json
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from http.server import ThreadingHTTPServer
from backend.barcode_engine import TeledocBarcodeParser, build_synthetic_tiff_with_barcode
from backend.database import DB_PATH, init_db
from backend.server import TeledocRequestHandler


def make_multipart_body(field_name: str, filename: str, file_bytes: bytes) -> tuple[str, bytes]:
    boundary = "----TeledocBoundary7MA4YWxkTrZu0gW"
    lines = [
        f"--{boundary}\r\n".encode("ascii"),
        f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'.encode("ascii"),
        b"Content-Type: image/tiff\r\n\r\n",
        file_bytes,
        f"\r\n--{boundary}--\r\n".encode("ascii"),
    ]
    return f"multipart/form-data; boundary={boundary}", b"".join(lines)


def http_request(method: str, url: str, data: bytes | None = None, headers: dict | None = None) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status, resp.read()


def run_all_tests() -> None:
    print("=" * 78)
    print(" INICIANDO TESTES INTEGRADOS DO ECOSSISTEMA TELEDOC (PASSOS 1 A 5)")
    print("=" * 78)

    # Limpa banco anterior para teste reproduzível e popula cenários iniciais
    if DB_PATH.exists():
        DB_PATH.unlink()
    init_db()

    # -------------------------------------------------------------------------
    # TESTE 1: Decodificação Óptica Real de Code 128 em 0°, 90°, 180° e 270°
    # -------------------------------------------------------------------------
    print("\n[TESTE 1] Validando motor óptico Code 128 (0°, 90°, 180°, 270°) sem hints...")
    for angle in (0, 90, 180, 270):
        tiff_capa = build_synthetic_tiff_with_barcode("1984729102", rotation_deg=angle)
        parsed = TeledocBarcodeParser.parse_image(tiff_capa, barcode_hint=None)
        assert parsed.tipo_documento == "TIPO_18", f"Falha tipo em {angle}°: {parsed}"
        assert parsed.codigo_barras_bruto == "1984729102", f"Falha código em {angle}°: {parsed}"
        assert parsed.orientacao_detectada == angle, f"Falha ângulo {angle}°: {parsed}"
        print(
            f"  ✓ Rotação {angle:3d}° -> Lido: {parsed.codigo_barras_bruto} "
            f"({parsed.tipo_documento}) | Confiança: {parsed.confianca_barcode:.2f}%"
        )

    # Inicia servidor HTTP em porta de teste
    server = ThreadingHTTPServer(("127.0.0.1", 8099), TeledocRequestHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    base_url = "http://127.0.0.1:8099"

    try:
        # ---------------------------------------------------------------------
        # TESTE 2 (PASSO 5): Simulação de Lote de 10 Páginas (1 Capa, 2 Guias, 7 Anexos)
        # ---------------------------------------------------------------------
        print("\n[TESTE 2] Executando Passo 5: Lote de 10 páginas via multipart/form-data...")
        status, body = http_request(
            "POST",
            f"{base_url}/api/v1/lotes",
            data=json.dumps({"operador_id": "OP_TESTE_PASSO5", "estacao_trabalho": "FI8170-LAB"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        assert status == 201
        lote = json.loads(body.decode("utf-8"))
        id_lote = lote["id_lote"]
        print(f"  ✓ Lote iniciado: {id_lote}")

        # Sequência de 10 páginas escaneadas (enviando apenas os bytes da imagem TIFF com barras Code 128!)
        pages_spec = [
            ("1984729102", 0, "TIPO_18", "Capa do Protocolo (10 dígitos iniciando com 19)"),
            (None, 0, "TIPO_20", "Folha Anexa à Capa (Borderô de entrega)"),
            ("G9821827361829", 0, "TIPO_19", "Guia Médica #1 (G + 13 dígitos)"),
            (None, 0, "TIPO_20", "Folha Anexa #1 da Guia 1"),
            (None, 90, "TIPO_20", "Folha Anexa #2 da Guia 1 (Escaneada a 90°)"),
            (None, 0, "TIPO_20", "Folha Anexa #3 da Guia 1"),
            ("G9821827361830", 180, "TIPO_19", "Guia Médica #2 (Escaneada invertida a 180°)"),
            (None, 0, "TIPO_20", "Folha Anexa #1 da Guia 2"),
            (None, 0, "TIPO_20", "Folha Anexa #2 da Guia 2"),
            (None, 0, "TIPO_20", "Folha Anexa #3 da Guia 2"),
        ]

        for idx, (barcode_str, rot, expected_tipo, desc) in enumerate(pages_spec, start=1):
            tiff_bytes = build_synthetic_tiff_with_barcode(barcode_text=barcode_str, rotation_deg=rot)
            ctype, mp_body = make_multipart_body("file", f"pagina_{idx:02d}.tif", tiff_bytes)
            st, resp_b = http_request(
                "POST",
                f"{base_url}/api/v1/lotes/{id_lote}/paginas",
                data=mp_body,
                headers={"Content-Type": ctype},
            )
            assert st == 200
            pag_resp = json.loads(resp_b.decode("utf-8"))["pagina"]
            assert pag_resp["tipo_documento"] == expected_tipo, (
                f"Página {idx} esperava {expected_tipo}, obteve {pag_resp['tipo_documento']}"
            )
            assert pag_resp["codigo_barras_bruto"] == barcode_str
            print(
                f"  ✓ Pág #{idx:02d} -> {pag_resp['tipo_documento']} | "
                f"Barcode: {str(pag_resp['codigo_barras_bruto'] or 'ANEXO'):14s} | {desc}"
            )

        # Conclui o lote e aciona o enriquecimento e validação com a tabela BASE
        st, conc_b = http_request(
            "POST",
            f"{base_url}/api/v1/lotes/{id_lote}/concluir",
            data=b"{}",
            headers={"Content-Type": "application/json"},
        )
        assert st == 200
        lote_concluido = json.loads(conc_b.decode("utf-8"))
        assert lote_concluido["status_processamento"] == "CONCLUIDO"
        assert lote_concluido["total_imagens_capturadas"] == 10

        # Consulta a árvore documental completa do protocolo 1984729102
        st, tree_b = http_request("GET", f"{base_url}/api/v1/protocolos/1984729102")
        assert st == 200
        tree = json.loads(tree_b.decode("utf-8"))

        assert tree["status_validacao"] == "STATUS_INDEXADO_COM_SUCESSO"
        assert tree["nome_prestador"] == "Hospital Sírio-Libanês - Unidade Bela Vista"
        assert tree["codigo_prestador"] == "61.384.920/0001-12"
        assert tree["lote_origem"] == "LT-2026-00918"
        assert tree["qtd_guias_esperada"] == 2
        assert tree["qtd_guias_digitalizadas"] == 2
        assert float(tree["valor_total"]) == 4850.75
        assert tree["total_paginas_protocolo"] == 10
        assert len(tree["paginas_capa"]) == 2
        assert len(tree["guias"]) == 2
        assert len(tree["guias"][0]["paginas"]) == 4
        assert len(tree["guias"][1]["paginas"]) == 4

        print("\n[ÁRVORE DOCUMENTAL VERIFICADA COM SUCESSO]")
        print(f"└── Protocolo: {tree['numero_protocolo']} ({tree['nome_prestador']})")
        print(f"    ├── Capa + Anexos de Capa ({len(tree['paginas_capa'])} páginas)")
        for g in tree["guias"]:
            print(f"    ├── Guia: {g['numero_guia']} ({len(g['paginas'])} páginas)")

        # Verifica download do PDF consolidado
        st_pdf, pdf_bytes = http_request("GET", f"{base_url}/api/v1/protocolos/1984729102/pdf")
        assert st_pdf == 200
        assert pdf_bytes.startswith(b"%PDF-1.4")
        print(f"  ✓ PDF Consolidado gerado e validado ({len(pdf_bytes):,} bytes)")

        # ---------------------------------------------------------------------
        # TESTE 3: Cenários de Exceção e Auditoria (Divergência de Guias)
        # ---------------------------------------------------------------------
        print("\n[TESTE 3] Simulando cenários de divergência para a Central de Auditoria...")
        http_request(
            "POST",
            f"{base_url}/api/v1/simulador/cenario",
            data=json.dumps({"cenario": "divergencia_guias"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        http_request(
            "POST",
            f"{base_url}/api/v1/simulador/cenario",
            data=json.dumps({"cenario": "protocolo_inexistente"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )

        st_aud, aud_b = http_request("GET", f"{base_url}/api/v1/auditoria/divergencias")
        assert st_aud == 200
        aud_data = json.loads(aud_b.decode("utf-8"))
        assert aud_data["total_divergencias"] == 2
        print(f"  ✓ Fila de Auditoria detectou {aud_data['total_divergencias']} protocolos com exceção:")
        for item in aud_data["items"]:
            print(
                f"    - Protocolo {item['numero_protocolo']}: {item['status_validacao']} "
                f"(Guias Físicas: {item['qtd_guias_digitalizadas']} vs Esperadas XML: {item['qtd_guias_esperada']})"
            )

        print("\n" + "=" * 78)
        print(" TODOS OS TESTES DO PROJETO TELEDOC PASSARAM COM 100% DE SUCESSO!")
        print("=" * 78)

    finally:
        server.shutdown()


if __name__ == "__main__":
    run_all_tests()
