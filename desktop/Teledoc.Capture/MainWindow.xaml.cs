using System;
using System.Collections.Concurrent;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Interop;
using Teledoc.Capture.Services;

namespace Teledoc.Capture
{
    public partial class MainWindow : Window
    {
        private readonly ScannerTwainService _scanner = new();
        private readonly TeledocApiClient _api = new("http://localhost:8000");
        private readonly ConcurrentQueue<ScannedPageEventArgs> _uploadQueue = new();

        private string? _activeBatchId;
        private int _pendingUploads;

        public MainWindow()
        {
            InitializeComponent();
            Loaded += OnWindowLoaded;
            Closed += (_, _) =>
            {
                _scanner.Dispose();
                _api.Dispose();
            };
        }

        private async void OnWindowLoaded(object sender, RoutedEventArgs e)
        {
            _scanner.StatusChanged += (_, msg) =>
                Dispatcher.Invoke(() => TxtFooterStatus.Text = msg);

            _scanner.PageCaptured += OnScannerPageCaptured;
            _scanner.BatchTransferCompleted += OnBatchTransferCompleted;

            _scanner.InitializeTwainSession();

            var online = await _api.CheckApiHealthAsync();
            TxtApiStatus.Text = online
                ? "API ONLINE (localhost:8000)"
                : "API OFFLINE (Verifique Teledoc Engine)";
        }

        private async void BtnStartScan_Click(object sender, RoutedEventArgs e)
        {
            try
            {
                BtnStartScan.IsEnabled = false;
                LstScannedPages.Items.Clear();

                var batch = await _api.CreateBatchAsync(
                    operadorId: Environment.UserName,
                    estacaoTrabalho: Environment.MachineName
                );

                _activeBatchId = batch.id_lote;
                TxtBatchId.Text = _activeBatchId[..8] + "...";
                TxtPageCount.Text = "0 folhas";

                var hwnd = new WindowInteropHelper(this).Handle;
                await _scanner.StartContinuousBatchCaptureAsync(hwnd);
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    $"Erro ao iniciar captura do lote: {ex.Message}",
                    "Teledoc Capture",
                    MessageBoxButton.OK,
                    MessageBoxImage.Error
                );
                BtnStartScan.IsEnabled = true;
            }
        }

        private async void OnScannerPageCaptured(object? sender, ScannedPageEventArgs e)
        {
            if (string.IsNullOrEmpty(_activeBatchId)) return;

            Interlocked.Increment(ref _pendingUploads);
            Dispatcher.Invoke(() =>
            {
                TxtPageCount.Text = $"{e.PageNumber} folhas";
                TxtQueueStatus.Text = $"{_pendingUploads} em envio";
            });

            try
            {
                var res = await _api.UploadScannedPageAsync(
                    _activeBatchId,
                    e.PageNumber,
                    e.ImageTiffBytes,
                    e.HardwareBarcodeHint
                );

                Interlocked.Decrement(ref _pendingUploads);

                Dispatcher.Invoke(() =>
                {
                    TxtQueueStatus.Text = $"{_pendingUploads} pendentes";
                    TxtLastBarcode.Text = res.pagina.codigo_barras_bruto ?? "SEM CÓDIGO (TIPO_20)";
                    LstScannedPages.Items.Add(
                        $"[Pág #{res.pagina.numero_pagina_lote:D2}] {res.pagina.tipo_documento} ({res.pagina.descricao_tipo}) " +
                        $"| Barcode: {res.pagina.codigo_barras_bruto ?? "N/A"} | Confiança: {res.pagina.confianca_barcode:F1}%"
                    );
                });
            }
            catch (Exception ex)
            {
                Interlocked.Decrement(ref _pendingUploads);
                Dispatcher.Invoke(() =>
                    LstScannedPages.Items.Add($"[ERRO Pág #{e.PageNumber:D2}] Falha no envio para API: {ex.Message}")
                );
            }
        }

        private async void OnBatchTransferCompleted(object? sender, EventArgs e)
        {
            if (string.IsNullOrEmpty(_activeBatchId)) return;

            // Aguarda esvaziamento da fila de upload antes de fechar o lote na API
            while (_pendingUploads > 0)
            {
                await Task.Delay(150);
            }

            await _api.FinalizeBatchAsync(_activeBatchId);

            Dispatcher.Invoke(() =>
            {
                TxtFooterStatus.Text = $"Lote {_activeBatchId[..8]} concluído, enriquecido com a BASE XML e indexado em TIFF/PDF!";
                BtnStartScan.IsEnabled = true;
            });
        }
    }
}
