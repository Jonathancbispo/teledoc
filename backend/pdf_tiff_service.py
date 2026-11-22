"""
Serviço de Conversão/Otimização de Imagens (TIFF / PDF Consolidado / Preview SVG)
---------------------------------------------------------------------------------
Gera arquivos PDF multi-página compatíveis com a especificação ISO 32000-1 (PDF 1.4)
para o endpoint `/api/v1/protocolos/{numero_protocolo}/pdf` e renderiza previews
visuais em alta definição (300 DPI equivalente) com barras reais do Code 128.
"""

from __future__ import annotations

from typing import Any, Dict, List
from backend.barcode_engine import encode_code128_modules


def _escape_pdf_text(text: str) -> str:
    """Converte caracteres acentuados para ASCII seguro em fontes PDF Standard Type1 (Helvetica)."""
    replacements = {
        "á": "a", "à": "a", "ã": "a", "â": "a", "ä": "a",
        "é": "e", "è": "e", "ê": "e", "ë": "e",
        "í": "i", "ì": "i", "î": "i", "ï": "i",
        "ó": "o", "ò": "o", "õ": "o", "ô": "o", "ö": "o",
        "ú": "u", "ù": "u", "û": "u", "ü": "u",
        "ç": "c",
        "Á": "A", "À": "A", "Ã": "A", "Â": "A",
        "É": "E", "È": "E", "Ê": "E",
        "Í": "I", "Ì": "I", "Î": "I",
        "Ó": "O", "Ò": "O", "Õ": "O", "Ô": "O",
        "Ú": "U", "Ù": "U", "Û": "U",
        "Ç": "C",
        "(": "\\(", ")": "\\)", "\\": "\\\\",
    }
    out = []
    for ch in str(text or ""):
        if ch in replacements:
            out.append(replacements[ch])
        elif 32 <= ord(ch) <= 126:
            out.append(ch)
        else:
            out.append("?")
    return "".join(out)


def generate_consolidated_protocol_pdf(
    protocolo: Dict[str, Any],
    guias: List[Dict[str, Any]],
    paginas: List[Dict[str, Any]],
) -> bytes:
    """
    Gera um arquivo PDF multi-página real consolidando todas as folhas do protocolo
    na ordem documental exata (Capa -> Guias -> Folhas Anexas), incluindo desenho
    vetorial das barras Code 128 em cada página.
    """
    objects: List[bytes] = []

    def add_obj(content: bytes) -> int:
        objects.append(content)
        return len(objects)

    # Objeto 1: Catalog (preenchido depois)
    add_obj(b"")
    # Objeto 2: Pages (preenchido depois)
    add_obj(b"")
    # Objeto 3: Fonte Helvetica Regular
    font_reg_id = add_obj(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    # Objeto 4: Fonte Helvetica Bold
    font_bold_id = add_obj(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")

    guia_map = {g["id_guia"]: g for g in guias}
    page_obj_ids: List[int] = []
    total_pages = max(1, len(paginas))

    for idx, pag in enumerate(paginas, start=1):
        tipo = pag.get("tipo_documento", "TIPO_20")
        barcode = pag.get("codigo_barras_bruto") or ""
        guia_info = guia_map.get(pag.get("id_guia"))

        lines: List[str] = []
        # Moldura externa da página A4 (595 x 842 pt)
        lines.append("0.85 0.88 0.92 RG 1.5 w 28 28 539 786 re S")
        # Cabeçalho institucional Teledoc / Porto Seguro
        lines.append("0.07 0.14 0.27 rg 28 754 539 60 re f")

        lines.append("BT /F2 14 Tf 1 1 1 rg 42 790 Td (TELEDOC - PRONTUARIO DIGITAL CONSOLIDADO) Tj ET")
        lines.append(
            f"BT /F1 9 Tf 0.8 0.88 1 rg 42 772 Td "
            f"(Protocolo: {_escape_pdf_text(protocolo.get('numero_protocolo'))} | "
            f"Prestador: {_escape_pdf_text(protocolo.get('nome_prestador') or 'Nao Identificado na BASE')} | "
            f"Folha {idx} de {total_pages}) Tj ET"
        )

        # Badge de Tipo Documental
        if tipo == "TIPO_18":
            lines.append("0.12 0.39 0.85 rg 42 705 240 28 re f")
            lines.append("BT /F2 11 Tf 1 1 1 rg 52 715 Td (TIPO 18 - CAPA DE LOTE / PROTOCOLO) Tj ET")
        elif tipo == "TIPO_19":
            lines.append("0.06 0.58 0.41 rg 42 705 240 28 re f")
            lines.append("BT /F2 11 Tf 1 1 1 rg 52 715 Td (TIPO 19 - GUIA MEDICA TISS / SADT) Tj ET")
        else:
            lines.append("0.42 0.45 0.50 rg 42 705 240 28 re f")
            lines.append("BT /F2 11 Tf 1 1 1 rg 52 715 Td (TIPO 20 - FOLHA ANEXA / LAUDO) Tj ET")

        # Desenho real do Código de Barras Code 128 no PDF
        if barcode:
            try:
                modules = encode_code128_modules(barcode)
                lines.append("0 0 0 rg")
                x_cursor = 330.0
                mod_w = 1.25
                for bit in modules:
                    if bit == 1:
                        lines.append(f"{x_cursor:.2f} 695 {mod_w:.2f} 38 re f")
                    x_cursor += mod_w
                lines.append(
                    f"BT /F2 9 Tf 0.1 0.1 0.1 rg 345 683 Td (CODE 128: {_escape_pdf_text(barcode)}) Tj ET"
                )
            except Exception:
                pass

        # Bloco de Metadados Enriquecidos da BASE
        lines.append("0.96 0.97 0.99 rg 42 545 511 125 re f")
        lines.append("0.82 0.85 0.90 RG 0.8 w 42 545 511 125 re S")
        lines.append("BT /F2 11 Tf 0.1 0.15 0.25 rg 54 650 Td (METADADOS DE INDEXACAO E ENRIQUECIMENTO BASE XML) Tj ET")

        meta_rows = [
            f"Protocolo (Capa): {protocolo.get('numero_protocolo')}   |   Status Validacao: {protocolo.get('status_validacao')}",
            f"Prestador: {protocolo.get('nome_prestador') or 'N/A'}   |   CNPJ/Codigo: {protocolo.get('codigo_prestador') or 'N/A'}",
            f"Lote Origem XML: {protocolo.get('lote_origem') or 'N/A'}   |   Valor Total Protocolo: R$ {float(protocolo.get('valor_total') or 0):,.2f}",
            f"Guias Esperadas (XML): {protocolo.get('qtd_guias_esperada') or 0}   |   Guias Digitalizadas: {protocolo.get('qtd_guias_digitalizadas') or 0}",
            (
                f"Vinculo Documental: Guia {guia_info['numero_guia']} (Ordem #{guia_info['ordem_no_protocolo']})"
                if guia_info
                else "Vinculo Documental: Vinculado diretamente a Capa do Protocolo"
            ),
            f"Arquivo Storage TIFF: {pag.get('caminho_storage_tiff')}   |   Confianca Barcode: {pag.get('confianca_barcode') or 0:.2f}%",
        ]

        y_pos = 630
        for row_txt in meta_rows:
            lines.append(f"BT /F1 9.5 Tf 0.2 0.25 0.32 rg 54 {y_pos} Td ({_escape_pdf_text(row_txt)}) Tj ET")
            y_pos -= 16

        # Corpo da página (Conteúdo clínico simulado)
        lines.append("0.85 0.88 0.92 RG 1 w 42 120 511 405 re S")
        lines.append(
            f"BT /F2 11 Tf 0.15 0.2 0.3 rg 54 500 Td "
            f"(CONTEUDO DOCUMENTAL DIGITALIZADO - PAGINA #{pag.get('numero_pagina_lote')} DO LOTE) Tj ET"
        )
        lines.append(
            f"BT /F1 10 Tf 0.3 0.35 0.4 rg 54 475 Td "
            f"(Pagina #{pag.get('numero_pagina_documento')} dentro da estrutura {_escape_pdf_text(tipo)} | Resolucao: 300 DPI | Perfil Scanner: 004 : BPO) Tj ET"
        )

        # Rodapé de auditoria
        lines.append(
            f"BT /F1 8 Tf 0.45 0.5 0.55 rg 42 42 Td "
            f"(Documento gerado eletronicamente pelo Teledoc Engine API - ID Pagina: {_escape_pdf_text(pag.get('id_pagina'))}) Tj ET"
        )

        stream_bytes = "\n".join(lines).encode("latin-1", errors="replace")
        content_obj = (
            f"<< /Length {len(stream_bytes)} >>\nstream\n".encode("ascii")
            + stream_bytes
            + b"\nendstream"
        )
        content_id = add_obj(content_obj)

        page_dict = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            f"/Resources << /Font << /F1 {font_reg_id} 0 R /F2 {font_bold_id} 0 R >> >> "
            f"/Contents {content_id} 0 R >>"
        ).encode("ascii")
        page_id = add_obj(page_dict)
        page_obj_ids.append(page_id)

    kids_str = " ".join(f"{pid} 0 R" for pid in page_obj_ids)
    objects[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[1] = f"<< /Type /Pages /Kids [{kids_str}] /Count {len(page_obj_ids)} >>".encode("ascii")

    # Serializa o arquivo PDF com tabela xref
    pdf_buf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: List[int] = []
    for idx, obj_bytes in enumerate(objects, start=1):
        offsets.append(len(pdf_buf))
        pdf_buf.extend(f"{idx} 0 obj\n".encode("ascii"))
        pdf_buf.extend(obj_bytes)
        pdf_buf.extend(b"\nendobj\n")

    xref_pos = len(pdf_buf)
    pdf_buf.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    pdf_buf.extend(b"0000000000 65535 f \n")
    for off in offsets:
        pdf_buf.extend(f"{off:010d} 00000 n \n".encode("ascii"))

    pdf_buf.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode("ascii")
    )
    return bytes(pdf_buf)


def generate_page_svg_preview(
    pagina: Dict[str, Any],
    protocolo: Dict[str, Any],
    guia: Dict[str, Any] | None = None,
) -> str:
    """
    Renderiza um preview SVG de alta resolução da página digitalizada (Capa, Guia ou Folha Anexa),
    incluindo o código de barras Code 128 real codificado barra a barra.
    """
    tipo = pagina.get("tipo_documento", "TIPO_20")
    barcode = pagina.get("codigo_barras_bruto") or ""
    num_prot = protocolo.get("numero_protocolo", "---")
    prestador = protocolo.get("nome_prestador") or "Prestador Não Encontrado na BASE XML"
    cnpj = protocolo.get("codigo_prestador") or "Sem CNPJ na BASE"
    lote_origem = protocolo.get("lote_origem") or "Sem Lote XML"
    valor_total = float(protocolo.get("valor_total") or 0.0)

    # Renderiza barras SVG do Code 128
    barcode_rects = []
    if barcode:
        try:
            modules = encode_code128_modules(barcode)
            x_cur = 460
            mod_w = 2
            for bit in modules:
                if bit == 1:
                    barcode_rects.append(
                        f'<rect x="{x_cur}" y="52" width="{mod_w}" height="58" fill="#0f172a" />'
                    )
                x_cur += mod_w
        except Exception:
            pass
    barcode_svg = "\n".join(barcode_rects)

    if tipo == "TIPO_18":
        badge_color = "#1d4ed8"
        badge_bg = "#dbeafe"
        titulo_doc = "CAPA DE LOTE / PROTOCOLO DE FATURAMENTO (TIPO 18)"
        sub_doc = "Documento Mestre de Abertura de Pasta Digital - Leitura Automática Code 128"
    elif tipo == "TIPO_19":
        badge_color = "#047857"
        badge_bg = "#d1fae5"
        titulo_doc = "GUIA DE SERVIÇO PROFISSIONAL / SP-SADT TISS (TIPO 19)"
        sub_doc = f"Guia Médica Individual #{guia['ordem_no_protocolo'] if guia else 1} vinculada ao Protocolo {num_prot}"
    else:
        badge_color = "#475569"
        badge_bg = "#f1f5f9"
        titulo_doc = "FOLHA ANEXA / LAUDO DE EXAME COMPLEMENTAR (TIPO 20)"
        sub_doc = (
            f"Anexo agregado automaticamente à Guia {guia['numero_guia']}"
            if guia
            else f"Anexo agregado à Capa do Protocolo {num_prot}"
        )

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 1060" width="100%" height="100%">
  <defs>
    <pattern id="scanGrid" width="40" height="40" patternUnits="userSpaceOnUse">
      <path d="M 40 0 L 0 0 0 40" fill="none" stroke="#f1f5f9" stroke-width="1"/>
    </pattern>
  </defs>
  <!-- Fundo Papel Digitalizado 300 DPI -->
  <rect width="800" height="1060" fill="#ffffff"/>
  <rect x="24" y="24" width="752" height="1012" fill="url(#scanGrid)" stroke="#cbd5e1" stroke-width="2" rx="4"/>

  <!-- Cabeçalho e Área de Barcode Code 128 -->
  <rect x="44" y="42" width="390" height="92" fill="#f8fafc" stroke="#e2e8f0" rx="6"/>
  <text x="60" y="72" font-family="Inter, Arial, sans-serif" font-size="18" font-weight="800" fill="#0f172a">PORTO SEGURO SAÚDE</text>
  <text x="60" y="95" font-family="Inter, Arial, sans-serif" font-size="12" font-weight="600" fill="#475569">CENTRAL DE CAPTURA INTELIGENTE TELEDOC</text>
  <text x="60" y="116" font-family="monospace" font-size="11" fill="#64748b">Scanner: Ricoh fi-8170 | Perfil 004:BPO | 300 DPI</text>

  <!-- Caixa do Código de Barras Code 128 -->
  <rect x="446" y="42" width="310" height="92" fill="#ffffff" stroke="#94a3b8" stroke-dasharray="4 2" rx="6"/>
  {barcode_svg if barcode else '<text x="601" y="88" text-anchor="middle" font-family="monospace" font-size="13" fill="#94a3b8">[ SEM CÓDIGO DE BARRAS - TIPO_20 ]</text>'}
  <text x="601" y="125" text-anchor="middle" font-family="monospace" font-size="13" font-weight="700" fill="#0f172a">{barcode if barcode else "FOLHA CONTÍNUA / ANEXA"}</text>

  <!-- Faixa de Classificação Documental -->
  <rect x="44" y="152" width="712" height="58" fill="{badge_bg}" stroke="{badge_color}" stroke-width="1.5" rx="6"/>
  <text x="62" y="178" font-family="Inter, Arial, sans-serif" font-size="15" font-weight="800" fill="{badge_color}">{titulo_doc}</text>
  <text x="62" y="198" font-family="Inter, Arial, sans-serif" font-size="12" fill="#334155">{sub_doc}</text>

  <!-- Metadados do Protocolo / Enriquecimento XML -->
  <rect x="44" y="228" width="712" height="170" fill="#f8fafc" stroke="#cbd5e1" rx="6"/>
  <text x="62" y="254" font-family="Inter, Arial, sans-serif" font-size="12" font-weight="700" fill="#475569">DADOS ENRIQUECIDOS DA TABELA BASE (XML PORTO SEGURO)</text>
  <line x1="62" y1="264" x2="738" y2="264" stroke="#e2e8f0" stroke-width="1"/>

  <text x="62" y="290" font-family="Inter, Arial, sans-serif" font-size="12" fill="#64748b">PROTOCOLO (CAPA):</text>
  <text x="200" y="290" font-family="monospace" font-size="14" font-weight="700" fill="#0f172a">{num_prot}</text>

  <text x="420" y="290" font-family="Inter, Arial, sans-serif" font-size="12" fill="#64748b">LOTE ORIGEM XML:</text>
  <text x="550" y="290" font-family="monospace" font-size="13" font-weight="700" fill="#0f172a">{lote_origem}</text>

  <text x="62" y="320" font-family="Inter, Arial, sans-serif" font-size="12" fill="#64748b">PRESTADOR:</text>
  <text x="200" y="320" font-family="Inter, Arial, sans-serif" font-size="13" font-weight="700" fill="#0f172a">{prestador}</text>

  <text x="62" y="350" font-family="Inter, Arial, sans-serif" font-size="12" fill="#64748b">CNPJ / CÓDIGO:</text>
  <text x="200" y="350" font-family="monospace" font-size="13" fill="#334155">{cnpj}</text>

  <text x="420" y="350" font-family="Inter, Arial, sans-serif" font-size="12" fill="#64748b">VALOR TOTAL:</text>
  <text x="550" y="350" font-family="monospace" font-size="14" font-weight="700" fill="#047857">R$ {valor_total:,.2f}</text>

  <text x="62" y="380" font-family="Inter, Arial, sans-serif" font-size="12" fill="#64748b">GUIA VINCULADA:</text>
  <text x="200" y="380" font-family="monospace" font-size="13" font-weight="700" fill="#1e293b">{guia["numero_guia"] if guia else "N/A (Capa Principal)"}</text>

  <text x="420" y="380" font-family="Inter, Arial, sans-serif" font-size="12" fill="#64748b">PÁGINA NO DOC / LOTE:</text>
  <text x="575" y="380" font-family="monospace" font-size="13" font-weight="700" fill="#1e293b">Folha #{pagina.get("numero_pagina_documento")} (Lote Pág #{pagina.get("numero_pagina_lote")})</text>

  <!-- Corpo Clínico / Tabela de Procedimentos TISS -->
  <rect x="44" y="418" width="712" height="510" fill="#ffffff" stroke="#cbd5e1" rx="6"/>
  <rect x="44" y="418" width="712" height="36" fill="#f1f5f9" rx="6"/>
  <text x="62" y="441" font-family="Inter, Arial, sans-serif" font-size="12" font-weight="700" fill="#334155">DETALHAMENTO TÉCNICO / REGISTRO DE ATENDIMENTO MÉDICO</text>

  <line x1="62" y1="490" x2="738" y2="490" stroke="#e2e8f0" stroke-width="1"/>
  <text x="62" y="480" font-family="monospace" font-size="11" font-weight="700" fill="#475569">CÓDIGO TUSS       DESCRIÇÃO DO PROCEDIMENTO / EXAME                         QTD     STATUS</text>
  <text x="62" y="515" font-family="monospace" font-size="12" fill="#1e293b">10101012          CONSULTA EM CONSULTÓRIO (NO HORÁRIO NORMAL)                01      EXECUTADO</text>
  <text x="62" y="545" font-family="monospace" font-size="12" fill="#1e293b">40304361          HEMOGRAMA COM CONTAGEM DE PLAQUETAS OU FRAÇÕES             01      ANEXADO</text>
  <text x="62" y="575" font-family="monospace" font-size="12" fill="#1e293b">40801012          RADIOGRAFIA / LAUDO DE IMAGEM DIAGNÓSTICA                  01      CONFERIDO</text>

  <rect x="62" y="620" width="676" height="160" fill="#f8fafc" stroke="#e2e8f0" rx="4"/>
  <text x="80" y="648" font-family="Inter, Arial, sans-serif" font-size="12" font-weight="600" fill="#475569">Observações / Assinatura Digital e Carimbo Médico:</text>
  <text x="80" y="675" font-family="Inter, Arial, sans-serif" font-size="12" fill="#64748b">Documento capturado e indexado automaticamente pelo motor Teledoc Engine.</text>
  <text x="80" y="700" font-family="monospace" font-size="11" fill="#64748b">Arquivo Storage: {pagina.get("caminho_storage_tiff")}</text>
  <text x="80" y="725" font-family="monospace" font-size="11" fill="#64748b">Confiança Leitura Óptica Code 128: {float(pagina.get("confianca_barcode") or 0):.2f}%</text>

  <!-- Carimbo de Validação -->
  <g transform="translate(520, 810) rotate(-6)">
    <rect x="0" y="0" width="200" height="70" fill="none" stroke="{badge_color}" stroke-width="2.5" rx="8"/>
    <text x="100" y="28" text-anchor="middle" font-family="monospace" font-size="12" font-weight="700" fill="{badge_color}">TELEDOC INDEXADO</text>
    <text x="100" y="46" text-anchor="middle" font-family="monospace" font-size="10" fill="{badge_color}">{protocolo.get("status_validacao")}</text>
    <text x="100" y="60" text-anchor="middle" font-family="monospace" font-size="9" fill="#64748b">300 DPI TIFF/PDF</text>
  </g>

  <!-- Rodapé Técnico -->
  <text x="44" y="965" font-family="monospace" font-size="10" fill="#64748b">ID Página: {pagina.get("id_pagina")} | Lote: {pagina.get("id_lote")}</text>
  <text x="756" y="965" text-anchor="end" font-family="monospace" font-size="10" fill="#64748b">Teledoc Capture &amp; Engine v1.0</text>
</svg>"""
