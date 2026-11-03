; NSIS hooks for the Pebble Orbit installer.
;
; The updater stops the app itself and then copies files. It knows one process
; name, the main binary, because that is what Tauri's own check looks for. The
; Python backend is spawned by the shell and outlives it whenever the shell is
; terminated hard - which is exactly what the updater and this installer do. The
; surviving process keeps its executable mapped, so the copy of
; pebble-backend-x86_64-pc-windows-msvc.exe fails with "Error opening file for
; writing" and the install stops half-way, leaving a new shell next to an old
; backend.
;
; lib.rs now prevents that survivor with a job object, but a job object only
; protects builds that contain it: every copy installed before that fix still
; leaks its backend, and those are the copies that have to be upgraded. This
; hook is the only thing standing between them and the same failed install.
;
; nsExec and taskkill rather than nsProcess: nsExec ships with the NSIS
; distribution the bundler uses, so it cannot be missing at build time, and
; taskkill is part of Windows. Nothing here needs a third-party plugin.

!macro NSIS_HOOK_PREINSTALL
  DetailPrint "Stopping any running Pebble backend..."
  nsExec::ExecToLog 'taskkill /F /T /IM pebble-backend-x86_64-pc-windows-msvc.exe'
  Pop $0
  ; taskkill returns as soon as the kill is issued; Windows still has to unmap
  ; the image and drop the file lock before the copy can take the name.
  Sleep 800
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  DetailPrint "Stopping any running Pebble backend..."
  nsExec::ExecToLog 'taskkill /F /T /IM pebble-backend-x86_64-pc-windows-msvc.exe'
  Pop $0
  Sleep 800
!macroend
