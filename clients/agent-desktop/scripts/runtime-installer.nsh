!macro customCheckAppRunning
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
        ${If} $R0 != 0
        ${OrIf} $R2 >= 1240
          MessageBox MB_OK|MB_ICONEXCLAMATION "Runtime 任务仍在收尾或网络暂时不可用。此次安装已暂停，程序与执行记录保留；恢复网络后重新运行安装器即可。" /SD IDOK
          SetErrorLevel 2
          Quit
        ${EndIf}
      ${Loop}
    ${Else}
      !ifdef BUILD_UNINSTALLER
        MessageBox MB_OK|MB_ICONEXCLAMATION "旧 Runtime 仍在运行。请从托盘退出后卸载；若旧版卡住，请运行新版安装器登记自动恢复。未终止任何进程。" /SD IDOK
        SetErrorLevel 2
        Quit
      !else
        nsExec::ExecToStack '"$PowerShellPath" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\runtime-legacy-upgrade.ps1" -Mode Prepare -AppId "${APP_ID}" -InstallerPath "$EXEPATH" -InstallDirectory "$INSTDIR" -SupervisorPath "$PLUGINSDIR\RuntimeProcessSupervisor.exe"'
        Pop $R0
        Pop $R1
        ${If} $R0 == 0
          MessageBox MB_OK|MB_ICONINFORMATION "旧版异常退出的恢复已登记。请正常重启 Windows；登录后会自动继续安装并恢复原设备连接，无需卸载、重新配对或提供日志。此次未终止旧程序。" /SD IDOK
          SetErrorLevel 3010
        ${Else}
          MessageBox MB_OK|MB_ICONEXCLAMATION "旧版仍在运行，无法登记自动恢复。请先从托盘选择退出，再运行安装器；此次未覆盖或终止旧程序。" /SD IDOK
          SetErrorLevel 2
        ${EndIf}
        Quit
      !endif
    ${EndIf}
  ${ElseIf} $R0 != 603
    MessageBox MB_OK|MB_ICONEXCLAMATION "无法核验 Runtime 进程状态，安装已停止，未终止任何进程。" /SD IDOK
    SetErrorLevel 2
    Quit
  ${EndIf}
!macroend

!ifndef BUILD_UNINSTALLER
  Function runtimeCancelPendingUpgrade
    InitPluginsDir
    File /oname=$PLUGINSDIR\runtime-legacy-upgrade.ps1 "${RUNTIME_LEGACY_UPGRADE_SOURCE}"
    nsExec::ExecToStack '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\runtime-legacy-upgrade.ps1" -Mode Cancel -AppId "${APP_ID}" -InstallDirectory "$INSTDIR"'
    Pop $R0
    Pop $R1
  FunctionEnd
  Function .onInstFailed
    Call runtimeCancelPendingUpgrade
    MessageBox MB_OK|MB_ICONEXCLAMATION "安装未完成，已尝试恢复原登录启动设置。请重新运行安装器继续；设备配对和任务记录仍保留。" /SD IDOK
  FunctionEnd
  !define MUI_CUSTOMFUNCTION_ABORT runtimeCancelPendingUpgrade
!endif

!macro customInstall
  nsExec::ExecToStack '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\runtime-legacy-upgrade.ps1" -Mode Restore -AppId "${APP_ID}" -InstallDirectory "$INSTDIR"'
  Pop $R0
  Pop $R1
  ${If} $R0 != 0
    MessageBox MB_OK|MB_ICONEXCLAMATION "安装完成，但登录启动项尚未恢复。请打开 Runtime 并在托盘菜单重新启用自动启动。" /SD IDOK
  ${EndIf}
!macroend

!macro customUnInstall
  ; Remove only this product's current-user login item; retain Runtime facts/data.
  ${IfNot} ${isUpdated}
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "${APP_ID}"
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run" "${APP_ID}"
    ; Allow a later reinstall to register again, while retaining all execution facts.
    ${If} "${APP_ID}" == "cn.aidingyi.agent.runtime.acceptance"
      Delete "$APPDATA\aidwork-runtime-app-acceptance\login-startup-initialized"
    ${Else}
      Delete "$APPDATA\aidwork-runtime-app\login-startup-initialized"
    ${EndIf}
  ${EndIf}
!macroend
