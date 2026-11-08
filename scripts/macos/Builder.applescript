on run
	set appRoot to POSIX path of (path to me)
	set tools to appRoot & "Contents/Resources/tools/"
	set source to tools & "source"
	set commandLine to "export HOOKSHOT_TOOLS=" & quoted form of source & " && export WGPU_NATIVE=" & quoted form of (tools & "wgpu-native") & " && export HOOKSHOT_SDL2=" & quoted form of (tools & "sdl2/lib/libSDL2-2.0.0.dylib") & " && export HOOKSHOT_FFMPEG=" & quoted form of (tools & "ffmpeg") & " && export RX_BACKEND=metal && exec arch -x86_64 " & quoted form of (tools & "python/bin/python3") & " -I " & quoted form of (source & "/builder/linux/hookshot_builder.py")
	do shell script commandLine
end run
