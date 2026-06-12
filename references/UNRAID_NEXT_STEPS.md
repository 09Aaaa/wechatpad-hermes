# Unraid Next Steps

Current release archive expected in the writable drop share:

```bash
/mnt/user/appdata/_wechatpad_hermes_drop/hermes-wechatpadpromax-YYYYMMDDHHMMSS.tar.gz
```

## Optional: Authorize Key-Based SSH

If Windows can write only to the drop share, authorize the public key from the Unraid terminal:

```bash
bash /mnt/user/appdata/_wechatpad_hermes_drop/authorize-unraid-ssh-key.example.sh
```

This script adds only the public key from the drop share to `/root/.ssh/authorized_keys`. It does not contain passwords, authcodes, API keys, or WeChat identifiers.

## Install The Dry-Run Release

Run from the Unraid terminal:

```bash
bash /mnt/user/appdata/_wechatpad_hermes_drop/install-wechatpad-hermes-YYYYMMDDHHMMSS.sh
```

The installer checks the archive SHA256, extracts into a timestamped release directory, installs a local Python venv, and keeps runtime data under:

```bash
/mnt/user/appdata/wechatpad-hermes/data
```

## Create Runtime Env

Create or edit:

```bash
/mnt/user/appdata/wechatpad-hermes/data/.env
```

Keep these dry-run safety values until live behavior is verified:

```bash
WECHATPAD_SEND_ENABLED=false
WECHATPAD_DRY_RUN=true
WECHATPAD_ADMIN_TOOLS_ENABLED=false
WECHATPAD_ALLOW_UNKNOWN_OUTBOUND=false
WECHATPAD_STORE_RAW_MESSAGES=false
```

## Verify Dry-Run

After `.env` exists, run from the installed release directory:

```bash
cd /mnt/user/appdata/wechatpad-hermes/releases/dryrun-YYYYMMDDHHMMSS/hermes-wechatpadpromax
WECHATPAD_HERMES_RELEASE=dryrun-YYYYMMDDHHMMSS bash references/verify-unraid-dryrun.example.sh
```

Do not enable real WeChat sending until the dry-run bridge, privacy blocking, and real Hermes endpoint behavior are verified.
