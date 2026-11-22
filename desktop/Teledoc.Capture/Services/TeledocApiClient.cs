using System;
using System.IO;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace Teledoc.Capture.Services
{
    public record BatchSessionDto(
        string id_lote,
        string operador_id,
        string estacao_trabalho,
        string status_processamento
    );

    public record PageUploadResponseDto(
        string mensagem,
        UploadedPageDto pagina
    );

    public record UploadedPageDto(
        string id_pagina,
        string id_lote,
        string? id_protocolo,
        string? id_guia,
        string tipo_documento,
        int numero_pagina_lote,
        int numero_pagina_documento,
        string? codigo_barras_bruto,
        double confianca_barcode,
        string descricao_tipo
    );

    /// <summary>
    /// Cliente HTTP assíncrono para comunicação direta entre o Teledoc Capture (WPF)
    /// e o Teledoc Engine & API (sem pastas compartilhadas ou scripts manuais).
    /// </summary>
    public class TeledocApiClient : IDisposable
    {
        private readonly HttpClient _http;
        private readonly JsonSerializerOptions _jsonOptions = new()
        {
            PropertyNameCaseInsensitive = true
        };

        public string BaseUrl { get; }

        public TeledocApiClient(string baseUrl = "http://localhost:8000")
        {
            BaseUrl = baseUrl.TrimEnd('/');
            _http = new HttpClient
            {
                BaseAddress = new Uri(BaseUrl),
                Timeout = TimeSpan.FromSeconds(30)
            };
        }

        public async Task<bool> CheckApiHealthAsync(CancellationToken ct = default)
        {
            try
            {
                using var resp = await _http.GetAsync("/api/v1/health", ct);
                return resp.IsSuccessStatusCode;
            }
            catch
            {
                return false;
            }
        }

        public async Task<BatchSessionDto> CreateBatchAsync(
            string operadorId,
            string estacaoTrabalho,
            CancellationToken ct = default
        )
        {
            var body = JsonSerializer.Serialize(new
            {
                operador_id = operadorId,
                estacao_trabalho = estacaoTrabalho
            });

            using var content = new StringContent(body, Encoding.UTF8, "application/json");
            using var resp = await _http.PostAsync("/api/v1/lotes", content, ct);
            resp.EnsureSuccessStatusCode();

            var json = await resp.Content.ReadAsStringAsync(ct);
            return JsonSerializer.Deserialize<BatchSessionDto>(json, _jsonOptions)!;
        }

        /// <summary>
        /// Envia a página escaneada em memória via multipart/form-data para `/api/v1/lotes/{id_lote}/paginas`.
        /// </summary>
        public async Task<PageUploadResponseDto> UploadScannedPageAsync(
            string idLote,
            int pageNumber,
            byte[] tiffImageBytes,
            string? barcodeHint = null,
            CancellationToken ct = default
        )
        {
            using var form = new MultipartFormDataContent();

            if (tiffImageBytes is { Length: > 0 })
            {
                var fileContent = new ByteArrayContent(tiffImageBytes);
                fileContent.Headers.ContentType = MediaTypeHeaderValue.Parse("image/tiff");
                form.Add(fileContent, "file", $"scan_p{pageNumber:D4}.tif");
            }

            if (!string.IsNullOrWhiteSpace(barcodeHint))
            {
                form.Add(new StringContent(barcodeHint, Encoding.UTF8), "barcode_hint");
            }

            using var resp = await _http.PostAsync($"/api/v1/lotes/{idLote}/paginas", form, ct);
            resp.EnsureSuccessStatusCode();

            var json = await resp.Content.ReadAsStringAsync(ct);
            return JsonSerializer.Deserialize<PageUploadResponseDto>(json, _jsonOptions)!;
        }

        public async Task<string> FinalizeBatchAsync(string idLote, CancellationToken ct = default)
        {
            using var content = new StringContent("{}", Encoding.UTF8, "application/json");
            using var resp = await _http.PostAsync($"/api/v1/lotes/{idLote}/concluir", content, ct);
            resp.EnsureSuccessStatusCode();
            return await resp.Content.ReadAsStringAsync(ct);
        }

        public void Dispose()
        {
            _http.Dispose();
        }
    }
}
