# Season operations

This runbook covers the supported local setup, annual data rollover, test-run cleanup, production startup, monitoring, and rollback. Run commands from the repository root unless a step says otherwise.

## Runtime and credentials

Use CPython 3.13.15 in `.venv`. Confirm that `python3.13 --version` reports `Python 3.13.15`, then install only from `requirements.txt`:

```sh
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m unittest discover --start-directory tests --verbose
```

Run production from a local checkout that is not inside Google Drive, Dropbox, iCloud Drive, or another synchronized folder. An ignored file can still be copied by a cloud-sync client, and synchronized runtime state can be modified by another machine.

Keep credentials outside the checkout in an existing absolute-path directory that only the deployment user can access. One-time setup:

```sh
install -d -m 700 "$HOME/.config/surrender-index"
install -m 600 credentials.example.json "$HOME/.config/surrender-index/credentials.json"
export SURRENDER_INDEX_CONFIG_DIR="$HOME/.config/surrender-index"
```

Replace the placeholders in the installed `credentials.json`. Put `gmail_credentials.json` in that same directory and run `chmod 600` on it if Gmail OAuth is needed. The bot writes `gmail_token.pickle` there with mode `600`. `SURRENDER_INDEX_CONFIG_DIR` is optional only for backward compatibility; without it, the code falls back to files in the working directory, which is not the supported production setup. These files must never be committed or cloud-synced:

- `credentials.json` contains the X credentials used by all three clients, the login values used by optional browser flows, `gmail_email`, and any Twilio values used for notifications.
- `gmail_credentials.json` is the Google OAuth client file used for Gmail notifications outside the native macOS Mail path.
- `gmail_token.pickle` is created after the Gmail OAuth flow succeeds.

The current code initializes all three X API clients at startup, even though the main and cancel account workflows are disabled by default. Consequently, `credentials.json` must include the main, `90_`, and `cancel_` X client keys for every run. Browser-login and Twilio values are only exercised when their corresponding flags are enabled.

The X key suffixes are `bearer_token`, `consumer_key`, `consumer_secret`, `access_token`, and `access_token_secret`. Use each suffix without a prefix for the main client, with `90_` for the percentile client, and with `cancel_` for the cancel client. The optional main browser path uses `email`, `username`, and `password`; the optional cancel browser path uses the corresponding `cancel_` names. Notification configuration uses `gmail_email`, and, when Twilio is selected, `twilio_account_sid`, `twilio_auth_token`, `from_phone_number`, and `to_phone_number`.

On macOS, the default notification path uses the Mail app. The first run may require Automation permission for the terminal or service launching the bot. On other platforms, the default path uses Gmail OAuth. `--notifyUsingTwilio` selects Twilio instead, and `--disableNotifications` suppresses heartbeat and error notifications.

Google Chrome must be installed. ChromeDriver setup currently occurs at runtime even when neither optional browser-posting workflow is enabled.

## Annual rollover checklist

Do this only after nflverse has finalized the previous regular season and postseason data. The production bot should remain stopped throughout the rollover.

1. Create the new season branch from an up-to-date `master` and record the currently deployed commit with `git rev-parse HEAD`.
2. Append the finalized season with the reproducible command-line builder. When `--data` is omitted, it always downloads a fresh nflverse file into the ignored `.cache` directory; it never trusts a previous cached download as finalized. Use `--data` only for a separately verified, finalized local file. For example, after the 2026 season is finalized, run:

   ```sh
   python scripts/append_historical_season.py \
     --season 2026 \
     --base 1999-2025_surrender_indices.npy \
     --output 1999-2026_surrender_indices.npy
   ```

   `Historical Surrender Indices.ipynb` remains available for exploration and full historical rebuilds.
3. Advance `CURRENT_SEASON` in `surrender_index_bot.py`. The historical filename and both public season labels are derived from that constant. Update the notebook's terminal year and output filename as part of the same rollover.
4. Review the builder output for missing input seasons, calculation errors, implausible punt counts, and non-finite values before replacing the tracked baseline. After independently confirming the new total, update `EXPECTED_INDEX_COUNT` and the expected public year in the offline tests. The tests also validate dtype, shape, and finite positive values.
5. Do not start the live bot during preseason. The live feed intentionally is not filtered by season type.
6. Before the first regular-season run, stop every bot process and make sure `current_surrender_indices.npy` is absent. This file is rebuilt from regular-season punts and must not carry the previous season or a test run into the new season.
7. Make sure `tweeted_plays.json` is absent or older than 12 hours. At startup, the bot automatically clears that file when it is at least 12 hours old.
8. Complete the preflight checks below, merge the season branch, and deploy the exact merged commit. Record the previous commit for comparison, but do not treat the pre-2026 commit as runnable rollback: it has 2025 labels, the 1999–2024 baseline, and an obsolete dependency manifest. For this initial migration, failure means stop and hold the bot until a corrected 2026-compatible commit passes preflight. Later in-season releases may use a proven 2026-compatible commit for rollback.

The historical `.npy` baseline is a curated build artifact and remains tracked. Raw nflverse CSV downloads, notebook checkpoints, credentials, and runtime state remain local and are ignored.

## Command-line flags and defaults

| Flag | Effect |
| --- | --- |
| no flags | Enables notifications and X posting for `@surrender_idx90` punts at or above the current-season 90th percentile. The main and cancel accounts stay disabled. |
| `--disableTweeting` | Suppresses X posts, but still initializes X clients, calculates and writes `current_surrender_indices.npy`, and marks detected drives in `tweeted_plays.json`. This is not a read-only mode. |
| `--disableNotifications` | Suppresses the startup heartbeat and error notifications. |
| `--notifyUsingTwilio` | Uses Twilio instead of native macOS Mail or Gmail for notifications. |
| `--enableMainAccount` | Enables `@surrender_index` posting. Its ordinary post path uses browser automation; delay-of-game reply threads still use the X API. This is intentionally off by default. |
| `--enableCancel` | Enables the cancel-poll workflow after a `@surrender_idx90` post. This is intentionally off by default. |
| `--disableTweepyReply` | When cancel mode is enabled, creates its poll with browser automation instead of Tweepy. |
| `--debug` | Prints detailed calculation inputs and multipliers and prevents headless browser operation. |
| `--notHeadless` | Prevents headless browser operation without enabling calculation diagnostics. |
| `--disableFinalCheck` | Accepted for compatibility but currently has no effect. |

The default main/cancel choices are operational policy, not missing setup. Re-enabling or redesigning either workflow should be handled as a separate change with account-specific testing.

## Preflight and no-post validation

Before the first live window:

1. Verify that the deployed commit contains the intended season label and historical baseline.
2. Activate `.venv`, run `python -m pip check`, and run `python -m unittest discover --start-directory tests --verbose`.
3. Confirm that `SURRENDER_INDEX_CONFIG_DIR` is an absolute path to the external mode-`700` directory and its credential files are mode `600` and available only on the deployment machine.
4. Confirm that no old process or launchd job is running.
5. Wait until preseason has ended before exercising the live feed.
6. Run the bot with posting and notifications disabled, confirm normal startup and an ESPN scoreboard refresh without errors, then stop it with Control-C. This can be done after preseason and before the first regular-season game; it does not need to observe a live punt:

   ```sh
   SURRENDER_INDEX_CONFIG_DIR="$HOME/.config/surrender-index" \
     python surrender_index_bot.py --disableTweeting --disableNotifications
   ```

7. Because that mode deliberately updates state, remove its season-index file before the production start:

   ```sh
   rm -f current_surrender_indices.npy
   ```

   If the no-post run did observe a live drive that production must process, also remove `tweeted_plays.json`; otherwise it will age out and clear automatically after 12 hours. Only remove these files while every bot process is stopped.

8. Start production with no account-enabling flags and verify that the startup output says tweeting is enabled and the main account is disabled.

## Starting and stopping

For a foreground run:

```sh
source .venv/bin/activate
export SURRENDER_INDEX_CONFIG_DIR="$HOME/.config/surrender-index"
python surrender_index_bot.py
```

Stop it with Control-C and wait for the process to exit before editing or removing runtime state.

For an unattended Mac, `deploy/com.andrewshackelford.surrender-index.plist.example` is an optional launchd template. It contains no credentials. Bootstrap it only after preseason has ended and the production preflight is complete:

1. Copy it to `~/Library/LaunchAgents/com.andrewshackelford.surrender-index.plist`.
2. Replace every `REPLACE_WITH_REPOSITORY_PATH` value with the absolute path to the non-synced deployment checkout. Replace `REPLACE_WITH_CONFIG_DIRECTORY` with the absolute path to the existing mode-`700` configuration directory.
3. Create the repository's `logs` directory and lint the edited plist with `plutil -lint`.
4. Re-enable the deliberately seasonal job, then bootstrap it:

   ```sh
   launchctl enable "gui/$(id -u)/com.andrewshackelford.surrender-index"
   launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.andrewshackelford.surrender-index.plist
   ```

Stop the launchd job before maintenance:

```sh
launchctl bootout "gui/$(id -u)" ~/Library/LaunchAgents/com.andrewshackelford.surrender-index.plist
```

After the final postseason run, both boot out and persistently disable the job so a later login cannot reload it during the offseason or preseason:

```sh
launchctl bootout "gui/$(id -u)" ~/Library/LaunchAgents/com.andrewshackelford.surrender-index.plist
launchctl disable "gui/$(id -u)/com.andrewshackelford.surrender-index"
launchctl print-disabled "gui/$(id -u)"
```

Confirm that the label is disabled. The next regular-season start must explicitly run the `enable` and `bootstrap` commands above. The template's `Umask` is `077`, and it supplies no bot flags, so it uses the normal `@surrender_idx90` and notification defaults. Make account or notification choices explicitly in the installed local copy, not by committing credentials or machine-specific paths.

## Monitoring

During every game window:

- Confirm that the process is running. For launchd, use `launchctl print "gui/$(id -u)/com.andrewshackelford.surrender-index"`.
- Follow `logs/surrender-index.log` and `logs/surrender-index.error.log` when using the launchd template. The foreground equivalent is the terminal output.
- Look for the daily startup heartbeat, current game IDs, calculated post text, and any exception/backoff messages.
- Compare a detected 90th-percentile punt with the `@surrender_idx90` timeline. Printing post text proves calculation, not successful delivery.
- Check that `current_surrender_indices.npy` grows during the season and that `tweeted_plays.json` changes during live games.
- Investigate a missing heartbeat, repeated backoff, an unmoving state file during a game, or a log that stops updating.

The process refreshes the scoreboard daily, retries transient ESPN request failures, and uses exponential backoff after uncaught errors. Those behaviors do not replace external process and post-delivery monitoring.

## Rollback

1. Stop the foreground process or boot out the launchd job.
2. Preserve logs and record the failing commit. Do not copy credentials into the repository.
3. During the initial 2026 cutover, stop here and hold the bot. Do not redeploy the pre-migration commit: it would post a 2025 label, load only the 1999–2024 baseline, and its old dependencies are not compatible with the supported Python runtime. Prepare and validate a corrected 2026-compatible commit instead.
4. For a later in-season release, a rollback is allowed only when the target is a previously deployed, proven 2026-compatible commit. With a clean working tree, switch to that commit or deployment branch. Avoid a hard reset so local state is not accidentally destroyed.
5. Recreate `.venv` from the rollback revision's `requirements.txt` if dependencies changed.
6. Verify that the code and tracked historical baseline come from the same revision.
7. Run the no-post validation and perform its manual `current_surrender_indices.npy` cleanup.
8. Restart without main or cancel account flags and monitor the next live window.

If the failed release wrote incorrect live values, keep the bot stopped until the exact affected values are understood. Correcting production state is a separate, reviewed operation; do not replace the whole file with an old copy merely to make the service start.
