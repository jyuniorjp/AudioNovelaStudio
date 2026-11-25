; Instalador de Audio Novela Studio (Inno Setup 6)
; Lo compila COMPILAR.bat después de crear AudioNovelaStudio.exe. La versión llega con /DAppVer=...

#ifndef AppVer
  #define AppVer "1.0.0"
#endif
#define AppName "Audio Novela Studio"
#define AppExe "AudioNovelaStudio.exe"

[Setup]
; El AppId no se cambia nunca: así una versión nueva reemplaza a la anterior en vez de instalarse al lado
AppId={{0340F46E-9A5E-45CA-955C-96D8AE416A17}
AppName={#AppName}
AppVersion={#AppVer}
AppVerName={#AppName} {#AppVer}
AppPublisher=Kuon Studio
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Sin permisos de administrador: se instala para el usuario actual. Quien quiera, elige "para todos" en el diálogo.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\instaladores
OutputBaseFilename=Instalar_AudioNovelaStudio_{#AppVer}
SetupIconFile=..\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
WizardStyle=modern
WizardImageFile=lateral.bmp,lateral_2x.bmp
WizardSmallImageFile=pequena.bmp,pequena_2x.bmp
Compression=lzma2/max
SolidCompression=yes
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "escritorio"; Description: "Crear un acceso directo en el escritorio"; GroupDescription: "Accesos directos:"

[Files]
Source: "..\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\icon.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LEEME.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\voz\*"; DestDir: "{app}\voz"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; IconFilename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; IconFilename: "{app}\{#AppExe}"; Tasks: escritorio

[Run]
Filename: "{app}\{#AppExe}"; Description: "Abrir {#AppName} ahora"; Flags: nowait postinstall skipifsilent

; Al desinstalar no se tocan los ajustes ni lo aprendido por Kuon IA (%APPDATA%\AudioNovelaStudio)
; ni los audios guardados (Música\AudioNovelaStudio).
