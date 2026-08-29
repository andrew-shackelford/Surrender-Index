# Surrender Index

This project contains a bot that follows live NFL games and posts the "Surrender Index" of each qualifying punt. The Surrender Index is a deliberately arbitrary metric created by SB Nation's [Jon Bois](https://twitter.com/jon_bois) to quantify how cowardly a punt is.

The X accounts are `@surrender_index` for the main account and `@surrender_idx90` for the secondary account, which posts only punts at or above the current season's 90th percentile. `@CancelSurrender` supports the optional public vote workflow. The main and cancel accounts are disabled by default; a normal run posts only qualifying punts to `@surrender_idx90`.

The repository also contains a Jupyter notebook used to calculate the historical Surrender Index distribution from nflverse play-by-play data. The bot uses that distribution to put each live punt in historical context.

## Supported setup

The supported runtime is CPython 3.13.15 in a repository-local virtual environment. Confirm that `python3.13 --version` reports `Python 3.13.15`, then run:

```sh
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m unittest discover --start-directory tests --verbose
```

Google Chrome must also be installed because the current runtime initializes ChromeDriver support. The bot needs X credentials and, depending on notification choices, Gmail OAuth or Twilio values. Never commit or cloud-sync these secrets.

For production, use a local checkout that is not inside Google Drive, Dropbox, iCloud Drive, or another synchronized folder. Keep credentials outside the checkout in an owner-only directory:

```sh
install -d -m 700 "$HOME/.config/surrender-index"
install -m 600 credentials.example.json "$HOME/.config/surrender-index/credentials.json"
export SURRENDER_INDEX_CONFIG_DIR="$HOME/.config/surrender-index"
```

Replace the placeholders in that installed `credentials.json`. Put `gmail_credentials.json` in the same directory with mode `600` if Gmail OAuth is needed. The environment variable is optional for backward compatibility, but production should set it; otherwise the bot looks in its working directory.

Start the default account configuration with:

```sh
python surrender_index_bot.py
```

By default, posting and notifications are enabled, while the main and cancel accounts are disabled. See [Season operations](docs/operations.md) before a first run, a no-post test, or a season rollover. It documents every command-line flag, the manual state cleanup required after `--disableTweeting`, monitoring, launchd setup, and rollback.

## Accounts and automation status

- `@surrender_idx90`: enabled during a normal run; uses the X API and posts punts at or above the current-season 90th percentile.
- `@surrender_index`: disabled unless `--enableMainAccount` is passed; its present browser-based posting path is intentionally retained.
- `@CancelSurrender`: disabled unless `--enableCancel` is passed; the account has not been part of the routine run since the X API and automation-policy changes.

Do not enable the main or cancel workflows as part of a routine season start without a separate validation effort.

## Credits

This bot would not be possible without [nflverse's](https://github.com/nflverse) [play-by-play data](https://github.com/nflverse/nflverse-data/releases/tag/pbp). Thanks to Ben Baldwin and the other nflfastR/nflverse maintainers. Ben's [fourth-down decision bot](https://twitter.com/ben_bot_baldwin) also analyzes fourth-down plays, with considerably more statistical rigor.

The project was created by Andrew Shackelford (`andrewshackelford97@gmail.com`). Comments, suggestions, and pull requests are welcome.

### Mastodon versions

Tom Casavant has graciously [forked the repository](https://github.com/TomCasavant/Surrender-Index) and created Mastodon versions at [@surrender_index@tomkahe.com](https://tomkahe.com/@surrender_index) and [@surrender_idx90@tomkahe.com](https://tomkahe.com/@surrender_idx90).
