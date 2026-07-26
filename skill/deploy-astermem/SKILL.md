---
name: deploy-astermem
description: Guide the user through deploying AsterMem to a remote server and binding a domain with HTTPS. Collect server address, SSH credentials, and domain name; remotely install Docker, bring up the service, configure Caddy reverse proxy, and harden security post-launch. Use when the user says "deploy to server", "go live", "bind a domain", "public access", "deploy AsterMem", or asks how to run this project on a VPS / cloud server.
---

# Deploy AsterMem to a Remote Server

Target state: Run AsterMem via Docker Compose on the server (fixed port 8768 inside the container, listening on localhost only), with Caddy as a reverse proxy that auto-issues HTTPS certificates. Users access the service via `https://<domain>`. All data is persisted in the server's `data/` directory.

The entire process is executed by you (the agent) remotely via SSH — do not throw commands at the user to run themselves. Check the output of each step before proceeding to the next.

## Step 0: Gather Information

Ask the user for the following information (ask only for what's missing, don't re-ask everything):

1. **Server address**: Public IP, SSH port (default 22)
2. **SSH account**: Username (usually root) + password, or a pre-configured key
3. **Domain**: e.g. `mem.example.com`. If the user has no domain, use the "IP + port" approach (see below) — no DNS, ICP filing, or ports 80/443 needed
4. **Server region**: For servers in mainland China, remind the user that binding a domain requires ICP filing (the IP direct-connect approach doesn't); Docker Hub and GitHub may need mirror acceleration
5. **Model API Key** (optional): Any of Bailian / OpenRouter / Google AI / Asterove, for semantic search. Not required to start — can be configured later in the Web UI

## Step 1: DNS Resolution

Have the user add an A record at their domain registrar: `mem.example.com → server IP`. Then verify locally — do not start installing Caddy until DNS has propagated (certificate issuance will fail):

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

**Security critical**: When using the domain + reverse proxy approach, change the port mapping in `docker-compose.yml` to listen on localhost only, to avoid exposing port 8768 directly to the internet:

```yaml
    ports:
      - "127.0.0.1:8768:8768"
```

Start and verify:

```bash
docker compose up -d --build
curl -fsS http://127.0.0.1:8768/api/auth/check && echo OK
```

## Step 5: Caddy Reverse Proxy + HTTPS

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

**No-domain approach (IP + port direct)**: Skip Steps 1 and 5, keep the compose port as `"8768:8768"`, allow 8768 in the security group. No need for 80/443 or ICP filing. Users access via `http://<IP>:8768`. Clearly inform the user this is plaintext HTTP — a strong password is essential.

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
- [ ] Port 8768 is not directly exposed to the internet (domain approach)
- [ ] docker compose ps shows container healthy
- [ ] (Optional) Provider test passed, semantic search is working
```

## Routine Operations (inform the user)

- **Backup**: All state is in `/opt/astermem/data/` — backup = copy this directory
- **Update**: `cd /opt/astermem && git pull && docker compose up -d --build`
- **Forgot password**: `docker compose exec astermem python server.py --reset-admin` (resets to admin/admin without touching data)
- **View logs**: `docker compose logs -f --tail 100`
