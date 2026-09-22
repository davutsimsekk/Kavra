' Windows başlangıcında (bkz. install_tts_autostart.ps1) run_tts_server.bat'ı görünmez pencerede,
' çıktısını _cache\tts_server.log dosyasına yazarak başlatır. Elle çift tıklayınca çalışan
' run_tts_server.bat'ın kendisi (konsollu hâli) bundan etkilenmez.
root = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))
logDir = root & "_cache"
Set fso = CreateObject("Scripting.FileSystemObject")
If Not fso.FolderExists(logDir) Then fso.CreateFolder(logDir)
cmd = "cmd.exe /c """"" & root & "run_tts_server.bat"" >> """ & logDir & "\tts_server.log"" 2>&1"""
CreateObject("WScript.Shell").Run cmd, 0, False
