' 双击此文件启动额度悬浮窗（无窗口闪烁）
' 优先启动 release 下的 EXE；没有 EXE 时用本机 Python 运行源码
' 若弹窗提示文件来源，请选择“打开”
Dim sh, fso, scriptDir, root, exe, py
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
root = fso.GetParentFolderName(scriptDir)
exe = root & "\release\额度悬浮窗.exe"
If fso.FileExists(exe) Then
    sh.Run """" & exe & """", 0, False
Else
    ' 优先使用官方安装的 Python（带 tkinter），找不到再退回 PATH 中的 pythonw
    py = "C:\Users\Z\AppData\Local\Programs\Python\Python310\pythonw.exe"
    If Not fso.FileExists(py) Then py = "pythonw.exe"
    sh.Run """" & py & """ """ & root & "\src\usage_widget.pyw""", 0, False
End If
