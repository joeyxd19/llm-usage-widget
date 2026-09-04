' 双击此文件启动额度悬浮窗（无窗口闪烁）
' 若弹窗提示文件来源，请选择“打开”
Dim sh, fso, scriptDir
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
sh.Run "pythonw.exe """ & scriptDir & "\usage_widget.pyw""", 0, False
