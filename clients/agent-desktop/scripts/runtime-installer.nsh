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
    !ifndef BUILD_UNINSTALLER
      ; A legacy marker cannot be drained by old Main. Prepare BEFORE requesting
      ; its maintenance exit, so no misleading old "upgrade paused" dialog appears.
      nsExec::ExecToStack '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\runtime-legacy-upgrade.ps1" -Mode Prepare -AppId "${APP_ID}" -InstallerPath "$EXEPATH" -InstallDirectory "$INSTDIR" -SupervisorPath "$PLUGINSDIR\RuntimeProcessSupervisor.exe"'
      Pop $R0
      Pop $R1
      ${If} $R0 == 0
        MessageBox MB_OK|MB_ICONINFORMATION "旧客户端的恢复已登记。请正常重启 Windows；登录后会自动继续安装，配对和任务记录保留。" /SD IDOK
        StrCpy $R9 3010
        Return
      ${ElseIf} $R0 != 2
        ; Only a missing/v2 marker means normal drain. Never hide a preparation
        ; error as an active task; raw PowerShell output can contain private paths.
        MessageBox MB_OK|MB_ICONEXCLAMATION "升级恢复登记失败（错误码：$R0），此次安装未覆盖旧程序。设备和任务记录保留。" /SD IDOK
        StrCpy $R9 2
        Return
      ${EndIf}
    !endif
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
        ${If} $R0 != 0
        ${OrIf} $R2 >= 60
          !ifndef BUILD_UNINSTALLER
            Call runtimeScheduleDeferredInstall
            ${If} $R9 == 3010
              Return
            ${EndIf}
          !endif
          MessageBox MB_OK|MB_ICONEXCLAMATION "旧 Runtime 未能在 30 秒内退出，此次未覆盖程序。请正常重启 Windows，登录后先不要启动旧客户端，立即重新运行本安装包。原配对和任务记录保留。安装后使用“连接检查与恢复”按具体提示恢复连接；不要反复覆盖、卸载或结束所有 Node 进程。" /SD IDOK
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
        Call runtimeScheduleDeferredInstall
        ${If} $R9 == 3010
          Return
        ${EndIf}
        MessageBox MB_OK|MB_ICONEXCLAMATION "旧版仍在运行。请从托盘退出后重新安装；若旧版拒绝退出，请正常重启 Windows，登录后先不要启动旧版，立即重新运行本安装包。配对和任务记录保留，此次未覆盖旧程序。" /SD IDOK
        StrCpy $R9 2
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
    Function runtimeScheduleDeferredInstall
      nsExec::ExecToStack '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\runtime-legacy-upgrade.ps1" -Mode PrepareDeferred -AppId "${APP_ID}" -InstallerPath "$EXEPATH" -InstallDirectory "$INSTDIR" -SupervisorPath "$PLUGINSDIR\RuntimeProcessSupervisor.exe"'
      Pop $R0
      Pop $R1
      ${If} $R0 == 0
        MessageBox MB_OK|MB_ICONINFORMATION "旧客户端未能退出，重启续装已登记。请正常重启 Windows；已暂时关闭本产品旧版开机启动，登录后会自动继续安装。配对和任务记录保留。安装完成后在“连接检查与恢复”中按具体提示处理；若旧资源资料无法核验，页面会另行指导登记重启恢复。" /SD IDOK
        StrCpy $R9 3010
      ${Else}
        MessageBox MB_OK|MB_ICONEXCLAMATION "重启续装登记失败（错误码：$R0）。旧程序和任务资料未覆盖。请确认原 Windows 用户、目录权限、系统 PowerShell 与凭据保护正常后重试；也可正常重启并立即重跑本安装包。" /SD IDOK
        StrCpy $R9 2
      ${EndIf}
    FunctionEnd

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
