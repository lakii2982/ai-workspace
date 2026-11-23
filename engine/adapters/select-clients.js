// Run with macOS osascript -l JavaScript. No third-party GUI runtime required.
ObjC.import('AppKit')
function run(argv) {
  const host = $.NSApplication.sharedApplication
  host.setActivationPolicy($.NSApplicationActivationPolicyAccessory)
  host.activateIgnoringOtherApps(true)
  const choices = [
    ['codex', 'Codex', 'com.openai.codex'], ['claude-desktop', 'Claude Desktop', 'com.anthropic.claudefordesktop'],
    ['workbuddy', 'WorkBuddy', 'com.tencent.workbuddy.mac'], ['doubao', '豆包办公', 'com.bytedance.doubaowork'],
    ['dsh', 'DeepSeek Harness', 'com.deepseek.dsh'],
  ]
  const alert = $.NSAlert.alloc.init
  alert.messageText = '安装 AI WorkSpace'
  alert.informativeText = '选择桌面 Agent 和工作空间。安装后，所选 Agent 可使用本地日常工具。'
  const panel = $.NSView.alloc.initWithFrame($.NSMakeRect(0, 0, 400, 195))
  const buttons = choices.map(([id, name, bundle], i) => {
    const box = $.NSButton.alloc.initWithFrame($.NSMakeRect(0, 165 - i * 26, 400, 25))
    box.setButtonType($.NSSwitchButton)
    box.title = name
    box.state = $.NSWorkspace.sharedWorkspace.URLForApplicationWithBundleIdentifier(bundle).isNil() ? 0 : 1
    panel.addSubview(box)
    return box
  })
  const path = $.NSTextField.alloc.initWithFrame($.NSMakeRect(0, 4, 400, 27))
  path.stringValue = argv[0]
  panel.addSubview(path)
  alert.accessoryView = panel
  alert.addButtonWithTitle('安装')
  alert.addButtonWithTitle('选择其他目录')
  alert.addButtonWithTitle('取消')
  for (;;) {
    const answer = alert.runModal
    if (answer === 1002) return ''
    if (answer === 1001) {
      const folder = $.NSOpenPanel.openPanel
      folder.canChooseDirectories = true
      folder.canChooseFiles = false
      folder.canCreateDirectories = true
      if (folder.runModal === 1) path.stringValue = folder.URL.path
      continue
    }
    const ids = choices.filter((_, i) => Number(buttons[i].state) === 1).map(c => c[0])
    if (ids.length && ObjC.unwrap(path.stringValue)) return ids.join(',') + '\n' + ObjC.unwrap(path.stringValue)
    alert.informativeText = '请至少选择一个桌面 Agent，并指定工作空间目录。'
  }
}
