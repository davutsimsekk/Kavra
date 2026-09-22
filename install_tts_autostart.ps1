# Kavra TTS sunucusunu (bu bilgisayarın GPU'sunu VPS'e uzak GPU olarak sunan servis) Windows
# oturum açılışında, görünmez şekilde başlatan bir Görev Zamanlayıcı görevi kurar/kaldırır.
#
# Yönetici gerekmez: görev yalnızca kendi oturumunda ("Yalnızca kullanıcı oturum açtığında"),
# geçerli kullanıcı için kaydedilir.
#
# Kullanım:
#   powershell -ExecutionPolicy Bypass -File install_tts_autostart.ps1            (kur)
#   powershell -ExecutionPolicy Bypass -File install_tts_autostart.ps1 -Uninstall (kaldır)
#   powershell -ExecutionPolicy Bypass -File install_tts_autostart.ps1 -Status    (durum)
param(
    [switch]$Uninstall,
    [switch]$Status
)

$TaskName = "KavraTTSServer"
$Root = $PSScriptRoot
$VbsPath = Join-Path $Root "run_tts_server_hidden.vbs"
$LogPath = Join-Path $Root "_cache\tts_server.log"
$TokenPath = Join-Path $Root "_cache\tts_server_token.txt"

function Show-Status {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) {
        Write-Host "Kurulu değil. Kurmak için: powershell -ExecutionPolicy Bypass -File install_tts_autostart.ps1"
        return
    }
    $info = Get-ScheduledTaskInfo -TaskName $TaskName
    Write-Host "Görev: $TaskName  |  Durum: $($task.State)  |  Son çalışma: $($info.LastRunTime)  |  Son sonuç: $($info.LastTaskResult)"
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:8790/health" -Headers @{Authorization = "Bearer x"} -TimeoutSec 3 -ErrorAction Stop
    } catch {
        $code = $_.Exception.Response.StatusCode.value__
        if ($code -eq 401) { Write-Host "Sunucu şu an CEVAP VERİYOR (401 beklenen — token yanlış verildi, bu normal)." }
        else { Write-Host "Sunucuya henüz ulaşılamıyor (birkaç saniye içinde açılıyor olabilir): $_" }
    }
    if (Test-Path $TokenPath) { Write-Host "Token: $(Get-Content $TokenPath -Raw)" }
    if (Test-Path $LogPath) { Write-Host "Son günlük satırları:"; Get-Content $LogPath -Tail 5 }
}

if ($Status) { Show-Status; exit }

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Görev kaldırıldı: $TaskName"
    Write-Host "Not: çalışmakta olan sunucu süreci varsa kapanmaz; kapatmak için Görev Yöneticisi'nden 'python.exe' (tts_server) sürecini sonlandır."
    exit
}

if (-not (Test-Path $VbsPath)) { throw "Bulunamadı: $VbsPath" }

$action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "`"$VbsPath`"" -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero)

try {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Force -ErrorAction Stop `
        -Description "Kavra TTS sunucusu (XTTS v2 + Piper) - bu bilgisayarin GPU'sunu Tailscale uzerinden VPS'e sunar. Kaldirmak icin: install_tts_autostart.ps1 -Uninstall" `
        | Out-Null
} catch {
    Write-Host "KURULAMADI: $($_.Exception.Message)"
    Write-Host "Bu genellikle görevin kısıtlı/uzaktan bir oturumdan (ör. otomasyon aracı, uzak masaüstü) kaydedilmeye çalışılmasından kaynaklanır."
    Write-Host "Bu betiği kendi normal masaüstü oturumunda (Başlat menüsünden açtığın bir PowerShell penceresinde) çalıştırmayı dene."
    exit 1
}

Write-Host "Kuruldu: '$TaskName' oturum açılışında sessizce başlayacak."
Write-Host "Hemen şimdi başlatmak için (oturum açılışını beklemeden):"
Write-Host "  Start-ScheduledTask -TaskName $TaskName"
Write-Host "Durum/token için: powershell -ExecutionPolicy Bypass -File install_tts_autostart.ps1 -Status"
Write-Host "Kaldırmak için:   powershell -ExecutionPolicy Bypass -File install_tts_autostart.ps1 -Uninstall"
