# Proxy Setup — Get Your Server Talking to LinkedIn via a Real IP

LinkedIn aggressively blocks datacenter IP ranges (AWS, GCP, DigitalOcean, etc.).
If your server runs in the cloud, you need a way to make LinkedIn requests look
like they're coming from a real residential or mobile connection.

This guide covers **5 options**, ordered from simplest to most powerful. Pick
the one that fits your setup.

---

## Quick decision tree

```
Do you have a laptop you can use as a proxy?
  ├─ Yes → Option 1 (SOCKS via SSH) or Option 2 (SOCKS via cloudflared)
  └─ No
      Do you have an Android phone?
        ├─ Yes → Option 3 (Termux phone proxy)
        └─ No
            Are you OK paying for a proxy service?
              ├─ Yes → Option 4 (residential proxy)
              └─ No
                  Is your server on a residential/home IP already?
                    ├─ Yes → no proxy needed
                    └─ No → Option 5 (WireGuard VPN to a home server)
```

---

## Option 1 — SOCKS via SSH (-D) ⭐ SIMPLEST

**Best for:** You have a laptop, want 5-minute setup, no extra software.

### How it works

```
EC2 ──ssh -D 1080──> laptop's sshd
                       │
                       └─> LinkedIn (from laptop's IP)
```

### Setup

**On your laptop** (one time):
```bash
# Make sure sshd is running (Ubuntu/Debian)
sudo apt install openssh-server
sudo systemctl enable --now sshd

# Find your laptop's local IP (for EC2 to connect back)
ip addr show | grep 'inet ' | grep -v 127.0.0.1
# e.g. 192.168.1.42

# Find your public IP
curl ifconfig.me
# e.g. 101.53.252.2 (PTCL)
```

**Configure EC2 security group** to allow inbound on port 22 from `0.0.0.0/0` (or at least your laptop's ISP range).

**On EC2** (every session, or via systemd):
```bash
ssh -D 1080 -N -f -i ~/.ssh/laptop_key alian@<laptop-public-ip>
# Test:
curl --proxy socks5://127.0.0.1:1080 https://api.ipify.org
# Should print your laptop's public IP
```

### Pros & cons

| ✅ Pros | ❌ Cons |
|---|---|
| 5-min setup, no extra software | Laptop must stay on |
| Works on any port-forwarded ssh | Port 22 must be open on laptop |
| No public tunnel needed | No automatic reconnect |

### Persistence with systemd

Create `/etc/systemd/system/laptop-socks.service`:
```ini
[Unit]
Description=SOCKS proxy to laptop
After=network-online.target

[Service]
ExecStart=/usr/bin/ssh -D 1080 -N -i /home/admin/.ssh/laptop_key alian@<laptop-public-ip>
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now laptop-socks
sudo systemctl status laptop-socks  # verify
```

---

## Option 2 — SOCKS via cloudflared tunnel ⭐ MOST RELIABLE

**Best for:** You want a persistent connection that auto-reconnects, even when your laptop moves between WiFi networks or goes to sleep.

### How it works

```
EC2 ──localhost:2222 (ssh client)──┐
                                    │  cloudflared tunnel
                                    │  (encrypted, NAT-traversing)
laptop's sshd :22 ◀────────────────┘
   │
   └─> LinkedIn (from laptop's IP)
```

### Setup

**On your laptop**:

```bash
# 1. Install cloudflared
# macOS: brew install cloudflared
# Linux: https://pkg.cloudflare.com/
# Windows: winget install Cloudflare.cloudflared

# 2. Make sure sshd is running
sudo systemctl enable --now sshd  # Linux
# or: brew services start openssh  # macOS

# 3. Start a one-shot cloudflared tunnel to your laptop's sshd
cloudflared tunnel --url ssh://localhost:22
# It prints a URL like: https://random-words.trycloudflare.com
# SAVE THIS URL — you'll need it on the server

# 4. Make the tunnel persistent (systemd service)
sudo tee /etc/systemd/system/cloudflared-laptop.service > /dev/null <<'EOF'
[Unit]
Description=Cloudflared tunnel from EC2 to laptop
After=network-online.target

[Service]
ExecStart=/usr/local/bin/cloudflared access ssh \
  --hostname random-words.trycloudflare.com \
  --listener localhost:2222
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now cloudflared-laptop
```

**On EC2**:

```bash
# 1. Test the tunnel
ssh -o ConnectTimeout=5 -p 2222 alian@localhost
# Should drop you into your laptop's shell

# 2. Set up a SOCKS proxy over the tunnel
ssh -D 1080 -N -f -i ~/.ssh/laptop_key -p 2222 alian@localhost

# 3. Or make IT persistent
sudo tee /etc/systemd/system/laptop-socks.service > /dev/null <<'EOF'
[Unit]
Description=SOCKS proxy to laptop (over cloudflared tunnel)
After=cloudflared-laptop.service
Requires=cloudflared-laptop.service

[Service]
ExecStart=/usr/bin/ssh -D 1080 -N -i /home/admin/.ssh/laptop_key -p 2222 alian@localhost
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now laptop-socks
```

### Pros & cons

| ✅ Pros | ❌ Cons |
|---|---|
| Auto-reconnects if connection drops | Slightly more setup than Option 1 |
| No port-forwarding on laptop | Needs cloudflared installed |
| Works behind any NAT | Free trycloudflare URLs change if you restart |
| Encrypted, fast | |

### Long-term URL (no trycloudflare)

For a stable URL, set up a [named cloudflared tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/):
```bash
cloudflared tunnel login
cloudflared tunnel create my-laptop
cloudflared tunnel route dns my-laptop laptop.yourdomain.com
# Now you can use https://laptop.yourdomain.com instead of random.trycloudflare.com
```

---

## Option 3 — Termux phone proxy 📱

**Best for:** You're mobile, want a backup proxy, or don't have a laptop handy.

See [`TERMUX_SETUP.md`](TERMUX_SETUP.md) for the full guide. Short version:

```bash
# On phone (in Termux)
bash scripts/termux_setup.sh
# Prints a public key

# On EC2, add the public key to ~/.ssh/authorized_keys
# Then:
linkedin-proxy start  # on phone
ssh -D 1080 -N -f -i ~/.ssh/phone_key -p 2222 phoneuser@localhost  # on EC2
```

**Note:** Mobile data may incur charges. Use WiFi when possible.

---

## Option 4 — Residential proxy service 💰

**Best for:** You don't have a personal device to use as a proxy.

Services like [Bright Data](https://brightdata.com/), [Smartproxy](https://smartproxy.com/), [IPRoyal](https://iproyal.com/), or [Webshare](https://www.webshare.io/) sell access to pools of residential IPs.

### Setup

Most services give you:
- A hostname (e.g. `proxy.example.com`)
- A port (e.g. `22225`)
- A username + password

**On EC2**:
```bash
# Set env vars (in ~/.bashrc or systemd unit)
export LINKEDIN_MCP_PROXY="socks5://user:pass@proxy.example.com:22225"
# or
export LINKEDIN_MCP_PROXY="http://user:pass@proxy.example.com:22225"

# Test
curl --proxy "$LINKEDIN_MCP_PROXY" https://api.ipify.org
```

### Pros & cons

| ✅ Pros | ❌ Cons |
|---|---|
| No personal device needed | Costs $5-50/month |
| Massive IP pool = hard to detect | IPs are shared with other users |
| Easy to set up | Some services get flagged by LinkedIn |
| | Bandwidth often metered |

### Recommended providers (2026)

| Provider | Price | Pool size | Notes |
|---|---|---|---|
| **Webshare** | Free tier (10 proxies) | Small | Good for testing |
| **IPRoyal** | ~$3/GB | 200K+ | Pay-as-you-go, ethical sourcing |
| **Smartproxy** | ~$12/GB | 40M+ | Premium, great success rate |
| **Bright Data** | ~$12/GB | 72M+ | Industry standard, expensive |
| **Decodo (formerly Smartproxy)** | ~$7/GB | 40M+ | Discount tier |

⚠️ **Avoid**: Any "free proxy" lists — they're almost always already flagged by LinkedIn.

---

## Option 5 — WireGuard VPN to a home server

**Best for:** You have a home server / NAS / Raspberry Pi with a residential IP, and you want the most professional setup.

### How it works

```
EC2 (WireGuard peer)  ◀──encrypted tunnel──▶  Home server (WireGuard peer)
   │                                                  │
   10.0.0.2/24 ───────────────────────────────────── 10.0.0.1/24
                                                      │
                                                      └─> LinkedIn (from home IP)
```

### Setup

**On home server** (one time):
```bash
sudo apt install wireguard
wg genkey | tee /home/server.key | wg pubkey > /home/server.pub

sudo tee /etc/wireguard/wg0.conf > /dev/null <<EOF
[Interface]
Address = 10.0.0.1/24
ListenPort = 51820
PrivateKey = $(cat /home/server.key)
PostUp = iptables -A FORWARD -i wg0 -j ACCEPT; iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
PostDown = iptables -D FORWARD -i wg0 -j ACCEPT; iptables -t nat -D POSTROUTING -o eth0 -j MASQUERADE

[Peer]
PublicKey = <EC2_PUBLIC_KEY>
AllowedIPs = 10.0.0.2/32
EOF
sudo systemctl enable --now wg-quick@wg0
```

**On EC2**:
```bash
sudo apt install wireguard
wg genkey | tee /home/ec2.key | wg pubkey > /home/ec2.pub

sudo tee /etc/wireguard/wg0.conf > /dev/null <<EOF
[Interface]
Address = 10.0.0.2/24
PrivateKey = $(cat /home/ec2.key)

[Peer]
PublicKey = <HOME_SERVER_PUBLIC_KEY>
Endpoint = <HOME_SERVER_PUBLIC_IP>:51820
AllowedIPs = 10.0.0.0/24
PersistentKeepalive = 25
EOF
sudo systemctl enable --now wg-quick@wg0

# Now route LinkedIn traffic through the VPN
sudo ip route add 104.18.0.0/16 dev wg0  # LinkedIn Cloudflare range
# Or just route everything:
sudo ip route replace default dev wg0
```

### Pros & cons

| ✅ Pros | ❌ Cons |
|---|---|
| Fastest (lowest overhead) | Most complex setup |
| Always-on, no laptop dependency | Need a home server |
| Full Layer 3 networking | Need port 51820 open on home |
| Easy to share with other devices | |

---

## Choosing `LINKEDIN_MCP_PROXY` for linkedin-mcp-pro

Once you have any of the above set up, point linkedin-mcp-pro at it:

```bash
# Option 1 or 2 (SOCKS via SSH)
export LINKEDIN_MCP_PROXY="socks5://127.0.0.1:1080"

# Option 3 (Termux)
export LINKEDIN_MCP_PROXY="socks5://127.0.0.1:1080"  # same — EC2 tunnels to phone

# Option 4 (residential)
export LINKEDIN_MCP_PROXY="socks5://user:pass@proxy.example.com:22225"

# Option 5 (WireGuard) — no env var needed, traffic is already routed
unset LINKEDIN_MCP_PROXY
```

The scripts (`bootstrap_session.sh`, `post_with_stealth.py`, `use_profile_session.py`) all read this env var.

---

## Testing your setup

Whichever option you pick, verify it works:

```bash
# 1. What IP does LinkedIn see?
curl --proxy "$LINKEDIN_MCP_PROXY" https://api.ipify.org
# Should be a residential IP, not a datacenter IP

# 2. Can you reach LinkedIn at all?
curl --proxy "$LINKEDIN_MCP_PROXY" -I https://www.linkedin.com
# Should return 200 OK (or 303 redirect to login)

# 3. End-to-end: does the browser session work?
python3 scripts/post_with_stealth.py --check
# Should print "session health: OK"
```

If any of these fail, the issue is the proxy — not linkedin-mcp-pro. Fix the proxy first.

---

## Security notes

- **Never share your proxy endpoint publicly** — it's a direct line to your home network
- **Use SSH keys, not passwords** — see `termux_setup.sh` for keygen
- **Restrict port forwarding** — only forward what you need
- **Monitor traffic** — unusual spikes could mean someone is piggy-backing
- **Rotate IPs** — if using mobile, you'll get a new IP every time you reconnect; for residential proxies, rotate via the service's API

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Connection refused` to proxy | SOCKS not running | Restart `laptop-socks.service` or cloudflared |
| `407 Proxy Authentication Required` | Wrong credentials | Check `user:pass@` in proxy URL |
| `SOCKS5 error: host unreachable` | Tunnel down | Check `cloudflared-laptop.service` status |
| LinkedIn returns 999 | IP flagged | Switch proxy or wait 24h |
| `curl` works but browser doesn't | Browser ignoring `ALL_PROXY` | Set `LINKEDIN_MCP_PROXY` explicitly (handled by scripts) |
