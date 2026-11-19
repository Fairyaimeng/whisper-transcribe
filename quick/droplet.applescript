-- 转文字 droplet（Finder「打开方式」入口）
-- 设计要点：本应用自身不读取任何用户文件，只把选中路径拼进 /tmp 下的 .command 脚本，
-- 再交给已获授权的 Terminal 执行，由 Terminal 承担 TCC 文件访问权限。
-- 链路：本 app → /tmp/whisper_quick_<stamp>.command → Terminal → tui.py（自带调度引擎）

property pyPath : "/usr/bin/python3"

-- 动态定位 tui.py：不写死任何用户路径
-- 查找顺序：环境变量 WHISPER_TUI → 主目录常见克隆位置 → 主目录浅层搜索
on resolveTuiPath()
	set homeDir to POSIX path of (path to home folder)
	set candidates to {homeDir & "whisper-transcribe/tui.py", homeDir & "Documents/whisper-transcribe/tui.py", homeDir & "Desktop/whisper-transcribe/tui.py", homeDir & "Developer/whisper-transcribe/tui.py", homeDir & "Applications/whisper-transcribe/tui.py"}
	try
		set envPath to do shell script "/usr/bin/printenv WHISPER_TUI || true"
		if envPath is not "" then set beginning of candidates to {envPath}
	end try
	repeat with c in candidates
		try
			do shell script "/bin/test -f " & quoted form of (c as text)
			return c as text
		end try
	end repeat
	-- 兜底：在主目录下浅层搜索（限深度、限时，找不到返回空串）
	try
		set found to do shell script "/usr/bin/find " & quoted form of homeDir & " -maxdepth 4 -name tui.py -path '*whisper-transcribe*' -print -quit 2>/dev/null || true"
		if found is not "" then return found
	end try
	return ""
end resolveTuiPath

on open theItems
	set pathList to {}
	repeat with anItem in theItems
		try
			set end of pathList to POSIX path of anItem
		end try
	end repeat

	if (count of pathList) = 0 then
		display dialog "没有收到文件。请先在 Finder 里选中视频或文件夹，再用「打开方式」选择「转文字」。" buttons {"好"} default button 1 with icon caution
		return
	end if

	-- 动态解析 tui.py 路径，找不到时给出明确提示而不是拼一个无效命令
	set tuiPath to my resolveTuiPath()
	if tuiPath is "" then
		display dialog "找不到 tui.py。请把本项目克隆到主目录（如 ~/whisper-transcribe），或设置环境变量 WHISPER_TUI 指向 tui.py 的绝对路径。" buttons {"好"} default button 1 with icon caution
		return
	end if

	-- 拼参数（每个路径单独 quoted form，含空格安全）
	set argText to ""
	repeat with p in pathList
		set argText to argText & " " & quoted form of (p as text)
	end repeat

	set stamp to do shell script "/bin/date +%Y%m%d_%H%M%S"
	set cmdFile to "/tmp/whisper_quick_" & stamp & ".command"

	-- .command 正文：trap 保证任何退出方式都自删，不留 /tmp 残留
	set body to "#!/bin/zsh" & linefeed & ¬
		"trap '/bin/rm -f \"$0\"' EXIT INT TERM HUP" & linefeed & ¬
		"printf '\\e]0;转文字\\a'" & linefeed & ¬
		"/usr/bin/python3 " & quoted form of tuiPath & argText & linefeed

	do shell script "/usr/bin/printf '%s' " & quoted form of body & " > " & quoted form of cmdFile & " && /bin/chmod 755 " & quoted form of cmdFile & " && /usr/bin/open -g -a Terminal " & quoted form of cmdFile
end open

on run
	display dialog "用法：在 Finder 中选中视频文件或文件夹 → 右键 → 打开方式 → 选「转文字」。" buttons {"好"} default button 1 with icon note
end run
