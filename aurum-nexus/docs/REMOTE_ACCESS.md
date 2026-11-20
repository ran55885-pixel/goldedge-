# Remote access (phone / outside your home)

**Never use router port forwarding.** It exposes the trading hub to the whole internet.

## On the same Wi-Fi (LAN)
The hub listens on `0.0.0.0:8787`. Scan the QR shown in the console / PHONE button / tray menu, log in.

## From anywhere: Tailscale (recommended, private network)
1. Install Tailscale on the trading PC/VPS and on your phone, log in with the same account.
2. Open `http://<pc-name>.<tailnet>.ts.net:8787` (or the 100.x.y.z address) on the phone.
3. Optional HTTPS: `tailscale serve --bg 8787` gives `https://<pc-name>.<tailnet>.ts.net/`.

## From anywhere: Cloudflare Tunnel (HTTPS, public hostname)
1. Install `cloudflared`, `cloudflared tunnel login`, `cloudflared tunnel create aurum`.
2. Route a hostname you own: `cloudflared tunnel route dns aurum nexus.example.com`.
3. Run: `cloudflared tunnel run --url http://localhost:8787 aurum`.
4. Strongly recommended: put **Cloudflare Access** (email one-time PIN) in front of the hostname in addition to
   the dashboard login.

Session cookies are marked `Secure` automatically when the dashboard is reached over HTTPS.
Pause / Resume / Kill / Apply always require a logged-in session.
