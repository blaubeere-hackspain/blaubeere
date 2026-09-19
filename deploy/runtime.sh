#!/usr/bin/env bash
# Runs inside the dedicated Jio VM, supplied by deploy/jio.sh.
set -Eeuo pipefail
revision="${1:?Commit SHA required}"
app="${2:?App origin required}"
landing="${3:?Landing origin required}"
root=/var/lib/blaubeere
sudo -n install -d -m 755 -o "$(id -un)" -g "$(id -gn)" "$root"
exec 9>"$root/deploy.lock"
flock -n 9 || { echo 'Another deployment is running' >&2; exit 1; }

if ! command -v cc >/dev/null || ! command -v unzip >/dev/null || [[ ! -x /usr/sbin/nginx ]] || ! dpkg-query -W libssl-dev >/dev/null 2>&1; then
  sudo -n apt-get update -qq
  sudo -n env DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=180 install -y --no-install-recommends build-essential pkg-config libssl-dev nginx unzip ca-certificates
fi
node -e 'if (Number(process.versions.node.split(".")[0]) < 22) process.exit(1)'
node_bin="$(command -v node)"
export CARGO_HOME="$root/cargo" RUSTUP_HOME="$root/rustup" CARGO_TARGET_DIR="$root/target" BUN_INSTALL="$root/bun"
export PATH="$BUN_INSTALL/bin:$CARGO_HOME/bin:$PATH"
if [[ ! -x "$CARGO_HOME/bin/rustup" ]]; then
  curl --proto '=https' --tlsv1.2 -fsSL https://sh.rustup.rs -o "$root/rust-install.sh"
  sh "$root/rust-install.sh" -y --no-modify-path --profile minimal --default-toolchain 1.94.0
fi
rustup toolchain install 1.94.0 --profile minimal
export RUSTUP_TOOLCHAIN=1.94.0
if [[ ! -x "$BUN_INSTALL/bin/bun" ]] || [[ "$("$BUN_INSTALL/bin/bun" --version)" != 1.3.12 ]]; then
  curl -fsSL https://bun.com/install -o "$root/bun-install.sh"
  bash "$root/bun-install.sh" bun-v1.3.12
fi

sudo -n id blaubeere >/dev/null 2>&1 || sudo -n useradd --system --home-dir "$root/data" --shell /usr/sbin/nologin blaubeere
sudo -n install -d -m 700 -o blaubeere -g blaubeere "$root/data"
if [[ ! -f "$root/bootstrap.env" ]]; then
  (umask 077; printf 'BOOTSTRAP_EMAIL=finance@blaubeere.local\nBOOTSTRAP_PASSWORD=%s\nBOOTSTRAP_COMPANIES=DEMO_001\n' "$(openssl rand -hex 24)" > "$root/bootstrap.env")
  sudo -n chown root:blaubeere "$root/bootstrap.env"
  sudo -n chmod 640 "$root/bootstrap.env"
fi

mkdir -p "$root/repo" "$root/releases"
if [[ ! -d "$root/repo/.git" ]]; then
  git -C "$root/repo" init -q
  git -C "$root/repo" remote add origin https://github.com/blaubeere-hackspain/blaubeere.git
fi
git -C "$root/repo" -c protocol.version=2 fetch --depth=1 --filter=blob:none origin "$revision"
[[ "$(git -C "$root/repo" rev-parse FETCH_HEAD)" == "$revision" ]]
# Export only runtime sources. Challenge datasets and local secrets never enter releases.
release="$(mktemp -d "$root/releases/$revision.XXXXXX")"
chmod 755 "$release"
git -C "$root/repo" archive "$revision" Cargo.toml Cargo.lock package.json bun.lock apps services fixtures scripts | tar -x -C "$release"
cd "$release"
export NEXT_TELEMETRY_DISABLED=1 API_INTERNAL_URL=http://127.0.0.1:4000
export NEXT_PUBLIC_APP_URL="$app" NEXT_PUBLIC_LANDING_URL="$landing"
bun install --frozen-lockfile
bun run test:web
bun run typecheck
bun run build
cargo test --locked --release --workspace
cargo build --locked --release --workspace
mkdir bin
install -m 755 "$CARGO_TARGET_DIR/release/blaubeere-api" "$CARGO_TARGET_DIR/release/blaubeere-mcp" bin/

cat > "$root/runtime.env" <<EOF
DATABASE_URL=sqlite://$root/data/blaubeere.db
API_BIND=127.0.0.1:4000
MCP_BIND=127.0.0.1:4001
API_INTERNAL_URL=http://127.0.0.1:4000
APP_ORIGIN=$app
API_ORIGIN=$app
MCP_RESOURCE=$app/mcp
NEXT_PUBLIC_APP_URL=$app
NEXT_PUBLIC_LANDING_URL=$landing
NODE_ENV=production
NEXT_TELEMETRY_DISABLED=1
EOF
chmod 644 "$root/runtime.env"
for name in api mcp app landing; do
  directory="$root/data"
  command="$root/current/bin/blaubeere-$name"
  extra_env=""
  case "$name" in
    api) extra_env="EnvironmentFile=$root/bootstrap.env" ;;
    app|landing)
      directory="$root/current/apps/$name"
      port=3100; [[ "$name" != landing ]] || port=3102
      command="$node_bin $directory/node_modules/next/dist/bin/next start --hostname 127.0.0.1 --port $port"
      ;;
  esac
  sudo -n tee "/etc/systemd/system/blaubeere-$name.service" >/dev/null <<EOF
[Unit]
After=network-online.target
Wants=network-online.target
[Service]
User=blaubeere
Group=blaubeere
WorkingDirectory=$directory
EnvironmentFile=$root/runtime.env
$extra_env
ExecStart=$command
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
UMask=0077
[Install]
WantedBy=multi-user.target
EOF
done

sudo -n tee /etc/nginx/conf.d/blaubeere.conf >/dev/null <<'NGINX'
log_format blaubeere '$remote_addr $request_method $uri $status';
server {
  listen 127.0.0.1:8080;
  server_name _;
  access_log /var/log/nginx/blaubeere.log blaubeere;
  proxy_http_version 1.1;
  proxy_set_header Host $host;
  proxy_set_header X-Forwarded-Host $host;
  proxy_set_header X-Forwarded-Proto https;
  proxy_set_header Connection "";
  proxy_buffering off;
  proxy_read_timeout 300s;
  location = /mcp { proxy_pass http://127.0.0.1:4001; }
  location = /.well-known/oauth-protected-resource { proxy_pass http://127.0.0.1:4001; }
  location = /.well-known/oauth-protected-resource/mcp { proxy_pass http://127.0.0.1:4001; }
  location /oauth/ { proxy_pass http://127.0.0.1:4000; }
  location /.well-known/ { proxy_pass http://127.0.0.1:4000; }
  location /api/ { proxy_pass http://127.0.0.1:4000; }
  location / { proxy_pass http://127.0.0.1:3100; }
}
NGINX
sudo -n nginx -t
sudo -n systemctl daemon-reload
sudo -n systemctl enable blaubeere-api blaubeere-mcp blaubeere-app blaubeere-landing nginx

previous="$(readlink "$root/current" || true)"
activate() {
  ln -sfn "$1" "$root/next" &&
  mv -Tf "$root/next" "$root/current" &&
  sudo -n systemctl restart blaubeere-api blaubeere-mcp blaubeere-app blaubeere-landing &&
  sudo -n systemctl reload-or-restart nginx
}
healthy() {
  for url in http://127.0.0.1:4000/health http://127.0.0.1:4001/health http://127.0.0.1:8080/login http://127.0.0.1:3102/; do
    curl --fail --silent --show-error --retry 20 --retry-connrefused --retry-all-errors --retry-delay 1 --max-time 5 "$url" >/dev/null || return 1
  done
}
if ! activate "$release" || ! healthy; then
  sudo -n journalctl -u 'blaubeere-*' --no-pager -n 80 >&2
  if [[ -n "$previous" ]]; then activate "$previous"; fi
  echo 'New release failed its health checks' >&2
  exit 1
fi
printf '%s\n' "$revision" > "$root/deployed-revision"
# ponytail: retain releases for manual rollback; prune old releases when disk use warrants it.
echo "Production services are healthy at $revision"
