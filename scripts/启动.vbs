' 双击此文件启动额度悬浮窗（无窗口闪烁）
' 查找顺序（全部相对本脚本所在仓库定位，不含任何绝对路径/用户名）：
'   1) 仓库构建产物：dist\onedir\llm-usage-widget\llm-usage-widget.exe
'      （PyInstaller --name llm-usage-widget，与 release.yml / setup.iss 一致）
'   2) release\llm-usage-widget.exe（手动放置副本，沿用旧版习惯、改用真实文件名）
'   3) 本机 Python 的 pythonw.exe（PATH 中查找）运行 v3 源码入口 src\qt\app.py
' 找不到 EXE 且找不到 Python 时弹窗提示，不再静默失败，
' 也不再回退到停更的 v2 入口（src\usage_widget.pyw）。
' 若弹窗提示文件来源，请选择"打开"
Option Explicit
Dim sh, fso, scriptDir, root, exe, py, exec, line
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
root = fso.GetParentFolderName(scriptDir)

' ---- 1/2: 优先启动 EXE ----
exe = root & "\dist\onedir\llm-usage-widget\llm-usage-widget.exe"
If Not fso.FileExists(exe) Then exe = root & "\release\llm-usage-widget.exe"
If fso.FileExists(exe) Then
    sh.Run "\""\" & exe & "\""\"", 0, False
    WScript.Quit
End If

' ---- 3: 回退本机 Python（PATH 中的 pythonw.exe）----
py = ""
Set exec = sh.Exec("cmd.exe /q /c where pythonw.exe")
Do While exec.Status = 0
    WScript.Sleep 50
Loop
If exec.ExitCode = 0 Then
    Do While Not exec.StdOut.AtEndOfStream
        line = Trim(exec.StdOut.ReadLine())
        If line <> "" And fso.FileExists(line) Then
            py = line
            Exit Do
        End If
    Loop
End If

If py = "" Then
    MsgBox "未找到 Python（pythonw.exe），无法以源码方式启动。" & vbCrLf & vbCrLf & _
           "请任选其一：" & vbCrLf & _
           "  1. 安装 Python 3.10+ 并勾选 ""Add Python to PATH""；" & vbCrLf & _
           "  2. 用 PyInstaller 打包出 EXE 后放到 dist\onedir\llm-usage-widget\ 或 release\ 下。", _
           vbExclamation, "额度悬浮窗"
    WScript.Quit 1
End If

sh.Run "\""\" & py & "\"" \""" & root & "\src\qt\app.py\""", 0, False
