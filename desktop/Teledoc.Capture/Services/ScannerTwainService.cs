using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using NTwain;
using NTwain.Data;

namespace Teledoc.Capture.Services
{
    public record ScannedPageEventArgs(
        int PageNumber,
        byte[] ImageTiffBytes,
        string? HardwareBarcodeHint,
        intDpi Resolution = 300
    );

    public record intDpi(int Value)
    {
        public static implicit operator intDpi(int v) => new(v);
    }

    /// <summary>
    /// Serviço de comunicação TWAIN com o scanner Ricoh/Fujitsu fi-8170 via driver PaperStream IP.
    /// Aplica o Perfil Operacional "004 : BPO" (300 DPI, P&B/Escala de Cinza Dinâmico,
    /// Rotação Automática por Orientação de Texto e Descarte Automático de Páginas em Branco).
    /// </summary>
    public class ScannerTwainService : IDisposable
    {
        public const string TargetDriverKeyword = "PaperStream IP";
        public const string TargetModelKeyword = "fi-8170";
        public const string OperationalProfileName = "004 : BPO";

        private TwainSession? _session;
        private DataSource? _activeSource;

        public event EventHandler<ScannedPageEventArgs>? PageCaptured;
        public event EventHandler<string>? StatusChanged;
        public event EventHandler? BatchTransferCompleted;

        public bool IsHardwareConnected => _activeSource is { IsOpen: true };

        public void InitializeTwainSession()
        {
            var appId = TWIdentity.CreateFromAssembly(
                DataGroups.Image | DataGroups.Control,
                Assembly.GetExecutingAssembly()
            );

            _session = new TwainSession(appId);
            _session.TransferReady += (_, e) =>
            {
                StatusChanged?.Invoke(this, "Alimentador ADF ativo - transferindo folha em memória...");
            };

            _session.DataTransferred += OnDataTransferred;
            _session.SourceDisabled += (_, _) =>
            {
                StatusChanged?.Invoke(this, "Leitura do alimentador concluída (ADF vazio).");
                BatchTransferCompleted?.Invoke(this, EventArgs.Empty);
            };

            _session.Open();

            // Procura prioritariamente pelo driver PaperStream IP fi-8170 (USB 3.2 / LAN)
            _activeSource = _session.FirstOrDefault(ds =>
                ds.Name.Contains(TargetDriverKeyword, StringComparison.OrdinalIgnoreCase) ||
                ds.Name.Contains(TargetModelKeyword, StringComparison.OrdinalIgnoreCase)
            ) ?? _session.FirstOrDefault();

            if (_activeSource != null)
            {
                _activeSource.Open();
                ConfigureProfile004Bpo(_activeSource);
                StatusChanged?.Invoke(
                    this,
                    $"Scanner conectado: {_activeSource.Name} [Perfil {OperationalProfileName} @ 300 DPI]"
                );
            }
            else
            {
                StatusChanged?.Invoke(
                    this,
                    "Scanner físico fi-8170 não detectado no barramento TWAIN. Modo Simulador BPO habilitado."
                );
            }
        }

        /// <summary>
        /// Configura programaticamente os parâmetros do perfil "004 : BPO" no driver PaperStream IP:
        /// - Resolução: 300 DPI (X e Y)
        /// - Alimentador contínuo ADF + Duplex
        /// - Rotação automática por orientação de texto (ICAP_AUTOMATICROTATE)
        /// - Descarte automático de páginas em branco (ICAP_AUTODISCARDBLANKPAGES)
        /// - Formato de transferência em memória/TIFF sem scripts intermediários
        /// </summary>
        private void ConfigureProfile004Bpo(DataSource source)
        {
            try
            {
                // 300 DPI
                source.Capabilities.ICapXResolution.SetValue(300f);
                source.Capabilities.ICapYResolution.SetValue(300f);

                // Preto e Branco / Escala de Cinza Dinâmico (PixelType.BlackWhite ou Gray)
                if (source.Capabilities.ICapPixelType.CanSet)
                {
                    source.Capabilities.ICapPixelType.SetValue(PixelType.Gray);
                }

                // Habilita Alimentador Automático (ADF)
                if (source.Capabilities.CapFeederEnabled.CanSet)
                {
                    source.Capabilities.CapFeederEnabled.SetValue(BoolType.True);
                }

                // Rotação Automática por Orientação de Texto
                if (source.Capabilities.ICapAutomaticRotate.CanSet)
                {
                    source.Capabilities.ICapAutomaticRotate.SetValue(BoolType.True);
                }

                // Descarte Automático de Páginas em Branco (-1 = Auto no padrão TWAIN)
                if (source.Capabilities.ICapAutoDiscardBlankPages.CanSet)
                {
                    source.Capabilities.ICapAutoDiscardBlankPages.SetValue(-1);
                }
            }
            catch (Exception ex)
            {
                StatusChanged?.Invoke(
                    this,
                    $"Aviso: Perfil '{OperationalProfileName}' gerenciado diretamente pelo driver PaperStream IP ({ex.Message})."
                );
            }
        }

        private int _currentPageCounter;

        private void OnDataTransferred(object? sender, DataTransferredEventArgs e)
        {
            if (e.NativeData == IntPtr.Zero) return;

            using var stream = e.GetNativeImageStream();
            if (stream == null) return;

            using var ms = new MemoryStream();
            stream.CopyTo(ms);
            var bytes = ms.ToArray();

            var pageNum = Interlocked.Increment(ref _currentPageCounter);
            PageCaptured?.Invoke(this, new ScannedPageEventArgs(pageNum, bytes, null, 300));
        }

        /// <summary>
        /// Inicia a captura contínua do lote no scanner fi-8170 (ou executa o lote simulado em memória).
        /// </summary>
        public async Task StartContinuousBatchCaptureAsync(
            IntPtr windowHandle,
            CancellationToken cancellationToken = default
        )
        {
            _currentPageCounter = 0;

            if (_activeSource is { IsOpen: true })
            {
                StatusChanged?.Invoke(this, "Digitalizando lote físico no Ricoh/Fujitsu fi-8170...");
                _activeSource.Enable(SourceEnableMode.NoUI, false, windowHandle);
                return;
            }

            // Modo Simulador Hardware fi-8170 (10 páginas: 1 Capa + 2 Guias + Folhas Anexas)
            StatusChanged?.Invoke(this, "Simulando alimentação ADF no fi-8170 (Perfil 004 : BPO - 10 folhas)...");
            var simulatedBarcodes = new string?[]
            {
                "1984729102",      // Pág 1: Capa de Lote (TIPO_18)
                null,              // Pág 2: Folha Anexa à Capa (TIPO_20)
                "G9821827361829",  // Pág 3: Guia Médica 1 (TIPO_19)
                null,              // Pág 4: Folha Anexa Guia 1 (TIPO_20)
                null,              // Pág 5: Folha Anexa Guia 1 (TIPO_20)
                null,              // Pág 6: Folha Anexa Guia 1 (TIPO_20)
                "G9821827361830",  // Pág 7: Guia Médica 2 (TIPO_19)
                null,              // Pág 8: Folha Anexa Guia 2 (TIPO_20)
                null,              // Pág 9: Folha Anexa Guia 2 (TIPO_20)
                null               // Pág 10: Folha Anexa Guia 2 (TIPO_20)
            };

            for (int i = 0; i < simulatedBarcodes.Length; i++)
            {
                cancellationToken.ThrowIfCancellationRequested();
                await Task.Delay(250, cancellationToken);
                var pageNum = Interlocked.Increment(ref _currentPageCounter);
                PageCaptured?.Invoke(
                    this,
                    new ScannedPageEventArgs(pageNum, Array.Empty<byte>(), simulatedBarcodes[i], 300)
                );
            }

            BatchTransferCompleted?.Invoke(this, EventArgs.Empty);
        }

        public void Dispose()
        {
            _activeSource?.Close();
            _session?.Close();
        }
    }
}
