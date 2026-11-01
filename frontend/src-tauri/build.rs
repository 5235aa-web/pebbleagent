fn main() {
  // tauri-build only declares tauri.conf.json, capabilities and binaries as
  // its inputs, so a replaced file under icons/ never rebuilds the Windows
  // resource: resource.lib keeps the old image and the linked exe keeps the
  // old icon. Declaring the ico here makes a new icon a build input.
  println!("cargo:rerun-if-changed=icons/icon.ico");
  tauri_build::build()
}
