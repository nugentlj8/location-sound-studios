' Launches the app with no console window, via pythonw.
' A .vbs run this way shows nothing itself and spawns no cmd window.
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

' folder this script lives in
here = fso.GetParentFolderName(WScript.ScriptFullName)
studio = fso.BuildPath(here, "lss_studio\lss_studio.py")

' prefer pythonw (no console); fall back to py -w
shell.CurrentDirectory = fso.BuildPath(here, "lss_studio")
shell.Run "pythonw """ & studio & """", 0, False
