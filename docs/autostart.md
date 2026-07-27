# Running the service at login

**You probably don't need this.** The reader service starts on demand: the first
`tabless add` or `tabless open` brings it up, and it exits quietly if another
instance already holds the port. Autostart only saves the ~1s cold start.

It becomes worth doing if you ever have something feeding the library on a
schedule, where "nobody has run a command yet today" would mean documents
arriving with nowhere to go.

Running two of them is safe either way: whichever loses the port exits
immediately.

---

## Linux — systemd user unit

`~/.config/systemd/user/tabless.service`:

```ini
[Unit]
Description=tabless reader service
After=default.target

[Service]
Type=simple
ExecStart=%h/.local/bin/tabless server
Restart=on-failure
RestartSec=5
# Only if your library isn't in the default location:
# Environment=TABLESS_HOME=%h/library

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now tabless
systemctl --user status tabless
```

`loginctl enable-linger $USER` keeps it alive between logins, if you want that.

---

## macOS — launchd

`~/Library/LaunchAgents/io.tabless.server.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>            <string>io.tabless.server</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/local/bin/tabless</string>
    <string>server</string>
  </array>
  <key>RunAtLoad</key>        <true/>
  <key>KeepAlive</key>        <true/>
</dict>
</plist>
```

```bash
launchctl load  ~/Library/LaunchAgents/io.tabless.server.plist
launchctl list | grep tabless
```

Check the path with `which tabless` first — a `pipx` install usually lands in
`~/.local/bin/`.

---

## Windows — a shortcut in the Startup folder

Open `shell:startup` (Win+R) and put a shortcut there pointing at:

```
pythonw.exe -m tabless.server
```

`pythonw` rather than `python` so no console window appears. Set the shortcut's
"Start in" to anything sensible.

Or create it from PowerShell:

```powershell
$startup = [Environment]::GetFolderPath('Startup')
$pythonw  = (Get-Command pythonw).Source
$s = (New-Object -ComObject WScript.Shell).CreateShortcut("$startup\tabless.lnk")
$s.TargetPath = $pythonw
$s.Arguments  = '-m tabless.server'
$s.Save()
```

**One caveat worth knowing.** That shortcut hard-codes an interpreter path.
Change Python environments and it fails **silently** — `pythonw` has no console,
so nothing is reported. The only consequence is falling back to on-demand
startup, so archiving keeps working either way; but if you ever do add something
that feeds the library unattended, verify it is actually alive rather than
assuming:

```powershell
Invoke-RestMethod http://127.0.0.1:6180/api/status
```

---

## Checking and stopping

```bash
curl http://127.0.0.1:6180/api/status     # pid, home, open windows, document count
```

To stop it, kill the pid that reports. It will come straight back the next time
you archive something, which is the intended behaviour.
