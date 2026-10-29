#ifndef AppVersion
#define AppVersion "0.2.13"
#endif
#ifndef BinaryDir
  #define BinaryDir "..\dist\Sentinella"
#endif
[Setup]
AppId={{71A99C86-646E-4D4D-8ADC-327D725596D8}
AppName=Sentinella
AppVersion={#AppVersion}
AppPublisher=Sentinella
DefaultDirName={localappdata}\Programs\Sentinella
DefaultGroupName=Sentinella
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.22000
OutputDir=..\dist
OutputBaseFilename=Sentinella-{#AppVersion}-Windows-x64-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
CloseApplications=yes
RestartApplications=no
SetupLogging=yes
SetupIconFile=..\assets\sentinella.ico
UninstallDisplayIcon={app}\Sentinella.exe

[Languages]
Name: "italian"; MessagesFile: "compiler:Languages\Italian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "{#BinaryDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\Sentinella"; Filename: "{app}\Sentinella.exe"; IconFilename: "{app}\Sentinella.exe"; AppUserModelID: "Sentinella.Desktop"; Check: not WizardNoIcons
Name: "{userdesktop}\Sentinella"; Filename: "{app}\Sentinella.exe"; IconFilename: "{app}\Sentinella.exe"; AppUserModelID: "Sentinella.Desktop"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Crea un collegamento sul desktop"; GroupDescription: "Collegamenti:"; Flags: unchecked

[Run]
Filename: "{app}\Sentinella.exe"; Description: "Avvia Sentinella"; Flags: nowait postinstall skipifsilent

; Writable preferences and receipts live outside {app}. Deliberately no
; UninstallDelete section: uninstall never deletes the user's private data.

[UninstallRun]
Filename: "{app}\Sentinella.exe"; Parameters: "--remove-startup"; Flags: runhidden skipifdoesntexist; RunOnceId: "RemoveOwnedStartup"
