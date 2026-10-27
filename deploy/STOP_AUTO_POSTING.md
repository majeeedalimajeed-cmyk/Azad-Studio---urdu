# Stop AZAD STUDIO auto posting

Auto posting is controlled by GitHub Actions repository variables, not by source code. Stopping does not delete posts already published; it prevents new public posts.

## Immediate stop (recommended)

In the GitHub repository, open **Settings → Secrets and variables → Actions → Variables** and set:

- `NR_PUBLISH_ENABLED` = `false`
- `NR_AUTO_POST_ENABLED` = `false`
- `NR_PUBLISH_SAFETY_OK` = `false`
- `NR_DRY_RUN` = `true`

The next hourly workflow run will restore to dry run. Any currently running workflow finishes its current job; it cannot start a new public publish after the variables take effect. For an immediate hard stop, also disable the **Hourly newsroom scan** workflow in the Actions tab.

## Stop from a terminal

If you have a GitHub token with repository Variables write permission available locally, run:

```bash
cd /home/user/nr_news
export GITHUB_TOKEN='your-token-with-variables-write'   # or GH_TOKEN
python3 deploy/toggle_auto_posting.py --stop
```

The script reads `GITHUB_TOKEN`, `GH_TOKEN`, or the existing `~/.git-credentials` file and never prints the token. It can also show the current state:

```bash
python3 deploy/toggle_auto_posting.py --status
```

To resume after an intentional stop:

```bash
python3 deploy/toggle_auto_posting.py --start
```

`--start` sets all launch safety flags to the values required for live auto posting. Do not use it unless the operator-approved test post has succeeded and the correct AZAD STUDIO Page is still API-confirmed.

## Local dashboard / persistent host

The local dashboard and any self-hosted `nr_news.cli serve` process read environment variables at startup. To stop a self-hosted instance, set the same variables to false/true in its environment file and restart the service, or stop the service/container itself. GitHub Actions variables do not affect a separately deployed host.
