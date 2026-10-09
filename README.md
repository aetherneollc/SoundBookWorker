# SoundBook render worker

Public GitHub Actions repository for CPU based FFmpeg rendering. It contains no application credentials. The Cloudflare control plane dispatches `render.yml` with a unique `job_id` and a private R2 `manifest_key`; the runner downloads all referenced objects and uploads `renders/<job_id>/output.mp4`.

## R2 object manifest (v1)

```json
{
  "version": 1,
  "width": 1920,
  "height": 1080,
  "fps": 30,
  "shots": [{"key": "projects/demo/shot-01.png", "duration": 5}],
  "audio": {"key": "projects/demo/audio.mp3"},
  "subtitle": {"key": "projects/demo/subtitles.srt"}
}
```

Add repository Actions secrets `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_ENDPOINT` (`https://<account-id>.r2.cloudflarestorage.com`), `R2_BUCKET`, `CALLBACK_URL` (the Worker URL ending in `/api/callback`) and `CALLBACK_TOKEN` (matching the Cloudflare Worker secret). Keep the bucket private and grant the key access only to this bucket. A manifest path is a path inside the runner only after the workflow downloads it.

The renderer can also run locally with `python renderer/render.py manifest.json output.mp4`; local manifests use `file` paths. GitHub-hosted runners install FFmpeg per job and have no persistent local state.
