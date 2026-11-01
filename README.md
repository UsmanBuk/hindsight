# Hindsight

**Hindsight is a local, offline meeting recorder for Linux.** Press one key to start recording your screen, your microphone and your computer's audio. Press it again to stop. A few seconds later you have a transcript labelled by speaker and Markdown meeting notes with a summary, decisions and action items. Recording, transcription and summarisation all run on your machine, so no audio or text is sent anywhere.

```
~/Meetings/2026-11-01_1830_Billing-Dashboard-Launch-Date-Decision/
├── recording.mp4    # screen + two audio tracks: "Me (mic)" and "Others (system)"
├── transcript.md    # timestamped, speaker-labelled transcript
└── notes.md         # title, summary, key points, decisions, action items
```

## Features

- **One-key start and stop.** `hindsight toggle` is bound to a global shortcut (Super+R by default on GNOME).
- **Screen and two audio tracks in one file.** The screen is captured with `x11grab`, and the mic and system audio are stored as separate tracks. Video is capped at 1080p and 15 fps, so files stay small (about 2–3 MB per minute for typical desktop content).
- **GPU encoding when available.** Uses `h264_nvenc` on NVIDIA GPUs and falls back to `libx264` automatically.
- **Local speech recognition.** Uses [NVIDIA Parakeet-TDT 0.6B v2](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v2), int8, running on CPU through [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx), with Silero VAD to split speech into utterances.
- **Speaker labels.** Your mic is labelled `Me` and the system audio `Others`. Speaker diarization (pyannote segmentation 3.0 + 3D-Speaker embeddings) separates remote participants into `Others 1`, `Others 2`, ….
- **Local AI notes.** Any [Ollama](https://ollama.com) model (default `gemma4:e4b`) writes the summary, key points, decisions and action items (who and what). Long meetings are summarised in chunks, so they work with small context windows.
- **Housekeeping.** Each meeting folder is named after the generated title. Desktop notifications appear on start, stop and when the notes are ready. Videos older than 30 days are deleted automatically, and notes and transcripts are kept.
- **Crash-safe recording.** Hindsight writes Matroska while recording and remuxes to MP4 on stop. If a recording is interrupted, it is processed the next time you start one.

## Requirements

- **Linux with an X11 session** (Wayland is not supported yet; see [Limitations](#limitations)).
- **PipeWire** (with `pipewire-pulse`) or **PulseAudio**, plus `pactl`.
- **ffmpeg** built with `x11grab` and `pulse` input. `h264_nvenc` is optional.
- `xdpyinfo` (package `x11-utils`), and optionally `notify-send` (`libnotify-bin`) for notifications.
- **[uv](https://docs.astral.sh/uv/)** and Python ≥ 3.12. uv installs the Python dependencies for you.
- **[Ollama](https://ollama.com)** running locally with a chat model pulled, for example `ollama pull gemma4:e4b`.
- **Model downloads (about 680 MB, once):** `install.sh` fetches these from the [sherpa-onnx releases](https://github.com/k2-fsa/sherpa-onnx/releases):
  - Parakeet-TDT 0.6B v2 int8 (ASR, ~660 MB)
  - Silero VAD (~1 MB)
  - pyannote segmentation 3.0 (~6 MB)
  - 3D-Speaker ERes2Net speaker embeddings (~40 MB)

On Debian/Ubuntu:

```bash
sudo apt install ffmpeg pulseaudio-utils x11-utils libnotify-bin curl
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Tested on Ubuntu 24.04 (GNOME on X11, PipeWire) on ARM64 with an NVIDIA GPU.

## Install

```bash
git clone https://github.com/UsmanBuk/hindsight.git
cd hindsight
./install.sh
```

The installer is safe to run again. It does the following:

1. creates the virtualenv with `uv sync`;
2. downloads the models into `./models/`;
3. symlinks `hindsight` into `~/.local/bin`;
4. on GNOME, adds a custom keyboard shortcut for `hindsight toggle`.

You can change the installer's behaviour with these variables:

| Variable | Default | Purpose |
|---|---|---|
| `HINDSIGHT_SHORTCUT` | `<Super>r` | GNOME accelerator for the toggle; use `none` to skip it |
| `HINDSIGHT_BIN_DIR` | `~/.local/bin` | Where the `hindsight` command is linked |
| `HINDSIGHT_MODELS_DIR` | `./models` | Where models are downloaded (set it at runtime too) |

For example, `HINDSIGHT_SHORTCUT='<Ctrl><Alt>m' ./install.sh`. On other desktops, bind a key to `~/.local/bin/hindsight toggle` in your desktop's keyboard settings.

## Usage

| Command | What it does |
|---|---|
| `hindsight toggle` | Starts recording, or stops it if a recording is already running (this is what the shortcut runs) |
| `hindsight start` / `hindsight stop` | Starts or stops explicitly |
| `hindsight status` | Shows whether a recording is in progress |
| `hindsight process <dir>` | Re-runs transcription and notes for a meeting folder |
| `hindsight cleanup` | Deletes videos older than the retention period |

After you stop, the recording is processed in the background, and a notification appears when the notes are ready. Processing a 1-minute meeting takes about 15 seconds on a modern CPU.

## Where output goes

Each meeting gets its own folder under `~/Meetings/` (change it with `HINDSIGHT_MEETINGS_DIR`), named `YYYY-MM-DD_HHMM_<Title>`:

- `recording.mp4`: H.264 video, plus AAC audio track 1 "Me (mic)" and track 2 "Others (system)". Most players let you switch between tracks.
- `transcript.md`: lines like `**[00:01:23] Others 2:** …`, with consecutive utterances from the same speaker merged.
- `notes.md`: title, date, duration, speakers, summary, key points, decisions and action items.

The runtime state and log are in `~/.local/state/hindsight/` (`hindsight.log`).

## Configuration

Hindsight reads `HINDSIGHT_*` environment variables, and every one has a sensible default. The keyboard shortcut runs without your shell profile, so if you change settings, put them where your desktop session picks them up, such as `~/.config/environment.d/hindsight.conf`.

| Variable | Default | Description |
|---|---|---|
| `HINDSIGHT_MEETINGS_DIR` | `~/Meetings` | Output folder |
| `HINDSIGHT_MODELS_DIR` | `<repo>/models` | Model folder |
| `HINDSIGHT_STATE_DIR` | `$XDG_STATE_HOME/hindsight` | State and log folder |
| `HINDSIGHT_OLLAMA_URL` | `http://127.0.0.1:11434/v1` | OpenAI-compatible endpoint |
| `HINDSIGHT_OLLAMA_MODEL` | `gemma4:e4b` | Model used for notes |
| `HINDSIGHT_MIC` | system default source | PulseAudio/PipeWire source name for your mic (see `pactl list short sources`) |
| `HINDSIGHT_SYSTEM_AUDIO` | default sink's `.monitor` | Source to record as "Others" |
| `HINDSIGHT_VIDEO_ENCODER` | `auto` | `auto`, `h264_nvenc` or `libx264` |
| `HINDSIGHT_MAX_WIDTH` / `HINDSIGHT_MAX_HEIGHT` | `1920` / `1080` | Video is scaled down to fit inside this size |
| `HINDSIGHT_FPS` | `15` | Video frame rate |
| `HINDSIGHT_RETENTION_DAYS` | `30` | Delete videos older than this; `0` keeps them forever |
| `HINDSIGHT_DIARIZATION_THRESHOLD` | `0.75` | Lower values split remote voices into more speakers |
| `HINDSIGHT_ASR_THREADS` | `min(12, CPUs)` | CPU threads for speech recognition |

Hindsight sends transcripts only to `HINDSIGHT_OLLAMA_URL`. If you point it at a remote server, your transcripts leave your machine.

## Limitations

- **X11 only.** On Wayland, `x11grab` captures only XWayland windows or nothing at all.
- **The whole X screen is recorded.** With several monitors, all of them are captured and scaled down together.
- **English only.** Parakeet-TDT 0.6B v2 is an English model.
- **Mic bleed.** If you use speakers instead of headphones, your mic also picks up the other participants, and their words show up as `Me` as well.
- **Diarization is approximate.** Similar voices can be merged, and one voice can sometimes be split. Tune `HINDSIGHT_DIARIZATION_THRESHOLD` if this happens.
- **The notes are only as good as the local model.** Check action items before you act on them.
- **Speech recognition runs on CPU.** It is fast enough on recent machines but has no GPU acceleration yet.
- No pause/resume and no tray indicator.

## Roadmap

- Wayland support through the xdg-desktop-portal ScreenCast API and PipeWire.
- Recording a single monitor or window.
- Multilingual transcription (Parakeet-TDT 0.6B v3 / Whisper).
- Echo suppression to remove remote speech that leaks into the mic track.
- GPU-accelerated ASR.
- A config file and custom prompt templates for the notes.
- Pause/resume and a tray indicator.
- Searching across past meetings.
- Tests and packaging (pipx / .deb).

## Consent

**Recording people without their knowledge may be illegal and is always a breach of trust.** Before you record:

- **Tell everyone in the meeting that you are recording** (screen, audio and an AI transcript) and get their consent. Ideally do this at the start of the recording, so the consent is on the record.
- **Follow the law where you and every participant are.** Many jurisdictions require consent from *all* parties to a conversation (for example several US states), and data-protection laws such as the GDPR give participants rights over recordings of them.
- **Follow your organisation's policies**, and the terms of the meeting platform you are using.
- **Store recordings securely, and delete them when you no longer need them.** The default 30-day video retention helps with this.

You are responsible for how you use Hindsight. It is provided as-is, without warranty; see the [LICENSE](LICENSE).

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE) © 2026 Syed Usman Bukhari
