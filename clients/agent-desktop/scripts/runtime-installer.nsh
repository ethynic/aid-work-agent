; CurrentUser DPAPI and HKCU work always runs in the original UAC outer user.
!ifdef BUILD_UNINSTALLER
  !define RUNTIME_GUARD_FUNCTION "un.runtimeCheckAppRunning"
!else
  !define RUNTIME_GUARD_FUNCTION "runtimeCheckAppRunning"
  !define MUI_CUSTOMFUNCTION_ABORT runtimeCancelPendingUpgrade
!endif

!macro runtimeCheckBeforeInstall
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
!macroend

!macro customInit
  ; Silent installs do not run page callbacks. Resolve the machine target first.
  ${If} ${UAC_IsInnerInstance}
  ${AndIf} ${Silent}
    !insertmacro setInstallModePerAllUsers
    !insertmacro runtimeCheckBeforeInstall
  ${EndIf}
!macroend

!macro customPageAfterChangeDir
  ; Wrap the actual InstFiles PRE callback, after the final directory choice.
  ; The builder still skips its section guard in an assisted inner instance.
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

; Parsed after the builder declares APP_EXECUTABLE_FILENAME and installMode.
!macro customHeader
  Function ${RUNTIME_GUARD_FUNCTION}
    StrCpy $R9 2
  InitPluginsDir
  File /oname=$PLUGINSDIR\runtime-legacy-upgrade.ps1 "${RUNTIME_LEGACY_UPGRADE_SOURCE}"
  File /oname=$PLUGINSDIR\RuntimeProcessSupervisor.exe "${RUNTIME_SUPERVISOR_SOURCE}"
  ; Never delegate process termination to the default builder check.
  ${nsProcess::FindProcess} "${APP_EXECUTABLE_FILENAME}" $R0
  ${If} $R0 == 0
    ${If} ${FileExists} "$INSTDIR\resources\runtime\host\native\RuntimeProcessSupervisor.exe"
      !ifdef BUILD_UNINSTALLER
        ${StdUtils.ExecShellAsUser} $R1 "$INSTDIR\${APP_EXECUTABLE_FILENAME}" "open" "--aidwork-exit-for-uninstall"
      !else
        ${StdUtils.ExecShellAsUser} $R1 "$INSTDIR\${APP_EXECUTABLE_FILENAME}" "open" "--aidwork-exit-for-update"
      !endif
      DetailPrint "正在停止领取新任务，等待在途任务安全收尾…"
      StrCpy $R2 0
      ${Do}
        Sleep 500
        ${nsProcess::FindProcess} "${APP_EXECUTABLE_FILENAME}" $R0
        ${If} $R0 == 603
          ${ExitDo}
        ${EndIf}
        IntOp $R2 $R2 + 1
        !ifndef BUILD_UNINSTALLER
          ${If} $R0 == 0
          ${AndIf} $R2 == 30
            ; r3 has the exit flag but cannot close an idle legacy-marker Host.
            ; Only a strictly validated v1 marker can register this one-reboot fix.
            nsExec::ExecToStack '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\runtime-legacy-upgrade.ps1" -Mode Prepare -AppId "${APP_ID}" -InstallerPath "$EXEPATH" -InstallDirectory "$INSTDIR" -SupervisorPath "$PLUGINSDIR\RuntimeProcessSupervisor.exe"'
            Pop $R0
            Pop $R1
            ${If} $R0 == 0
              MessageBox MB_OK|MB_ICONINFORMATION "旧客户端的退出已被历史记录阻断。恢复已登记，请正常重启 Windows；登录后会自动继续安装，配对和任务记录保留。" /SD IDOK
              StrCpy $R9 3010
              Return
            ${EndIf}
            ; A current v2 execution keeps its original drain path and timeout.
            StrCpy $R0 0
          ${EndIf}
        !endif
        ${If} $R0 != 0
        ${OrIf} $R2 >= 1240
          MessageBox MB_OK|MB_ICONEXCLAMATION "Runtime 任务仍在收尾或网络暂时不可用。此次安装已暂停，程序与执行记录保留；恢复网络后重新运行安装器即可。" /SD IDOK
          StrCpy $R9 2
          Return
        ${EndIf}
      ${Loop}
    ${Else}
      !ifdef BUILD_UNINSTALLER
        MessageBox MB_OK|MB_ICONEXCLAMATION "旧 Runtime 仍在运行。请从托盘退出后卸载；若旧版卡住，请运行新版安装器登记自动恢复。未终止任何进程。" /SD IDOK
        StrCpy $R9 2
        Return
      !else
        nsExec::ExecToStack '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\runtime-legacy-upgrade.ps1" -Mode Prepare -AppId "${APP_ID}" -InstallerPath "$EXEPATH" -InstallDirectory "$INSTDIR" -SupervisorPath "$PLUGINSDIR\RuntimeProcessSupervisor.exe"'
        Pop $R0
        Pop $R1
        ${If} $R0 == 0
          MessageBox MB_OK|MB_ICONINFORMATION "旧版异常退出的恢复已登记。请正常重启 Windows；登录后会自动继续安装并恢复原设备连接，无需卸载、重新配对或提供日志。此次未终止旧程序。" /SD IDOK
          StrCpy $R9 3010
        ${Else}
          MessageBox MB_OK|MB_ICONEXCLAMATION "旧版仍在运行，无法登记自动恢复。请先从托盘选择退出，再运行安装器；此次未覆盖或终止旧程序。" /SD IDOK
          StrCpy $R9 2
        ${EndIf}
        Return
      !endif
    ${EndIf}
  ${ElseIf} $R0 != 603
    MessageBox MB_OK|MB_ICONEXCLAMATION "无法核验 Runtime 进程状态，安装已停止，未终止任何进程。" /SD IDOK
    StrCpy $R9 2
    Return
  ${EndIf}
    StrCpy $R9 0
  FunctionEnd

  !ifndef BUILD_UNINSTALLER
    Function runtimePendingUpgrade
      StrCpy $R0 1004
      ReadEnvStr $R8 "APPDATA"
      ; Match the PowerShell environment, not all-users $APPDATA/ProgramData.
      StrCpy $R1 $R8 2 1
      ${If} $R1 != ":\"
        Return
      ${EndIf}
      System::Call 'kernel32::GetFileAttributesW(w "$R8") i.r0'
      ${If} $0 == -1
        Return
      ${EndIf}
      IntOp $0 $0 & 16
      ${If} $0 == 0
        Return
      ${EndIf}
      ${If} "${APP_ID}" == "cn.aidingyi.agent.runtime.acceptance"
        StrCpy $R8 "$R8\aidwork-runtime-app-acceptance\upgrade-pending\state.bin"
      ${Else}
        StrCpy $R8 "$R8\aidwork-runtime-app\upgrade-pending\state.bin"
      ${EndIf}
      ; Only real FILE/PATH_NOT_FOUND is a no-op. Access errors go to validation.
      System::Call 'kernel32::GetFileAttributesW(w "$R8") i.r0 ?e'
      Pop $R0
      ${If} $0 == -1
      ${AndIf} $R0 == 2
      ${OrIf} $0 == -1
      ${AndIf} $R0 == 3
        StrCpy $R0 0
        Return
      ${EndIf}
      InitPluginsDir
      File /oname=$PLUGINSDIR\runtime-legacy-upgrade.ps1 "${RUNTIME_LEGACY_UPGRADE_SOURCE}"
      nsExec::ExecToStack '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\runtime-legacy-upgrade.ps1" -Mode "$R7" -AppId "${APP_ID}" -InstallDirectory "$INSTDIR"'
      Pop $R0
      Pop $R1
      ; R1 may contain PowerShell paths. Never display or log its raw contents.
    FunctionEnd

    Function runtimeCallPendingUpgrade
      StrCpy $R0 1005
      ClearErrors
      ${If} ${UAC_IsInnerInstance}
        !insertmacro UAC_AsUser_Call Function runtimePendingUpgrade ${UAC_SYNCREGISTERS}|${UAC_SYNCINSTDIR}
      ${Else}
        Call runtimePendingUpgrade
      ${EndIf}
      ${If} ${Errors}
        StrCpy $R0 1005
      ${EndIf}
      ${If} $R0 < 0
      ${OrIf} $R0 == "error"
      ${OrIf} $R0 == "timeout"
        ; One bounded retry after a process/launcher failure. Revalidate the same
        ; protected state; validation errors (exit 1) are never blindly retried.
        Sleep 250
        StrCpy $R0 1005
        ClearErrors
        ${If} ${UAC_IsInnerInstance}
          !insertmacro UAC_AsUser_Call Function runtimePendingUpgrade ${UAC_SYNCREGISTERS}|${UAC_SYNCINSTDIR}
        ${Else}
          Call runtimePendingUpgrade
        ${EndIf}
        ${If} ${Errors}
          StrCpy $R0 1005
        ${EndIf}
      ${EndIf}
    FunctionEnd

    Function runtimeCancelPendingUpgrade
      StrCpy $R7 "Cancel"
      Call runtimeCallPendingUpgrade
    FunctionEnd
    Function .onInstFailed
      Call runtimeCancelPendingUpgrade
      MessageBox MB_OK|MB_ICONEXCLAMATION "安装未完成，已尝试恢复原登录启动设置。请重新运行安装器继续；设备配对和任务记录仍保留。" /SD IDOK
    FunctionEnd
  !endif
!macroend

!macro customInstall
  StrCpy $R7 "Restore"
  Call runtimeCallPendingUpgrade
  ${If} $R0 != 0
    MessageBox MB_OK|MB_ICONEXCLAMATION "安装完成，但旧版登录启动设置未能自动恢复（错误码：$R0）。设备配置保留；打开 Runtime 后可从托盘确认自动启动选项。" /SD IDOK
  ${EndIf}
!macroend

!macro customUnInstall
  ; Remove only this product's current-user login item; retain Runtime facts/data.
  ${IfNot} ${isUpdated}
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "${APP_ID}"
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\RunOnce" "${APP_ID}.upgrade"
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run" "${APP_ID}"
    ; Allow a later reinstall to register again, while retaining all execution facts.
    ${If} "${APP_ID}" == "cn.aidingyi.agent.runtime.acceptance"
      Delete "$APPDATA\aidwork-runtime-app-acceptance\login-startup-initialized"
    ${Else}
      Delete "$APPDATA\aidwork-runtime-app\login-startup-initialized"
    ${EndIf}
  ${EndIf}
!macroend
