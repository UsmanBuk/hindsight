# Contributing to Hindsight

Thanks for your interest! Bug reports, fixes and features are all welcome.

## Reporting bugs

Open an issue and include:

- your distro, desktop environment and session type (`echo $XDG_SESSION_TYPE`);
- your audio server (`pactl info | grep 'Server Name'`) and `ffmpeg -version | head -1`;
- the relevant lines of `~/.local/state/hindsight/hindsight.log`.

**Never attach recordings, transcripts or notes from real meetings.** They contain other people's voices and words. Reproduce the problem with a test recording instead.

## Development setup

```bash
git clone https://github.com/UsmanBuk/hindsight.git
cd hindsight
./install.sh            # or: uv sync, then download the models as in install.sh
uv run python hindsight.py status
```

To work on transcription and notes without making a new recording, re-run processing on an existing meeting folder:

```bash
uv run python hindsight.py process ~/Meetings/<folder>
```

## Before opening a pull request

CI runs these checks, so run them locally first:

```bash
uvx ruff check .                  # lint (config in pyproject.toml)
python3 -m py_compile hindsight.py
shellcheck install.sh bin/hindsight
```

Guidelines:

- Keep Hindsight **offline by default**. New features must not send audio, video or text off the machine unless the user explicitly configures it.
- Make new settings optional `HINDSIGHT_*` environment variables with sensible defaults, and document them in the README's Configuration table.
- Don't hardcode paths, device names or usernames.
- Keep pull requests focused. Describe how you tested your change (distro, desktop, GPU or no GPU).
- Never commit recordings, models or personal data. `.gitignore` covers the usual cases.

## License

By contributing, you agree that your contributions are licensed under the [MIT License](LICENSE).
