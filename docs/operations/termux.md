# Termux Setup — Use Your Android Phone as a Proxy Host

This guide shows how to set up an Android phone as a SOCKS proxy host for
linkedin-mcp-pro. Useful when:

- You travel and your laptop is off
- You want a backup proxy in case your primary goes down
- You want to test LinkedIn from a mobile IP (some flows behave differently)
- You don't have a laptop, just a phone

## What you need

| Item | Requirement |
|---|---|
| Phone | Android 7.0+ (any phone from 2017+) |
| Storage | ~300 MB free |
| Network | WiFi recommended (mobile data costs may apply) |
| Time | ~15 minutes |
| Skills | None — copy-paste commands only |

## Step 1 — Install Termux

⚠️ **Don't install Termux from Google Play** — that version is outdated and
no longer maintained.

Install from **F-Droid**:
1. Install [F-Droid](https://f-droid.org/) from their website
2. Open F-Droid, search "Termux"
3. Install Termux
4. Open Termux

Or from the [Termux GitHub releases](https://github.com/termux/termux-app/releases).

## Step 2 — Run the setup script

In Termux, run:

```bash
pkg install curl -y
curl -fsSL https://raw.githubusercontent.com/horizonbymuneeb/linkedin-mcp-pro/main/scripts/termux_setup.sh | bash
```

Or if you've cloned the repo:

```bash
git clone https://github.com/horizonbymuneeb/linkedin-mcp-pro
cd linkedin-mcp-pro
bash scripts/termux_setup.sh
```

The script will:
1. Update package lists
2. Install openssh, tmux, rsync, curl, python
3. Install cloudflared (the tunnel client)
4. Generate an SSH keypair for the server
5. Configure sshd on port 8022 (no password, key-only)
6. Start sshd in a tmux session called `linkedin-ssh`
7. Install a `linkedin-proxy` helper command

## Step 3 — Save the public key

The script prints your phone's public key at the end. **Copy it.**

Then on your **server**, add it to `~/.ssh/authorized_keys`:

```bash
# On server
echo "ssh-ed25519 AAAA... linkedin-mcp-pro@2026-XX-XX" >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```

## Step 4 — Start the cloudflared tunnel

Back on your **phone**, start a one-shot cloudflared tunnel:

```bash
cloudflared tunnel --url ssh://localhost:8022
```

You'll see output like:
```
Your quick tunnel has been created! Visit it at:
https://random-words-random-words.trycloudflare.com
```

**Save that URL.** Your server will use it to reach the phone.

Now stop the foreground command (Ctrl+C) and start it persistently in tmux:

```bash
export TUNNEL_URL="random-words-random-words.trycloudflare.com"
linkedin-proxy start
```

## Step 5 — Test from the server

On your **server**:

```bash
# Test SSH connectivity
ssh -o ConnectTimeout=5 -p 2222 -i ~/.ssh/phone_key phoneuser@localhost
# (should drop you into the phone's shell, then 'exit' to come back)

# Set up SOCKS via the phone
ssh -D 1080 -N -f -i ~/.ssh/phone_key -p 2222 phoneuser@localhost

# Verify IP
curl --proxy socks5://127.0.0.1:1080 https://api.ipify.org
# Should be your phone's mobile IP, not the EC2 datacenter IP
```

## Step 6 — Make it persistent (systemd on the server)

Create `/etc/systemd/system/phone-socks.service` on the server:

```ini
[Unit]
Description=SOCKS proxy to Android phone via cloudflared
After=network-online.target

[Service]
ExecStart=/usr/bin/ssh -D 1080 -N -i /home/admin/.ssh/phone_key -p 2222 phoneuser@localhost
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now phone-socks
sudo systemctl status phone-socks
```

## Long-term URL (no trycloudflare)

The `random-words.trycloudflare.com` URL **changes every time you restart
cloudflared**. For a stable URL, set up a named tunnel:

1. Sign up at https://one.dash.cloudflare.com/ (free)
2. Go to **Networks → Tunnels** → Create a tunnel
3. Install the connector on your phone:
   ```bash
   # In Termux
   cloudflared service install <your-tunnel-token>
   ```
4. Add a public hostname pointing to `ssh://localhost:8022`
5. Your URL is now stable, e.g. `phone.yourdomain.com`

## Battery and data considerations

- **Battery**: Termux + sshd + cloudflared uses ~3-5% battery per hour. Keep the phone plugged in for long sessions.
- **Data**: LinkedIn is lightweight (~1-5 MB per page load). A 1 GB mobile data plan covers ~200-1000 page views.
- **Sleep**: Android may put the phone to sleep and kill background apps. To prevent this:
  - Settings → Apps → Termux → Battery → "Unrestricted"
  - Disable battery optimization for Termux
  - On some phones: enable "Always-on display" or use a wakelock app

## Troubleshooting

| Issue | Solution |
|---|---|
| `ssh: connect to host localhost port 2222: Connection refused` | `linkedin-proxy start` on the phone first |
| `Permission denied (publickey)` | Make sure you pasted the **public** key, not private |
| Phone sleeps and tunnel drops | Disable battery optimization for Termux |
| Mobile data too slow | Use WiFi; LinkedIn + stealth is bandwidth-light but latency-sensitive |
| LinkedIn flags the IP anyway | Mobile IPs rotate — try toggling airplane mode to get a new one |
| `cloudflared` command not found | Re-run `termux_setup.sh` or install manually from [GitHub releases](https://github.com/cloudflare/cloudflared/releases) |
| `tmux: command not found` | `pkg install tmux` |

## What you get

After this setup, your phone becomes a drop-in proxy:

- **Battery on, plugged in** = reliable proxy
- **WiFi connected** = fast + cheap
- **Cloudflared tunnel** = no port forwarding, no router config
- **Key-only SSH** = secure

You can have BOTH a laptop proxy (Option 1/2) AND a phone proxy (Option 3)
running. linkedin-mcp-pro will use whichever one has lower latency.

## Advanced: keep Termux running in the background

Termux is a normal Android app, so it can be killed by the system. To keep
it running reliably:

```bash
# Install termux-wake-lock
pkg install termux-api
# Or use a wakelock script:
cat > ~/bin/wakelock.sh <<'EOF'
#!/data/data/com.termux/files/usr/bin/bash
while true; do
  termux-wake-lock
  sleep 60
done
EOF
chmod +x ~/bin/wakelock.sh
# Run in a separate tmux session
tmux new-session -d -s wakelock "~/bin/wakelock.sh"
```

And in Android settings:
- Apps → Termux → Battery → **Unrestricted**
- Apps → Termux → Notifications → **Allow all** (so you can wake it from the notification shade)
