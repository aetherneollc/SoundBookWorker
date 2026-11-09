# SoundBook Remotion renderer

Public GitHub Actions repository for Remotion video composition and MOSS speech synthesis. It contains no story-generation, LLM, image-generation runtime, or application credentials. The Cloudflare control plane dispatches `render.yml` with a unique `job_id` and a private R2 `manifest_key`; the runner downloads the already-generated media and uploads `renders/<job_id>/output.mp4`. Remotion handles moving camera planes, the AT-1 rover, animated diagrams, title and end cards, captions, and synchronized audio. For human story shots, an optional CPU anime segmentation step cuts out a clear single character, repairs the covered background, and moves the character independently of the camera. Uncertain masks keep the original image.

## R2 object manifest (v1)

```json
{
  "version": 1,
  "width": 1280,
  "height": 720,
  "fps": 24,
  "shots": [{"key": "projects/demo/empty-lunar-ground.png", "duration": 5,
             "scene_number": 1, "rover": {"action": "drive", "speaker": "阿拓 AT-1"}}],
  "audio_tracks": [
    {"key": "projects/demo/line-01.wav", "start_ms": 0, "volume": 1.0},
    {"key": "projects/demo/line-02.wav", "start_ms": 4200, "volume": 1.0}
  ],
  "subtitle": {"key": "projects/demo/subtitles.srt"}
}
```

`audio` remains supported as a single full-length audio track. `audio_tracks` allows the studio to place individual dialogue clips on the timeline. The Python preparation step measures the final speech files, locks the image durations to them, and passes that timeline to Remotion. FFmpeg remains a dependency for probing and speech pacing.

The first AT-1 is a reusable three-wheel engineering rover, drawn as a deterministic SVG in `remotion/rover.tsx`. Its actions are `idle`, `drive`, `sample`, and `signal`. A rover shot's image must be an empty plate with ground across the lower frame and space on the right for the rover. The control plane stores that background prompt separately from the shot description. AT-1 has no human face; speaking is shown by its communication indicator and timed audio cues. Human characters use separate layered assets only when a usable mask/rig exists.

Use `qa_mode=true` with a private test manifest to upload an isolated MP4 artifact without changing any studio episode. This is the acceptance path for new animation, voice, and timing changes before daily production uses them.

Add repository Actions secrets `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_ENDPOINT` (`https://<account-id>.r2.cloudflarestorage.com`), `R2_BUCKET`, `CALLBACK_URL` (the Worker URL ending in `/api/callback`) and `CALLBACK_TOKEN` (matching the Cloudflare Worker secret). Keep the bucket private and grant the key access only to this bucket. A manifest path is a path inside the runner only after the workflow downloads it.

The renderer can also run locally with `python renderer/prepare_remotion.py manifest.json remotion-props.json` followed by `npm run render`; local manifests use `file` paths. GitHub-hosted runners have no persistent local state.
