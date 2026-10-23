' Oriole launcher without any console window: runs Oriole.bat hidden.
' The console is only shown the first time (no "oriole" conda env yet), as creating it takes minutes.
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
here = fso.GetParentFolderName(WScript.ScriptFullName)
home = sh.ExpandEnvironmentStrings("%USERPROFILE%")
data = sh.ExpandEnvironmentStrings("%ProgramData%")

style = 1
For Each root In Array(home & "\anaconda3", home & "\miniconda3", data & "\anaconda3", data & "\miniconda3")
    If fso.FolderExists(root & "\envs\oriole") Then style = 0
Next

sh.Run """" & here & "\Oriole.bat""", style, False
