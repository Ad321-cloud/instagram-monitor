.TL
Instagram Username Monitor
.AU
Plain User Manual
.AB
This manual explains how the monitor works, how to run it on a Linux PC or Mac, and how to switch between two machines safely.
.AE
.NH 1
What the monitor does
.PP
The program checks each monitored Instagram username every two minutes. It stores the latest result and history in Supabase and sends status changes to Telegram.
.PP
Direct checks are used by default. A username returning HTTP 200 is treated as ACTIVE. HTTP 404 is treated as AVAILABLE. Login pages, redirects, rate limits, and network errors are treated as UNKNOWN and do not overwrite the last reliable status.
.NH 1
Important rule
.PP
Run only one copy at a time when using the same Telegram bot token. Before the Mac starts, stop the monitor on the PC with Ctrl-C. Otherwise Telegram polling can conflict and notifications may be duplicated.
.NH 1
Run on the existing PC
.PP
Open a terminal and run:
.DS
cd /home/aditya/Projects/instagram-monitor
source venv/bin/activate
python -m app.cli run
.DE
.PP
Keep this terminal open. Stop the monitor with Ctrl-C.
.NH 1
Beginner Mac setup
.PP
The project folder includes three helper files: mac-setup.command, mac-start.command, and mac-stop.command. After setup, your friend does not need to type Python commands.
.PP
First install Homebrew. Open Safari, visit https://brew.sh, copy the installation command shown there, and paste it into Terminal. Follow the prompts and enter the Mac password if asked.
.PP
Get the project folder as a ZIP file. Unzip it and move the instagram-monitor folder to the Desktop. Do not include your existing .env file in the ZIP.
.PP
Open the folder, right-click mac-setup.command, choose Open, and confirm Open if macOS shows a security warning. The setup window asks for:
.IP \(bu 2
Supabase pooler host, database user, and database password.
.IP \(bu 2
Telegram bot token and Telegram chat ID.
.PP
Use the same values as the working PC. The password is typed privately and is not shown on screen. The setup installs the required software and creates the local .env file.
.PP
After setup, double-click mac-start.command. A small Terminal window may remain open. Check Telegram for the startup message.
.PP
To stop monitoring, double-click mac-stop.command. To start it again, double-click mac-start.command.
.NH 1
Switch from PC to Mac
.IP 1.
In the PC terminal, press Ctrl-C and wait for shutdown to finish.
.IP 2.
On the Mac, open Terminal and run the start command.
.IP 3.
Check Telegram for the message “Instagram Monitor Started”.
.IP 4.
Use /list or /check in Telegram to confirm the monitor is responding.
.PP
If macOS blocks a command file, right-click it, choose Open, and confirm Open. If the friend sees a setup error, send the error text rather than sending passwords or screenshots.
.NH 1
Telegram commands
.PP
.DS
/list              Show monitored usernames and latest status
/monitor username Add a username
/demonitor username Remove a username from monitoring
/check             Run a check now
/status            Show service status
.DE
.NH 1
Troubleshooting
.PP
If authentication fails, verify DB_PASSWORD in .env. It must be the Supabase database password, not an API key.
.PP
If Telegram does not respond, verify TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID. Only one monitor may be running for that token.
.PP
If a profile shows UNKNOWN, wait for the next cycle. UNKNOWN means the response was blocked, redirected, rate-limited, or unavailable; it is not proof that the profile is inactive.
.PP
If a profile was previously shown as UNAVAILABLE but is actually live, run the fixed direct-check configuration. The next successful HTTP 200 check changes it back to ACTIVE and sends a return notification.
.NH 1
Security
.PP
Never commit .env, paste passwords into chat, or share screenshots containing tokens. Send credentials through a secure private channel. If a token or password is exposed, rotate it immediately.
.NH 1
Quick commands
.PP
Stop: Ctrl-C. Start again: cd into the project, activate venv, and run python -m app.cli run.
