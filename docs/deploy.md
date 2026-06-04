# Deploy

Production runs from one checkout on the server:

```bash
/opt/youtube_automation
```

The public UI is:

```text
https://and-remu.ourdocumwiki.live/sessions
```

The noVNC UI is:

```text
https://and-remu.ourdocumwiki.live/novnc/vnc.html
```

## Fast Path

From the local repository:

```bash
export YTA_SERVER_PASSWORD='...'
./ops/deploy-remote.sh
```

The script deploys the current branch. It:

1. Verifies tracked changes are committed.
2. Pushes the current branch to GitHub.
3. SSHes to the server.
4. Runs deploy from `/opt/youtube_automation`.
5. Verifies the server commit matches the local commit.
6. Checks `/api/ping` and Android services.

Useful overrides:

```bash
./ops/deploy-remote.sh --branch codex/sync-local-state-20260421
./ops/deploy-remote.sh --skip-push
./ops/deploy-remote.sh --check
./ops/deploy-remote.sh --dry-run
```

Default remote settings:

```bash
YTA_SERVER_HOST=195.123.219.89
YTA_SERVER_PORT=3333
YTA_SERVER_USER=root
YTA_SERVER_DIR=/opt/youtube_automation
YTA_PUBLIC_URL=https://and-remu.ourdocumwiki.live
```

## Manual Fallback

Use this if the wrapper is unavailable:

```bash
git push origin <branch>
ssh -p 3333 root@195.123.219.89
cd /opt/youtube_automation
git fetch origin <branch>
git checkout <branch>
git pull --ff-only origin <branch>
./ops/deploy.sh <branch>
```

Then verify:

```bash
curl -fsS https://and-remu.ourdocumwiki.live/api/ping
systemctl is-active yta-android-worker yta-appium yta-android-display
git rev-parse --short HEAD
```

There should not be an active duplicate checkout at `/opt/yta_sync_repo`.
