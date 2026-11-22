# Projeto Teledoc — Captura, Triagem Code 128, Enriquecimento XML e Consulta Documental

Substituição integral e unificada dos sistemas legados **E2scan** e **E2doc**, integrando digitalização contínua via scanner **Ricoh/Fujitsu fi-8170** (Perfil `004 : BPO`), separação documental por **Code 128**, cruzamento automático com a tabela **`BASE` (XMLs Porto Seguro)**, **Central de Auditoria de Exceções** e **Portal de Consulta com Visualizador em Árvore (TIFF/PDF)**.

---

## Estrutura do Projeto

- `database/schema.sql`: DDL relacional PostgreSQL completo (`teledoc_lotes`, `teledoc_protocolos`, `teledoc_guias`, `teledoc_paginas`, `base_xml_porto`).
- `database/seed_base_xml.sql`: Carga de dados iniciais da tabela `base_xml_porto`.
- `docker-compose.yml`: Orquestração do PostgreSQL 16 com inicialização automática do schema e seed.
- `backend/`: **Módulo 2 — Teledoc Engine & API**
  - `barcode_engine.py`: Codificador/Decodificador óptico **Code 128** (ISO/IEC 15417 Set B, Checksum Mod 103) com limiarização adaptativa e rotação automática em `0°`, `90°`, `180°` e `270°`, classificando `TIPO_18` (Capa), `TIPO_19` (Guia Médica) e `TIPO_20` (Folha Anexa).
  - `enrichment_service.py`: Pipeline de ingestão de páginas, montagem da árvore documental, cruzamento com `base_xml_porto` e conciliação de divergências (`STATUS_INDEXADO_COM_SUCESSO`, `STATUS_DIVERGENCIA_GUIAS`, `STATUS_PROTOCOLO_NAO_ENCONTRADO`).
  - `pdf_tiff_service.py`: Gerador de arquivos TIFF 300 DPI (`storage/tiff/`), PDF único consolidado do protocolo (`storage/pdf/`) e Previews Vetoriais SVG.
  - `server.py`: Servidor REST API implementando todos os contratos da Seção 6 e servindo o **Teledoc Web Hub**.
- `desktop/Teledoc.Capture/`: **Módulo 1 — Teledoc Capture (.NET 8 WPF + TWAIN)**
  - Aplicação Desktop para operação com o scanner **Ricoh/Fujitsu fi-8170** via driver **PaperStream IP (TWAIN)** no perfil `004 : BPO` e upload direto em memória (`multipart/form-data`).
- `frontend/index.html`: **Módulo 3 — Teledoc Web Hub (SPA)**
  - **Painel 1**: Portal de Consulta e Visualizador Documental em Árvore (`Protocolo -> Capa -> Guias -> Folhas`).
  - **Painel 2**: Central de Auditoria e Tratamento de Exceções (Comparativo Físico vs. XML da BASE, reindexação assistida, promoção de anexo para guia e exclusão de página).
  - **Painel 3**: Estação de Captura / Simulador de Cenários fi-8170 e Inspeção da Tabela `base_xml_porto`.
- `tests/test_integrated_batch.py`: **Passo 5 — Testes Integrados de Lote** (simula lote de 10 páginas com 1 Capa, 2 Guias e 7 Folhas Anexas + cenários de divergência).

---

## Como Executar

### 1. Subir o Teledoc Engine & Web Hub
```powershell
py -m backend.server
```
Acesse no navegador: **http://localhost:8000**

### 2. Executar a Suite de Testes Integrados (Passo 5)
```powershell
py tests/test_integrated_batch.py
```

### 3. Compilar o Desktop Capture (.NET 8 WPF)
```powershell
cd desktop/Teledoc.Capture
dotnet run
```
