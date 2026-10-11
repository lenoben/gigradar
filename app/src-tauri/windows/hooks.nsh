; Gigradar installer hooks (tauri.conf.json: bundle.windows.nsis.installerHooks).
;
; Install:   register the gigradar:// link, so clicking a notification starts the app on the job it names.
; Uninstall: remove the app's own scheduled task and nothing else; delete the data folder only when the user ticked
;            "Also delete my data" on the confirm page (unticked = kept, also in a silent uninstall).
; An update runs the old uninstaller with /UPDATE ($UpdateMode = 1): then the task, the data and the link stay.
;
; The names below must match gigradar/branding.py (TASK_FOLDER + TASK_NAME, DATA_DIR_NAME, APP_ID, LINK_SCHEME);
; tests/test_branding.py checks that.

!define GIGRADAR_SCHEME "gigradar"
!define GIGRADAR_TASK "\Gigradar Desktop\checks"
!define GIGRADAR_DATA "$LOCALAPPDATA\Gigradar Data"
!define GIGRADAR_AUMID "app.gigradar.desktop"

!macro NSIS_HOOK_POSTINSTALL
  WriteRegStr HKCU "Software\Classes\${GIGRADAR_SCHEME}" "" "URL:${PRODUCTNAME}"
  WriteRegStr HKCU "Software\Classes\${GIGRADAR_SCHEME}" "URL Protocol" ""
  WriteRegStr HKCU "Software\Classes\${GIGRADAR_SCHEME}\DefaultIcon" "" "$INSTDIR\${MAINBINARYNAME}.exe,0"
  WriteRegStr HKCU "Software\Classes\${GIGRADAR_SCHEME}\shell\open\command" "" '"$INSTDIR\${MAINBINARYNAME}.exe" "%1"'
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  ${If} $UpdateMode <> 1
    ; the app's own task (never the source checkout's \gigradar\gigradar-watch); absent = nothing to do
    nsExec::ExecToLog 'schtasks /delete /tn "${GIGRADAR_TASK}" /f'
    Pop $0
  ${EndIf}
!macroend

!macro NSIS_HOOK_POSTUNINSTALL
  ${If} $UpdateMode <> 1
    DeleteRegKey HKCU "Software\Classes\${GIGRADAR_SCHEME}"
    DeleteRegKey HKCU "Software\Classes\AppUserModelId\${GIGRADAR_AUMID}"
    ${If} $DeleteAppDataCheckboxState = 1
      RmDir /r "${GIGRADAR_DATA}"
    ${EndIf}
  ${EndIf}
!macroend
