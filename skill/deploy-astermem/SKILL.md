---
name: deploy-astermem
description: Guide the user through taking AsterMem live — on a cloud server, or on a machine they already own (home NAS, Raspberry Pi, this computer) exposed through Cloudflare Tunnel with no public IP. Ask how they want to deploy first, then install Docker, bring up the service, expose it with HTTPS (Cloudflare Tunnel or Caddy), and harden security post-launch. Use when the user says "deploy to server", "go live", "bind a domain", "public access", "deploy AsterMem", "expose it with a tunnel", or asks how to run this project on a VPS / cloud server (AWS, Alibaba Cloud, Cloudflare, Vercel, etc.) or a home machine without a public IP.
---

# Deploy AsterMem

Target state: AsterMem runs via Docker Compose (fixed port 8768, listening on localhost only) on a machine the user chooses, exposed to the internet with HTTPS — either through Cloudflare Tunnel or a Caddy reverse proxy. All data is persisted in that machine's `data/` directory.

The process is executed by you (the agent) — locally or over SSH, do not throw commands at the user to run themselves. Check the output of each step before proceeding to the next. **But do not touch anything before Step 0: ask the user what they want first.**

## Step 0: Ask What the User Wants (before doing anything)

Ask up front (use AskQuestion if available; ask only for what's missing):

1. **Where should it run?**
   - **Path A — Cloud server / VPS**: the user has one or is willing to rent one. Full flow: Steps 1–6
   - **Path B — A machine the user already owns**: this computer, a home NAS, a Raspberry Pi, a spare PC. No public IP, no port forwarding, no server rental — exposed through Cloudflare Tunnel for free. Flow: "Path B" section below, then Step 6
   - **Path C — LAN only**: no public access needed. Just run Step 4 on the machine and stop; remind the user to change the default password
2. **Domain**: does the user have one, and **is it managed on Cloudflare**? Path B requires a domain on Cloudflare (free plan; moving nameservers takes minutes, done once). Path A works with any domain (Caddy route) or none (IP + port)
3. **Region**: for mainland China servers, a bound domain requires ICP filing (Path B and the IP direct route don't involve filing); Docker Hub and GitHub may need mirror acceleration
4. **Model API Key** (optional): any of Bailian / OpenRouter / Google AI / Asterove, for semantic search. Not required to start — can be configured later in the Web UI

## Choosing a Platform (Path A, if the user has no server yet)

AsterMem is a stateful long-running process (SQLite + on-disk vector index, all data in `data/`), so it needs a real machine — before recommending a rental, remind the user that Path B (a machine they already own + Cloudflare Tunnel) costs nothing:

- **AWS**: EC2, or Lightsail for a cheaper and simpler fixed-price VPS. Launch an Ubuntu instance and the workflow below applies as-is
- **Alibaba Cloud**: ECS or Simple Application Server. Mainland regions require ICP filing to bind a domain; Hong Kong / Singapore regions do not
- **Cloudflare**: Not for hosting — but recommended in front of the server for DNS + free HTTPS + hiding the origin IP. Cloudflare Tunnel replaces the reverse proxy entirely and is the preferred exposure route (Step 5, Option A)
- **Vercel**: ❌ Not suitable. Serverless platforms have no persistent disk and cannot run a long-lived process, so AsterMem cannot run there

## Path B: No Server — a Machine You Already Own + Cloudflare Tunnel

`cloudflared` opens an outbound-only tunnel to Cloudflare's edge, so the machine needs no public IP, no router port forwarding, and no inbound firewall rules. Free plan is enough. Trade-off to state clearly: availability is tied to that machine and its network — it must stay powered on and online.

1. **Reach the machine**: if it is the computer you are running on, work locally. If it is a NAS / Raspberry Pi on the LAN, connect over SSH first (Step 2 applies unchanged)
2. **Run AsterMem**: if it is already running (e.g. via `./start.sh`), reuse it — read the port from `config.yaml`. Otherwise start it with Docker (Step 4; keep the `127.0.0.1:8768:8768` mapping)
3. **Install cloudflared**:
   - macOS: `brew install cloudflared`
   - Debian/Ubuntu (incl. Raspberry Pi OS): use the apt source from Step 5 Option A
   - NAS with Docker only: run the `cloudflare/cloudflared` container with the dashboard token instead
4. **Create the tunnel**: exactly the commands in Step 5 Option A, pointing the ingress `service` at the actual local port. On macOS, `cloudflared service install` registers a launchd service; on Linux, systemd
5. **Keep it alive**: the service starts on boot on both platforms. On a laptop/desktop, disable system sleep (macOS: `sudo pmset -a sleep 0`, or System Settings → prevent sleep when on power) — a sleeping machine means an offline memory service
6. Verify `https://<domain>/api/auth/check`, then go straight to Step 6 (hardening is mandatory — the service is now public)

## Step 1: DNS Resolution (Path A)

**If going the Cloudflare Tunnel route (Step 5, Option A)**: no A record is needed — the tunnel creates the DNS record itself. Just confirm the domain's nameservers point to Cloudflare (`dig NS example.com`), then skip ahead.

**Otherwise (Caddy route)**: have the user add an A record at their domain registrar: `mem.example.com → server IP`. Then verify locally — do not start installing Caddy until DNS has propagated (certificate issuance will fail):

```bash
dig +short mem.example.com   # should output the server IP
```

## Step 2: Establish SSH Connection

When using password login, install the local public key on the server first for passwordless access going forward:

```bash
ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519 2>/dev/null || true   # skip if exists
ssh-copy-id -p <port> <user>@<server_ip>   # enter password once interactively
```

If `ssh-copy-id` cannot run interactively, fall back to sshpass (macOS: `brew install sshpass`). Pass the password via environment variable — **never write plaintext passwords into scripts, files, or commit history**:

```bash
sshpass -p "$SSH_PASS" ssh -o StrictHostKeyChecking=accept-new -p <port> <user>@<server_ip> 'echo ok'
```

Once connected, probe the environment:

```bash
ssh <user>@<server_ip> 'cat /etc/os-release; docker --version; docker compose version'
```

## Step 3: Install Docker (if missing)

```bash
curl -fsSL https://get.docker.com | sh
systemctl enable --now docker
```

For mainland China servers, if image pulls time out, configure mirror acceleration in `/etc/docker/daemon.json` then `systemctl restart docker`.

## Step 4: Upload Code and Start

```bash
git clone https://github.com/Asterove/AsterMem.git /opt/astermem
cd /opt/astermem
```

If the server has difficulty accessing GitHub, pack and upload from the local machine instead:

```bash
# Run on local machine (exclude local data and dependencies)
tar czf /tmp/astermem.tgz --exclude=data --exclude=venv --exclude=web-ui/node_modules --exclude=.git .
scp /tmp/astermem.tgz <user>@<server_ip>:/tmp/
ssh <user>@<server_ip> 'mkdir -p /opt/astermem && tar xzf /tmp/astermem.tgz -C /opt/astermem'
```

If you have a model API key, write it to `.env` (refer to `.env.example`; compose picks it up automatically).

**Security critical**: When using a domain route (Step 5, Option A or B), change the port mapping in `docker-compose.yml` to listen on localhost only, to avoid exposing port 8768 directly to the internet:

```yaml
    ports:
      - "127.0.0.1:8768:8768"
```

Start and verify:

```bash
docker compose up -d --build
curl -fsS http://127.0.0.1:8768/api/auth/check && echo OK
```

## Step 5: Expose to the Internet — pick ONE route

| Route | Pick when | Inbound ports |
|-------|-----------|---------------|
| **A. Cloudflare Tunnel** (preferred) | Domain is on Cloudflare, or machine is behind NAT / has no public IP | None |
| **B. Caddy reverse proxy** | Domain is at another registrar and user won't move it | 80, 443 |
| **C. IP + port direct** | No domain at all | 8768 |

### Option A: Cloudflare Tunnel (preferred)

Zero inbound ports (not even 80/443), HTTPS terminated at Cloudflare's edge, origin IP hidden. Prerequisite: the domain's nameservers are on Cloudflare (free plan is enough; moving nameservers takes minutes and the user only does it once).

Install `cloudflared` on the server:

```bash
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg -o /usr/share/keyrings/cloudflare-main.gpg
echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main" > /etc/apt/sources.list.d/cloudflared.list
apt-get update && apt-get install -y cloudflared
```

Authorize and create the tunnel:

```bash
cloudflared tunnel login          # prints a URL — send it to the user to open in their browser and pick the zone
cloudflared tunnel create astermem
cloudflared tunnel route dns astermem mem.example.com   # creates the DNS record automatically
```

Write `/etc/cloudflared/config.yml` (get `<UUID>` from `cloudflared tunnel list`):

```yaml
tunnel: <UUID>
credentials-file: /root/.cloudflared/<UUID>.json
ingress:
  - hostname: mem.example.com
    service: http://127.0.0.1:8768
  - service: http_status:404
```

Install as a service and verify:

```bash
cloudflared service install
systemctl enable --now cloudflared
curl -fsS https://mem.example.com/api/auth/check && echo OK
```

If the user prefers clicking over CLI, the equivalent is Cloudflare dashboard → Zero Trust → Networks → Tunnels → Create tunnel; it hands them a one-line `cloudflared service install <token>` command to run on the server, and the hostname → `http://localhost:8768` mapping is configured in the dashboard.

Notes: keep the compose port on `127.0.0.1:8768:8768`; no firewall changes needed (the tunnel is outbound-only — you can even close all inbound ports except SSH). Known limit: Cloudflare's free plan caps uploads at 100 MB per request, which can affect large zip imports.

### Option B: Caddy Reverse Proxy

Install Caddy on Debian/Ubuntu (official apt source; for other distros see caddyserver.com/docs/install), then write `/etc/caddy/Caddyfile`:

```
mem.example.com {
    reverse_proxy 127.0.0.1:8768
}
```

```bash
systemctl reload caddy
```

Caddy will automatically issue and renew Let's Encrypt certificates, provided ports 80/443 are reachable and DNS has resolved (domains on mainland China servers must have ICP filing).

**Firewall / cloud security group**: Allow 80, 443 (and the SSH port); **do not** allow 8768. Cloud servers typically also need security group rules in the console — server-side ufw/firewalld alone is not enough.

### Option C: IP + Port Direct (no domain)

Skip Steps 1 and 5, keep the compose port as `"8768:8768"`, allow 8768 in the security group. No need for 80/443 or ICP filing. Users access via `http://<IP>:8768`. Clearly inform the user this is plaintext HTTP — a strong password is essential.

## Step 6: Post-launch Security Hardening (mandatory)

1. Visit `https://mem.example.com`, log in with the default account **admin / admin**
2. **Immediately guide the user to change the username and password in Admin → Sign-in** — the service is exposed to the internet, this step cannot be skipped
3. Confirm the "Require a username and password" toggle is enabled
4. Configure the embedding provider in Settings (if an API key was collected in Step 0, configure and test it for the user)
5. If the user's AI agent needs access: Create a token in Admin → API Tokens, then write to `~/.astermem/credentials` on the local machine:

```
ASTERMEM_BASE_URL=https://mem.example.com
ASTERMEM_TOKEN=ast_xxxxxxxx
```

## Completion Checklist

```
- [ ] https://<domain> opens and certificate is valid (or http is accessible under IP approach)
- [ ] Default password has been changed, login toggle is enabled
- [ ] Port 8768 is not directly exposed to the internet (domain routes)
- [ ] Tunnel routes: cloudflared service is active and survives reboot; Path B: machine will not sleep
- [ ] docker compose ps shows container healthy (Docker deployments)
- [ ] (Optional) Provider test passed, semantic search is working
```

## Routine Operations (inform the user)

- **Backup**: All state is in the project's `data/` directory — backup = copy this directory
- **Update**: `git pull && docker compose up -d --build` in the project directory
- **Forgot password**: `docker compose exec astermem python server.py --reset-admin` (resets to admin/admin without touching data)
- **View logs**: `docker compose logs -f --tail 100`
