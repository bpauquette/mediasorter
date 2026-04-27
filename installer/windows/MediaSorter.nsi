Unicode true

!include "MUI2.nsh"
!include "x64.nsh"

!define APP_NAME "MediaSorter"
!define APP_PUBLISHER "MediaSorter"
!define APP_VERSION "1.0.0"
!define APP_EXE "mediasorter.exe"
!define APP_INSTALL_DIR "$PROGRAMFILES64\${APP_NAME}"
!define APP_REG_KEY "Software\${APP_NAME}"
!define APP_UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}"
!define APP_ICON "..\..\assets\icons\mediasorter_icon.ico"
!define APP_WEBSITE "https://github.com/bpauquette/mediasorter"
!define APP_DIST_DIR "..\..\dist\windows\MediaSorter.dist"
!define HAND_BRAKE_URL "https://handbrake.fr/"
!define SEQUOIA_URL "https://www.sequoiaview.com/"
!define LEGAL_URL "https://github.com/bpauquette/mediasorter/blob/main/LEGAL_MARKETING_RECOMMENDATIONS.md"
!define PRIVACY_URL "https://github.com/bpauquette/mediasorter/blob/main/PRIVACY.md"
!define TERMS_URL "https://github.com/bpauquette/mediasorter/blob/main/TERMS.md"
!define REFUND_URL "https://github.com/bpauquette/mediasorter/blob/main/REFUND_POLICY.md"
!ifndef PAYMENT_URL
!define PAYMENT_URL "https://github.com/bpauquette/mediasorter/releases/latest"
!endif

Name "${APP_NAME}"
OutFile "..\..\dist\windows\MediaSorterSetup.exe"
InstallDir "${APP_INSTALL_DIR}"
InstallDirRegKey HKLM "${APP_REG_KEY}" "InstallDir"
RequestExecutionLevel admin
SetCompressor /SOLID lzma
ShowInstDetails show
ShowUninstDetails show

!define MUI_ABORTWARNING
!define MUI_ICON "${APP_ICON}"
!define MUI_UNICON "${APP_ICON}"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN "$INSTDIR\${APP_EXE}"
!define MUI_FINISHPAGE_RUN_TEXT "Launch MediaSorter"
!define MUI_FINISHPAGE_SHOWREADME "$INSTDIR\NOTICES-First-Run.txt"
!define MUI_FINISHPAGE_SHOWREADME_TEXT "View support, legal, and recommended tool links"
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "English"

Section "MediaSorter Application (required)" SEC_APP
  SectionIn RO

  SetOutPath "$INSTDIR"
  File /r "${APP_DIST_DIR}\*.*"

  ; First-run links note.
  FileOpen $0 "$INSTDIR\NOTICES-First-Run.txt" w
  FileWrite $0 "MediaSorter First-Run Links$\r$\n"
  FileWrite $0 "===========================$\r$\n$\r$\n"
  FileWrite $0 "Support / Buy MediaSorter: ${PAYMENT_URL}$\r$\n"
  FileWrite $0 "Privacy Policy: ${PRIVACY_URL}$\r$\n"
  FileWrite $0 "Terms of Use: ${TERMS_URL}$\r$\n"
  FileWrite $0 "Refund Policy: ${REFUND_URL}$\r$\n"
  FileWrite $0 "Legal + Marketing Guide: ${LEGAL_URL}$\r$\n$\r$\n"
  FileWrite $0 "The following software is not part of the MediaSorter project.$\r$\n"
  FileWrite $0 "They are optional external tools that work well alongside MediaSorter.$\r$\n$\r$\n"
  FileWrite $0 "HandBrake: ${HAND_BRAKE_URL}$\r$\n"
  FileWrite $0 "SequoiaView: ${SEQUOIA_URL}$\r$\n"
  FileClose $0

  ; App runtime support/payment link for in-app "Support / Buy" button.
  FileOpen $1 "$INSTDIR\support_url.txt" w
  FileWrite $1 "${PAYMENT_URL}$\r$\n"
  FileClose $1

!ifdef LICENSE_API_URL
  FileOpen $2 "$INSTDIR\license_api_url.txt" w
  FileWrite $2 "${LICENSE_API_URL}$\r$\n"
  FileClose $2
!endif

  ; URL shortcut files
  WriteINIStr "$INSTDIR\Support and Buy MediaSorter.url" "InternetShortcut" "URL" "${PAYMENT_URL}"
  WriteINIStr "$INSTDIR\Privacy Policy.url" "InternetShortcut" "URL" "${PRIVACY_URL}"
  WriteINIStr "$INSTDIR\Terms of Use.url" "InternetShortcut" "URL" "${TERMS_URL}"
  WriteINIStr "$INSTDIR\Refund Policy.url" "InternetShortcut" "URL" "${REFUND_URL}"
  WriteINIStr "$INSTDIR\Legal and Marketing Guide.url" "InternetShortcut" "URL" "${LEGAL_URL}"
  WriteINIStr "$INSTDIR\HandBrake (External Tool).url" "InternetShortcut" "URL" "${HAND_BRAKE_URL}"
  WriteINIStr "$INSTDIR\SequoiaView (External Tool).url" "InternetShortcut" "URL" "${SEQUOIA_URL}"

  ; Start Menu
  CreateDirectory "$SMPROGRAMS\${APP_NAME}"
  CreateShortcut "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}"
  CreateShortcut "$SMPROGRAMS\${APP_NAME}\Support and Buy MediaSorter.lnk" "$INSTDIR\Support and Buy MediaSorter.url"
  CreateShortcut "$SMPROGRAMS\${APP_NAME}\Privacy Policy.lnk" "$INSTDIR\Privacy Policy.url"
  CreateShortcut "$SMPROGRAMS\${APP_NAME}\Terms of Use.lnk" "$INSTDIR\Terms of Use.url"
  CreateShortcut "$SMPROGRAMS\${APP_NAME}\Refund Policy.lnk" "$INSTDIR\Refund Policy.url"
  CreateShortcut "$SMPROGRAMS\${APP_NAME}\Legal and Marketing Guide.lnk" "$INSTDIR\Legal and Marketing Guide.url"
  CreateShortcut "$SMPROGRAMS\${APP_NAME}\Uninstall ${APP_NAME}.lnk" "$INSTDIR\Uninstall.exe"
  CreateShortcut "$SMPROGRAMS\${APP_NAME}\HandBrake (Optional External Tool).lnk" "$INSTDIR\HandBrake (External Tool).url"
  CreateShortcut "$SMPROGRAMS\${APP_NAME}\SequoiaView (Optional External Tool).lnk" "$INSTDIR\SequoiaView (External Tool).url"

  ; Registry and uninstaller
  WriteRegStr HKLM "${APP_REG_KEY}" "InstallDir" "$INSTDIR"
  WriteRegStr HKLM "${APP_UNINSTALL_KEY}" "DisplayName" "${APP_NAME}"
  WriteRegStr HKLM "${APP_UNINSTALL_KEY}" "DisplayVersion" "${APP_VERSION}"
  WriteRegStr HKLM "${APP_UNINSTALL_KEY}" "Publisher" "${APP_PUBLISHER}"
  WriteRegStr HKLM "${APP_UNINSTALL_KEY}" "URLInfoAbout" "${APP_WEBSITE}"
  WriteRegStr HKLM "${APP_UNINSTALL_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKLM "${APP_UNINSTALL_KEY}" "DisplayIcon" "$INSTDIR\${APP_EXE}"
  WriteRegStr HKLM "${APP_UNINSTALL_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegDWORD HKLM "${APP_UNINSTALL_KEY}" "NoModify" 1
  WriteRegDWORD HKLM "${APP_UNINSTALL_KEY}" "NoRepair" 1

  WriteUninstaller "$INSTDIR\Uninstall.exe"
SectionEnd

Section "Desktop Shortcut" SEC_DESKTOP
  CreateShortcut "$DESKTOP\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}"
SectionEnd

Section "Uninstall"
  Delete "$DESKTOP\${APP_NAME}.lnk"

  Delete "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk"
  Delete "$SMPROGRAMS\${APP_NAME}\Support and Buy MediaSorter.lnk"
  Delete "$SMPROGRAMS\${APP_NAME}\Privacy Policy.lnk"
  Delete "$SMPROGRAMS\${APP_NAME}\Terms of Use.lnk"
  Delete "$SMPROGRAMS\${APP_NAME}\Refund Policy.lnk"
  Delete "$SMPROGRAMS\${APP_NAME}\Legal and Marketing Guide.lnk"
  Delete "$SMPROGRAMS\${APP_NAME}\Uninstall ${APP_NAME}.lnk"
  Delete "$SMPROGRAMS\${APP_NAME}\HandBrake (Optional External Tool).lnk"
  Delete "$SMPROGRAMS\${APP_NAME}\SequoiaView (Optional External Tool).lnk"
  RMDir "$SMPROGRAMS\${APP_NAME}"

  Delete "$INSTDIR\Support and Buy MediaSorter.url"
  Delete "$INSTDIR\Privacy Policy.url"
  Delete "$INSTDIR\Terms of Use.url"
  Delete "$INSTDIR\Refund Policy.url"
  Delete "$INSTDIR\Legal and Marketing Guide.url"
  Delete "$INSTDIR\support_url.txt"
  Delete "$INSTDIR\license_api_url.txt"
  Delete "$INSTDIR\HandBrake (External Tool).url"
  Delete "$INSTDIR\SequoiaView (External Tool).url"
  Delete "$INSTDIR\NOTICES-First-Run.txt"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir /r "$INSTDIR"

  DeleteRegKey HKLM "${APP_UNINSTALL_KEY}"
  DeleteRegKey HKLM "${APP_REG_KEY}"
SectionEnd
