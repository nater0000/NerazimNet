[Setup]
#define MyAppName "NerazimNet"
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0" ; Placeholder — create_installer.py passes /DMyAppVersion from pyproject.toml
#endif
#define MyPublisher "NerazimNet"
#define MyAppURL "https://github.com/nater0000/nerazimnet"
#define MyInstallDir "NerazimNet"
#define MyOutputDir "Output"
#define RootDir "{src}\..\.."

AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyInstallDir}
DefaultGroupName={#MyAppName}
UninstallDisplayIcon={app}\NerazimNet.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
OutputDir={#RootDir}\{#MyOutputDir}
OutputBaseFilename={#MyAppName}_Installer_{#MyAppVersion}
SetupIconFile={#RootDir}\resources\images\nerazimnet.ico
ChangesAssociations=yes
PrivilegesRequired=lowest

[Files]
; Main executable built by PyInstaller
Source: "{#RootDir}\dist\NerazimNet.exe"; DestDir: "{app}"; Flags: ignoreversion

; Bundled data files and folders 
; Syncthing is now contained in the python app

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\NerazimNet.exe"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\NerazimNet.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop icon"; GroupDescription: "Additional icons:";

[Run]
; Launch the application after installation
Filename: "{app}\NerazimNet.exe"; Description: "Launch NerazimNet"; Flags: nowait postinstall shellexec