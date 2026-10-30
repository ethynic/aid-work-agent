; Installation uses CurrentUser callbacks even when the file-copy section elevates.
!include "FileFunc.nsh"
!ifdef BUILD_UNINSTALLER
  !define RUNTIME_GUARD_FUNCTION "un.runtimeCheckAppRunning"
!else
  !define RUNTIME_GUARD_FUNCTION "runtimeCheckAppRunning"
  ; Initialize these before builder initMultiUser reads any install key. The
  ; generated ordinary uninstaller keeps the builder's original static keys.
  Var runtimeInstallRegistryKey
  Var runtimeUninstallRegistryKey
  !define INSTALL_REGISTRY_KEY "$runtimeInstallRegistryKey"
  !define UNINSTALL_REGISTRY_KEY "$runtimeUninstallRegistryKey"
  !ifdef UNINSTALL_REGISTRY_KEY_2
    Var runtimeLegacyUninstallRegistryKey
    !define RUNTIME_ORIGINAL_UNINSTALL_REGISTRY_KEY_2 "${UNINSTALL_REGISTRY_KEY_2}"
    !undef UNINSTALL_REGISTRY_KEY_2
    !define UNINSTALL_REGISTRY_KEY_2 "$runtimeLegacyUninstallRegistryKey"
  !endif
!endif

; Explicit acceptance-only isolation for final installer tests. Validate before
; placing any environment text into a PowerShell command line.
!macro runtimeReadFixture
  ReadEnvStr $R6 "AIDWORK_RUNTIME_INSTALL_FIXTURE"
  ; Normal customer machines have no fixture environment. ReadEnvStr marks a
  ; missing optional variable as an error; never propagate it as a guard/UAC
  ; failure after a successful product process check.
  ClearErrors
  ${If} $R6 != ""
    ${If} "${APP_ID}" != "cn.aidingyi.agent.runtime.acceptance"
      StrCpy $R0 2
      StrCpy $R1 "FIXTURE_NOT_ALLOWED_IN_PRODUCTION"
      Return
    ${EndIf}
    StrLen $R4 $R6
    ${If} $R4 != 36
      StrCpy $R0 2
      StrCpy $R1 "INVALID_FIXTURE_NAMESPACE"
      Return
    ${EndIf}
    StrCpy $R2 0
    ${Do}
      StrCpy $R3 $R6 1 $R2
      ${If} $R3 != "0"
      ${AndIf} $R3 != "1"
      ${AndIf} $R3 != "2"
      ${AndIf} $R3 != "3"
      ${AndIf} $R3 != "4"
      ${AndIf} $R3 != "5"
      ${AndIf} $R3 != "6"
      ${AndIf} $R3 != "7"
      ${AndIf} $R3 != "8"
      ${AndIf} $R3 != "9"
      ${AndIf} $R3 != "a"
      ${AndIf} $R3 != "b"
      ${AndIf} $R3 != "c"
      ${AndIf} $R3 != "d"
      ${AndIf} $R3 != "e"
      ${AndIf} $R3 != "f"
      ${AndIf} $R3 != "-"
        StrCpy $R0 2
        StrCpy $R1 "INVALID_FIXTURE_NAMESPACE"
        Return
      ${EndIf}
      IntOp $R2 $R2 + 1
    ${LoopUntil} $R2 == 36
  ${EndIf}
!macroend

; Only sanitized machine codes reach UI; launcher/parser output is discarded.
!macro runtimeSanitizeFailureCode
  ${Do}
    StrLen $R4 $R1
    ${If} $R4 == 0
      ${ExitDo}
    ${EndIf}
    IntOp $R2 $R4 - 1
    StrCpy $R3 $R1 1 $R2
    ${If} $R3 == "$\r"
    ${OrIf} $R3 == "$\n"
      StrCpy $R1 $R1 $R2
    ${Else}
      ${ExitDo}
    ${EndIf}
  ${Loop}
  StrLen $R4 $R1
  ${If} $R4 < 1
  ${OrIf} $R4 > 80
    StrCpy $R1 "INSTALLATION_COMMAND_FAILED"
  ${Else}
    StrCpy $R2 0
    ${Do}
      StrCpy $R3 $R1 1 $R2
      ${If} $R3 != "A"
      ${AndIf} $R3 != "B"
      ${AndIf} $R3 != "C"
      ${AndIf} $R3 != "D"
      ${AndIf} $R3 != "E"
      ${AndIf} $R3 != "F"
      ${AndIf} $R3 != "G"
      ${AndIf} $R3 != "H"
      ${AndIf} $R3 != "I"
      ${AndIf} $R3 != "J"
      ${AndIf} $R3 != "K"
      ${AndIf} $R3 != "L"
      ${AndIf} $R3 != "M"
      ${AndIf} $R3 != "N"
      ${AndIf} $R3 != "O"
      ${AndIf} $R3 != "P"
      ${AndIf} $R3 != "Q"
      ${AndIf} $R3 != "R"
      ${AndIf} $R3 != "S"
      ${AndIf} $R3 != "T"
      ${AndIf} $R3 != "U"
      ${AndIf} $R3 != "V"
      ${AndIf} $R3 != "W"
      ${AndIf} $R3 != "X"
      ${AndIf} $R3 != "Y"
      ${AndIf} $R3 != "Z"
      ${AndIf} $R3 != "_"
        StrCpy $R1 "INSTALLATION_COMMAND_FAILED"
        ${ExitDo}
      ${EndIf}
      IntOp $R2 $R2 + 1
    ${LoopUntil} $R2 == $R4
  ${EndIf}
!macroend

!macro runtimeCheckBeforeInstall
  StrCpy $R7 "Stop"
  StrCpy $R9 2
  ClearErrors
  ${If} ${UAC_IsInnerInstance}
    !insertmacro UAC_AsUser_Call Function ${RUNTIME_GUARD_FUNCTION} ${UAC_SYNCREGISTERS}|${UAC_SYNCINSTDIR}
  ${Else}
    Call ${RUNTIME_GUARD_FUNCTION}
  ${EndIf}
  ${If} ${Errors}
    StrCpy $R9 2
  ${EndIf}
  ${If} $R9 != 0
    SetErrorLevel $R9
    Quit
  ${EndIf}
!macroend
!macro customCheckAppRunning
  !insertmacro runtimeCheckBeforeInstall
  !ifndef BUILD_UNINSTALLER
    ${If} $R6 != ""
      ; Builder already computed link variables before CHECK_APP_RUNNING.
      ; Redirect all four locations before its create/rename/cleanup macros.
      CreateDirectory "$INSTDIR\fixture-shortcuts"
      StrCpy $oldMenuDirectory ""
      StrCpy $oldStartMenuLink "$INSTDIR\fixture-shortcuts\old-menu.lnk"
      StrCpy $newStartMenuLink "$INSTDIR\fixture-shortcuts\menu.lnk"
      StrCpy $oldDesktopLink "$INSTDIR\fixture-shortcuts\old-desktop.lnk"
      StrCpy $newDesktopLink "$INSTDIR\fixture-shortcuts\desktop.lnk"
    ${EndIf}
  !endif
!macroend
!ifndef BUILD_UNINSTALLER
  !macro preInit
    Call runtimeConfigureInstallerFixture
    ${If} $R0 != 0
      SetErrorLevel 2
      Quit
    ${EndIf}
  !macroend
!endif
!macro customInit
  ${If} ${UAC_IsInnerInstance}
  ${AndIf} ${Silent}
    !insertmacro setInstallModePerAllUsers
    !insertmacro runtimeCheckBeforeInstall
  ${EndIf}
!macroend
!macro customPageAfterChangeDir
  !ifdef MUI_PAGE_CUSTOMFUNCTION_PRE
    !undef MUI_PAGE_CUSTOMFUNCTION_PRE
  !endif
  !define MUI_PAGE_CUSTOMFUNCTION_PRE runtimeInstFilesPre
  Function runtimeInstFilesPre
    !ifdef allowToChangeInstallationDirectory
      Call instFilesPre
    !endif
    ${If} ${UAC_IsInnerInstance}
      !insertmacro runtimeCheckBeforeInstall
    ${EndIf}
  FunctionEnd
!macroend
!macro customHeader
  !ifndef BUILD_UNINSTALLER
    Function runtimeConfigureInstallerFixture
      StrCpy $R0 0
      StrCpy $runtimeInstallRegistryKey "Software\${APP_GUID}"
      StrCpy $runtimeUninstallRegistryKey "Software\Microsoft\Windows\CurrentVersion\Uninstall\${UNINSTALL_APP_KEY}"
      !ifdef UNINSTALL_REGISTRY_KEY_2
        StrCpy $runtimeLegacyUninstallRegistryKey "${RUNTIME_ORIGINAL_UNINSTALL_REGISTRY_KEY_2}"
      !endif
      !insertmacro runtimeReadFixture
      ${If} $R6 != ""
        ; Final EXE verification only uses silent CurrentUser installation.
        ${IfNot} ${Silent}
        ${OrIf} ${UAC_IsInnerInstance}
          StrCpy $R0 2
          Return
        ${EndIf}
        StrCpy $runtimeInstallRegistryKey "Software\AidWorkRuntimeCleanInstallFixtures\$R6\BuilderInstall"
        StrCpy $runtimeUninstallRegistryKey "Software\AidWorkRuntimeCleanInstallFixtures\$R6\BuilderUninstall"
        !ifdef UNINSTALL_REGISTRY_KEY_2
          StrCpy $runtimeLegacyUninstallRegistryKey "Software\AidWorkRuntimeCleanInstallFixtures\$R6\BuilderLegacyUninstall"
        !endif
      ${EndIf}
    FunctionEnd
  !endif
  Function ${RUNTIME_GUARD_FUNCTION}
    StrCpy $R9 2
    !insertmacro runtimeReadFixture
    InitPluginsDir
    File /oname=$PLUGINSDIR\runtime-clean-install.ps1 "${RUNTIME_CLEAN_INSTALL_SOURCE}"
    StrCpy $R5 ""
    !ifndef BUILD_UNINSTALLER
    nsExec::ExecToStack '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\runtime-clean-install.ps1" -Mode Detect -AppId "${APP_ID}" -InstallDirectory "$INSTDIR" -FixtureRegistryId "$R6"'
    Pop $R0
    Pop $R1
    !insertmacro runtimeSanitizeFailureCode
    ${If} $R0 != 0
      MessageBox MB_OK|MB_ICONEXCLAMATION "无法检查当前用户的旧 Runtime（$R1，退出码：$R0）。请检查当前用户权限、系统 PowerShell 或安全软件。本次安装未继续。" /SD IDOK
      Return
    ${EndIf}
    StrCpy $R5 "-RequireIdle"
    ${If} $R1 == "PROCESS_RUNNING"
      ; No processes or startup state have been changed at this point.
      ; Silent installation cannot obtain consent, so it also cancels.
      MessageBox MB_OKCANCEL|MB_ICONQUESTION|MB_DEFBUTTON2 "检测到旧 Runtime 客户端正在运行，需要退出后才能继续安装。$\r$\n$\r$\n点击“确定”将自动退出旧客户端及其本机执行进程并开始安装，当前任务会中断。$\r$\n点击“取消”将暂时不安装，旧客户端继续运行。" /SD IDCANCEL IDOK runtimeStopConfirmed
      StrCpy $R9 1
      Return
    ${ElseIf} $R1 != "PROCESS_CLEAR"
      MessageBox MB_OK|MB_ICONEXCLAMATION "无法确认旧 Runtime 的运行状态。本次安装未继续。" /SD IDOK
      Return
    ${EndIf}
    Goto runtimeStopReady
    runtimeStopConfirmed:
    StrCpy $R5 ""
    runtimeStopReady:
    !endif
    nsExec::ExecToStack '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\runtime-clean-install.ps1" -Mode Stop -AppId "${APP_ID}" -InstallDirectory "$INSTDIR" -FixtureRegistryId "$R6" $R5'
    Pop $R0
    Pop $R1
    ${If} $R0 != 0
      !insertmacro runtimeSanitizeFailureCode
      MessageBox MB_OK|MB_ICONEXCLAMATION "无法接管当前用户的旧 Runtime（$R1，退出码：$R0）。请检查当前用户权限、系统 PowerShell 或安全软件。安装未继续覆盖文件。" /SD IDOK
      Return
    ${EndIf}
    StrCpy $R9 0
  FunctionEnd
  !ifndef BUILD_UNINSTALLER
    Function runtimeFinalizeAsUser
      !insertmacro runtimeReadFixture
      InitPluginsDir
      File /oname=$PLUGINSDIR\runtime-clean-install.ps1 "${RUNTIME_CLEAN_INSTALL_SOURCE}"
      StrCpy $R5 ""
      ${GetParameters} $R2
      ClearErrors
      ${GetOptions} $R2 "/Clean" $R3
      ${IfNot} ${Errors}
        StrCpy $R5 "-Clean"
      ${EndIf}
      ClearErrors
      nsExec::ExecToStack '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\runtime-clean-install.ps1" -Mode "$R7" -AppId "${APP_ID}" -InstallDirectory "$INSTDIR" -FixtureRegistryId "$R6" $R5'
      Pop $R0
      Pop $R1
    FunctionEnd
    Function runtimeCallFinalize
      StrCpy $R0 2
      ClearErrors
      ${If} ${UAC_IsInnerInstance}
        !insertmacro UAC_AsUser_Call Function runtimeFinalizeAsUser ${UAC_SYNCREGISTERS}|${UAC_SYNCINSTDIR}
      ${Else}
        Call runtimeFinalizeAsUser
      ${EndIf}
      ${If} ${Errors}
        StrCpy $R0 2
      ${EndIf}
    FunctionEnd
  !endif
!macroend
!macro customInstall
  StrCpy $R7 "Verify"
  Call runtimeCallFinalize
  ${If} $R0 != 0
    !insertmacro runtimeSanitizeFailureCode
    MessageBox MB_OK|MB_ICONEXCLAMATION "安装检查未通过（$R1，退出码：$R0）。新版 Node Host、凭据保护或安装文件无法使用，请检查目录权限、系统 PowerShell 与安全软件后重新安装。" /SD IDOK
    SetErrorLevel 2
    Quit
  ${EndIf}
  StrCpy $R7 "Finalize"
  Call runtimeCallFinalize
  ${If} $R0 != 0
    !insertmacro runtimeSanitizeFailureCode
    MessageBox MB_OK|MB_ICONEXCLAMATION "安装代际登记失败（$R1，退出码：$R0）。请检查当前 Windows 用户配置目录权限后重新安装。" /SD IDOK
    SetErrorLevel 2
    Quit
  ${EndIf}
  ; The user callback writes the user pointer; the file-copy identity writes this
  ; stamp, including per-machine installations where the user cannot write here.
  StrCpy $R1 $R1 36
  FileOpen $R0 "$INSTDIR\runtime-installation.id" w
  FileWrite $R0 "$R1"
  FileClose $R0
  ${If} ${Errors}
    MessageBox MB_OK|MB_ICONEXCLAMATION "安装代际文件无法保存，请检查安装目录权限后重新安装。" /SD IDOK
    SetErrorLevel 2
    Quit
  ${EndIf}
  ${If} $R6 != ""
    ; The fixture's ordinary generated uninstaller is never executed. Clear
    ; only its isolated keys so the next fixture install copies the final EXE
    ; payload without invoking a production-key uninstaller.
    DeleteRegValue HKCU "$runtimeUninstallRegistryKey" UninstallString
    DeleteRegValue HKCU "$runtimeUninstallRegistryKey" QuietUninstallString
  ${EndIf}
!macroend
!macro customUnInstall
  ${IfNot} ${isUpdated}
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "${APP_ID}"
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\RunOnce" "${APP_ID}.upgrade"
  ${EndIf}
!macroend
