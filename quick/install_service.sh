#!/bin/zsh
# ============================================================
# 安装 / 卸载 Finder 右键「转文字」快速操作
#   install_service.sh install   安装
#   install_service.sh uninstall 卸载
# ============================================================
set -u

# 项目根目录：由脚本自身位置推导（quick/ 的上一级），换机器或换克隆路径都无需改代码
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUNNER="$ROOT/quick/quick_transcribe.sh"
SERVICES_DIR="$HOME/Library/Services"
# 包名用 ASCII（全角括号会让 automator CLI 与部分系统接口解析失败），菜单显示名保持中文
ACTION="Whisper Transcribe"
ACTION_DIR="Whisper Transcribe Folder"
MENU_LABEL="转文字（whisper）"
MENU_LABEL_DIR="转文字（整个文件夹）"

chmod +x "$RUNNER" 2>/dev/null

build_workflow() {
  local WF_NAME="$1"       # workflow 包名（不含 .workflow）
  local TYPE_ID="$2"       # com.apple.Automator.fileSystemObject.movie / .folder
  local LABEL="$3"         # 右键菜单显示名
  local WF="$SERVICES_DIR/$WF_NAME.workflow"

  rm -rf "$WF"
  mkdir -p "$WF/Contents"

  # ---- Info.plist ----
  cat > "$WF/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>NSServices</key>
	<array>
		<dict>
			<key>NSBackgroundColorName</key>
			<string>background</string>
			<key>NSIconName</key>
			<string>NSActionTemplate</string>
			<key>NSMenuItem</key>
			<dict>
				<key>default</key>
				<string>__MENU_LABEL__</string>
			</dict>
			<key>NSMessage</key>
			<string>runWorkflowAsService</string>
		</dict>
	</array>
</dict>
</plist>
PLIST

  # ---- document.wflow ----
  cat > "$WF/Contents/document.wflow" <<'WFLOW'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>AMApplicationBuild</key>
	<string>534</string>
	<key>AMApplicationVersion</key>
	<string>2.10</string>
	<key>AMDocumentVersion</key>
	<string>2</string>
	<key>actions</key>
	<array>
		<dict>
			<key>action</key>
			<dict>
				<key>AMAccepts</key>
				<dict>
					<key>Container</key>
					<string>List</string>
					<key>Optional</key>
					<true/>
					<key>Types</key>
					<array>
						<string>com.apple.cocoa.string</string>
					</array>
				</dict>
				<key>AMActionVersion</key>
				<string>2.0.3</string>
				<key>AMApplication</key>
				<array>
					<string>Automator</string>
				</array>
				<key>AMParameterProperties</key>
				<dict>
					<key>COMMAND_STRING</key>
					<dict/>
					<key>CheckedForUserDefaultShell</key>
					<dict/>
					<key>inputMethod</key>
					<dict/>
					<key>shell</key>
					<dict/>
					<key>source</key>
					<dict/>
				</dict>
				<key>AMProvides</key>
				<dict>
					<key>Container</key>
					<string>List</string>
					<key>Types</key>
					<array>
						<string>com.apple.cocoa.string</string>
					</array>
				</dict>
				<key>ActionBundlePath</key>
				<string>/System/Library/Automator/Run Shell Script.action</string>
				<key>ActionName</key>
				<string>Run Shell Script</string>
				<key>ActionParameters</key>
				<dict>
					<key>COMMAND_STRING</key>
					<string>exec "__RUNNER_PATH__" "$@"</string>
					<key>CheckedForUserDefaultShell</key>
					<true/>
					<key>inputMethod</key>
					<integer>1</integer>
					<key>shell</key>
					<string>/bin/zsh</string>
					<key>source</key>
					<string></string>
				</dict>
				<key>BundleIdentifier</key>
				<string>com.apple.RunShellScript</string>
				<key>CFBundleVersion</key>
				<string>2.0.3</string>
				<key>CanShowSelectedItemsWhenRun</key>
				<false/>
				<key>CanShowWhenRun</key>
				<true/>
				<key>Category</key>
				<array>
					<string>AMCategoryUtilities</string>
				</array>
				<key>Class Name</key>
				<string>RunShellScriptAction</string>
				<key>InputUUID</key>
				<string>__UUID_IN__</string>
				<key>Keywords</key>
				<array>
					<string>Shell</string>
					<string>Script</string>
					<string>whisper</string>
					<string>转文字</string>
				</array>
				<key>OutputUUID</key>
				<string>__UUID_OUT__</string>
				<key>UUID</key>
				<string>__UUID_ACT__</string>
				<key>UnlocalizedApplications</key>
				<array>
					<string>Automator</string>
				</array>
				<key>arguments</key>
				<dict>
					<key>0</key>
					<dict>
						<key>default value</key>
						<integer>0</integer>
						<key>name</key>
						<string>inputMethod</string>
						<key>required</key>
						<string>0</string>
						<key>type</key>
						<string>0</string>
						<key>uuid</key>
						<string>0</string>
					</dict>
					<key>1</key>
					<dict>
						<key>default value</key>
						<false/>
						<key>name</key>
						<string>CheckedForUserDefaultShell</string>
						<key>required</key>
						<string>0</string>
						<key>type</key>
						<string>0</string>
						<key>uuid</key>
						<string>1</string>
					</dict>
					<key>2</key>
					<dict>
						<key>default value</key>
						<string></string>
						<key>name</key>
						<string>source</string>
						<key>required</key>
						<string>0</string>
						<key>type</key>
						<string>0</string>
						<key>uuid</key>
						<string>2</string>
					</dict>
					<key>3</key>
					<dict>
						<key>default value</key>
						<string></string>
						<key>name</key>
						<string>COMMAND_STRING</string>
						<key>required</key>
						<string>0</string>
						<key>type</key>
						<string>0</string>
						<key>uuid</key>
						<string>3</string>
					</dict>
					<key>4</key>
					<dict>
						<key>default value</key>
						<string>/bin/sh</string>
						<key>name</key>
						<string>shell</string>
						<key>required</key>
						<string>0</string>
						<key>type</key>
						<string>0</string>
						<key>uuid</key>
						<string>4</string>
					</dict>
				</dict>
				<key>conversionLabel</key>
				<integer>0</integer>
				<key>isViewVisible</key>
				<integer>1</integer>
				<key>location</key>
				<string>309.000000:305.000000</string>
				<key>nibPath</key>
				<string>/System/Library/Automator/Run Shell Script.action/Contents/Resources/Base.lproj/main.nib</string>
			</dict>
			<key>isViewVisible</key>
			<integer>1</integer>
		</dict>
	</array>
	<key>connectors</key>
	<dict/>
	<key>workflowMetaData</key>
	<dict>
		<key>applicationBundleID</key>
		<string>com.apple.finder</string>
		<key>applicationBundleIDsByPath</key>
		<dict>
			<key>/System/Library/CoreServices/Finder.app</key>
			<string>com.apple.finder</string>
		</dict>
		<key>applicationPath</key>
		<string>/System/Library/CoreServices/Finder.app</string>
		<key>applicationPaths</key>
		<array>
			<string>/System/Library/CoreServices/Finder.app</string>
		</array>
		<key>inputTypeIdentifier</key>
		<string>__TYPE_ID__</string>
		<key>outputTypeIdentifier</key>
		<string>com.apple.Automator.nothing</string>
		<key>presentationMode</key>
		<integer>15</integer>
		<key>processesInput</key>
		<false/>
		<key>serviceApplicationBundleID</key>
		<string>com.apple.finder</string>
		<key>serviceApplicationPath</key>
		<string>/System/Library/CoreServices/Finder.app</string>
		<key>serviceInputTypeIdentifier</key>
		<string>__TYPE_ID__</string>
		<key>serviceOutputTypeIdentifier</key>
		<string>com.apple.Automator.nothing</string>
		<key>serviceProcessesInput</key>
		<false/>
		<key>systemImageName</key>
		<string>NSActionTemplate</string>
		<key>useAutomaticInputType</key>
		<false/>
		<key>workflowTypeIdentifier</key>
		<string>com.apple.Automator.servicesMenu</string>
	</dict>
</dict>
</plist>
WFLOW

  # ---- 占位替换 ----
  /usr/bin/sed -i '' \
    -e "s|__RUNNER_PATH__|$RUNNER|g" \
    -e "s|__TYPE_ID__|$TYPE_ID|g" \
    -e "s|__UUID_IN__|$(uuidgen)|g" \
    -e "s|__UUID_OUT__|$(uuidgen)|g" \
    -e "s|__UUID_ACT__|$(uuidgen)|g" \
    "$WF/Contents/document.wflow"

  /usr/bin/sed -i '' -e "s|__MENU_LABEL__|$LABEL|g" "$WF/Contents/Info.plist"

  # ---- 校验 ----
  local ok=1
  /usr/bin/plutil -lint "$WF/Contents/Info.plist" >/dev/null 2>&1 || ok=0
  /usr/bin/plutil -lint "$WF/Contents/document.wflow" >/dev/null 2>&1 || ok=0
  if [[ "$ok" -eq 1 ]]; then
    echo "  ✓ $WF_NAME.workflow  生成成功（plist 校验通过）"
  else
    echo "  ✗ $WF_NAME.workflow  plist 校验失败"
    return 1
  fi
}

refresh_services() {
  # 让系统重新扫描服务（不重启 Finder，避免打断用户当前窗口）
  /usr/bin/killall -u "$USER" pbs 2>/dev/null || true
  /System/Library/CoreServices/pbs -update 2>/dev/null || true
  /System/Library/CoreServices/pbs -flush 2>/dev/null || true
}

case "${1:-install}" in
  install)
    echo "== 安装 Finder 快速操作 =="
    mkdir -p "$SERVICES_DIR"
    chmod 755 "$RUNNER" || true
    build_workflow "$ACTION" "com.apple.Automator.fileSystemObject.movie" "$MENU_LABEL"
    build_workflow "$ACTION_DIR" "com.apple.Automator.fileSystemObject.folder" "$MENU_LABEL_DIR"
    echo "== 刷新服务注册 =="
    refresh_services
    echo "完成。"
    ;;
  uninstall)
    echo "== 卸载 =="
    rm -rf "$SERVICES_DIR/$ACTION.workflow" "$SERVICES_DIR/$ACTION_DIR.workflow"
    refresh_services
    echo "已移除两个快速操作。"
    ;;
  *)
    echo "用法：$0 {install|uninstall}"
    exit 1
    ;;
esac
