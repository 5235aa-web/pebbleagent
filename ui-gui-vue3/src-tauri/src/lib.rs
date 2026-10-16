use std::process::{Child, Command as StdCommand, Stdio};
use std::os::windows::process::CommandExt;
use std::os::windows::io::AsRawHandle;
use std::sync::Mutex;
use tauri::Manager;

const CREATE_NO_WINDOW: u32 = 0x08000000;
const CREATE_NEW_PROCESS_GROUP: u32 = 0x00000200;
const HIGH_PRIORITY_CLASS: u32 = 0x00000080;
const ABOVE_NORMAL_PRIORITY_CLASS: u32 = 0x00008000;

static SIDECAR_PID: Mutex<Option<u32>> = Mutex::new(None);

extern "system" {
    fn SetPriorityClass(handle: isize, priority: u32) -> i32;
    fn GetCurrentProcess() -> isize;
    fn NtSetInformationProcess(handle: isize, info_class: u32, info: *const u32, info_len: u32) -> i32;
    fn CreateToolhelp32Snapshot(flags: u32, pid: u32) -> isize;
    fn Process32FirstW(snap: isize, entry: *mut PROCESSENTRY32W) -> i32;
    fn Process32NextW(snap: isize, entry: *mut PROCESSENTRY32W) -> i32;
    fn CloseHandle(handle: isize) -> i32;
    fn OpenProcess(access: u32, inherit: i32, pid: u32) -> isize;
}

const TH32CS_SNAPPROCESS: u32 = 0x00000002;
const PROCESS_SET_INFORMATION: u32 = 0x0200;
const MAX_PATH: usize = 260;

#[repr(C)]
struct PROCESSENTRY32W {
    dwSize: u32,
    cntUsage: u32,
    th32ProcessID: u32,
    th32DefaultHeapID: usize,
    th32ModuleID: u32,
    cntThreads: u32,
    th32ParentProcessID: u32,
    pcPriClassBase: i32,
    dwFlags: u32,
    szExeFile: [u16; MAX_PATH],
}

unsafe fn boost_all_backends() {
    let snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    if snap == -1 { return; }
    let mut entry: PROCESSENTRY32W = std::mem::zeroed();
    entry.dwSize = std::mem::size_of::<PROCESSENTRY32W>() as u32;
    if Process32FirstW(snap, &mut entry) != 0 {
        loop {
            let name: Vec<u16> = entry.szExeFile.iter().take_while(|&&c| c != 0).copied().collect();
            let name_str = String::from_utf16_lossy(&name);
            if name_str.contains("pebble-backend") {
                let h = OpenProcess(PROCESS_SET_INFORMATION, 0, entry.th32ProcessID);
                if h != 0 {
                    SetPriorityClass(h, HIGH_PRIORITY_CLASS);
                    NtSetInformationProcess(h, ProcessIoPriority, &IoPriorityNormal, 4);
                    CloseHandle(h);
                }
            }
            if Process32NextW(snap, &mut entry) == 0 { break; }
        }
    }
    CloseHandle(snap);
}

const ProcessIoPriority: u32 = 33;
const IoPriorityNormal: u32 = 2;

fn kill_tree(pid: u32) {
  let _ = StdCommand::new("taskkill")
    .args(["/F", "/T", "/PID", &pid.to_string()])
    .creation_flags(CREATE_NO_WINDOW)
    .stdout(Stdio::null())
    .stderr(Stdio::null())
    .spawn();
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
  unsafe { SetPriorityClass(GetCurrentProcess(), HIGH_PRIORITY_CLASS); }
  let app = tauri::Builder::default()
    .plugin(tauri_plugin_shell::init())
    .setup(|app| {
      if cfg!(debug_assertions) {
        app.handle().plugin(
          tauri_plugin_log::Builder::default()
            .level(log::LevelFilter::Info)
            .build(),
        )?;
      }

      let exe_name = "pebble-backend-x86_64-pc-windows-msvc.exe";
      let sidecar = app.path().resource_dir()
        .unwrap_or_default()
        .join(exe_name);

      if sidecar.exists() {
        let mut cmd = StdCommand::new(sidecar);
        cmd.stdout(Stdio::null()).stderr(Stdio::null());
        cmd.env("PYTHONUNBUFFERED", "1");
        cmd.creation_flags(CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP);
        match cmd.spawn() {
          Ok(mut child) => {
            let pid = child.id();
            unsafe { SetPriorityClass(child.as_raw_handle() as isize, HIGH_PRIORITY_CLASS); }
            unsafe { NtSetInformationProcess(child.as_raw_handle() as isize, ProcessIoPriority, &IoPriorityNormal, 4); }
            eprintln!("[pebble-backend] started (pid: {})", pid);
            // 等 PyInstaller bootloader 衍生出 Python 子进程后再抬一遍
            std::thread::spawn(|| {
              std::thread::sleep(std::time::Duration::from_millis(1000));
              unsafe { boost_all_backends(); }
            });
            if let Ok(mut guard) = SIDECAR_PID.lock() {
              *guard = Some(pid);
            }
          }
          Err(e) => eprintln!("[pebble-backend] failed: {:?}", e),
        }
      } else {
        eprintln!("[pebble-backend] sidecar not found");
      }

      Ok(())
    })
    .build(tauri::generate_context!())
    .expect("error while building tauri application");

  app.run(|_app_handle, event| {
    if let tauri::RunEvent::Exit = event {
      if let Ok(mut guard) = SIDECAR_PID.lock() {
        if let Some(pid) = *guard {
          eprintln!("[pebble-backend] killing tree pid: {}", pid);
          kill_tree(pid);
          *guard = None;
        }
      }
    }
  });
}