---
name: server-health-check
description: Check the health of a Linux server over one reused SSH shell with read-only commands, recording findings in a note
argument-hint: <user@host from ssh_hosts>
disable-model-invocation: true
allowed-tools: shell_open shell_run shell_close shell_list notes_write notes_read
---
Check the server: $ARGUMENTS

Only hosts the user listed in `ssh_hosts` can be opened. Open one shell with `shell_open` (name
"check", host above) and reuse it for every command; never log in again per command. Run only
read-only commands, one idea per call, with a short timeout:

- identity and load: `hostname; uptime; cat /etc/os-release | head -3`
- memory and disk: `free -h; df -h --total | tail -5; df -ih | head -5`
- busiest processes: `ps aux --sort=-%cpu | head -8; ps aux --sort=-%mem | head -8`
- services: `systemctl --failed --no-pager | head -20`
- recent errors: `journalctl -p err -n 20 --no-pager`
- network: `ss -tulpn | head -20`
- time and updates: `timedatectl | head -4; ls /var/run/reboot-required 2>/dev/null`

Never change anything: no restarts, installs, deletes, edits or `sudo`. If a command needs more
rights than the login has, report that instead of working around it. Write the findings with
`notes_write` (title: the host name): what is healthy, what needs attention and why, and the exact
command that showed it. Close the shell with `shell_close` and end with the prioritised list.
