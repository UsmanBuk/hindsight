# Hindsight

A local, fully offline meeting recorder for Linux (GNOME on X11 with PipeWire). Press **Super+R** to start recording and press it again to stop. Hindsight records the screen with your mic and the system audio on separate tracks. When you stop, it transcribes both tracks with NVIDIA Parakeet and asks a local Ollama model for meeting notes.

```
~/Meetings/2026-11-01_1830_Billing-Dashboard-Launch-Date-Decision/
├── recording.mp4    # screen (≤1080p, 15 fps, H.264 NVENC) + 2 audio tracks: "Me (mic)", "Others (system)"
├── transcript.md    # timestamped, speaker-labelled: Me / Others 1 / Others 2 …
└── notes.md         # title, summary, key points, decisions, action items (who, what)
```

## Install

```bash
./install.sh
```

The installer does four things:

- creates the Python env with `uv sync`;
- downloads the models into `models/` (about 680 MB, once);
- symlinks `~/.local/bin/hindsight`;
- adds the GNOME custom shortcut **Super+R → `hindsight toggle`**.

Requirements: `ffmpeg` (with `x11grab`, `pulse` and ideally `h264_nvenc`), `pactl`, `xdpyinfo`, `notify-send`, `uv`, and Ollama serving `gemma4:e4b` at `http://127.0.0.1:11434`.

## Usage

| Command | What it does |
|---|---|
| `hindsight toggle` | Start, or stop if already recording (this is what Super+R runs) |
| `hindsight start` / `stop` | Explicit start / stop |
| `hindsight status` | Show whether a recording is in progress |
| `hindsight process <dir>` | (Re)run transcription and notes for a meeting folder |
| `hindsight cleanup` | Delete videos older than 30 days (notes and transcripts are kept) |

Desktop notifications appear when recording starts, when it stops, and when the notes are ready. The log is at `~/.local/state/hindsight/hindsight.log`.

## How it works

1. **Record.** ffmpeg captures the X11 screen, scales it to fit 1920×1080, and encodes at 15 fps with `h264_nvenc` (falling back to `libx264`). It records the default PulseAudio/PipeWire source (mic) and the default sink's `.monitor` (system audio) as two AAC tracks. It writes to `.mkv` while recording, so a crash doesn't corrupt the file, and remuxes to `recording.mp4` on stop.
2. **Transcribe.** Each audio track is extracted at 16 kHz and split into utterances with Silero VAD. The utterances are transcribed with **Parakeet-TDT 0.6B v2** (int8, [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx)). The mic track is labelled `Me`. The system track goes through speaker diarization (pyannote segmentation 3.0 + 3D-Speaker ERes2Net embeddings), so separate remote voices become `Others 1`, `Others 2`, …, or just `Others` if there is only one.
3. **Summarise.** The transcript goes to Ollama's OpenAI-compatible API (`/v1/chat/completions`, `gemma4:e4b`). Ollama serves the model with a 4k context, so long meetings are summarised in chunks and then merged. The notes' title is used to name the folder.
4. **Retention.** Every `start` and `stop` deletes `recording.mp4` files older than 30 days.

Nothing leaves the machine. The only network access is the one-time model download in `install.sh`.

## Configuration

Set these environment variables to override the defaults: `HINDSIGHT_MEETINGS` (default `~/Meetings`), `HINDSIGHT_OLLAMA_URL`, `HINDSIGHT_OLLAMA_MODEL`. Constants such as the frame rate, the maximum size and the retention period are at the top of `hindsight.py`.

## Notes

- If you use speakers rather than a headset, the mic will also pick up the other participants, and their words will show up as `Me` too.
- Wayland is not supported (`x11grab`).
