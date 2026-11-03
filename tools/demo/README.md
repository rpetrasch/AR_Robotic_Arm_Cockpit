# Demo videos

Two videos of the AR3 Cockpit, recorded from the real cockpit and a real Gazebo stack on
this machine:

| File | What | Use |
|---|---|---|
| `demo.gif` | ~1 min time-lapse, chapter labels only | README |
| `demo.mp4` | the same time-lapse as MP4 (a fraction of the GIF's size) | landing page `<video autoplay muted loop playsinline>` |
| `explainer.mp4` | the full walkthrough, captions burned in, real time except the marked speed-ups | docs, YouTube |
| `explainer.vtt` / `.srt` | the captions as subtitle files | `<track>` on a web page, YouTube upload |
| `poster.jpg` | a still from the Monitoring chapter | poster image for the videos |

## Record

Requirements: the usual cockpit setup (`./cockpit.sh` once: DB and Redis running, the
frontend's `node_modules`), Docker, the images built (`make image image-vnc`), and ffmpeg
(`brew install ffmpeg`). Stop any running ROS stack first; the recording starts and stops its
own. Nothing else should use ports 6080, 8080, 8401 and 8765 meanwhile.

```bash
make demo-video                 # ~30–45 min: records, then renders to build/demo/<time>/out/
make demo-video DEMO_TESTS=0    # without `make test` in chapter 1 (saves ~10 min)
make demo-video DEMO_EXECUTE=0  # Install & Setup only shown, no steps run
make demo-video DEMO_THEME=dark # the GUI in dark mode (default: light)
make demo-render                # render the newest recording again (e.g. after changing render.py)
make demo-gif                   # only the time-lapse (demo.gif, demo.mp4), much faster
make demo-publish               # copy its demo.gif + poster to docs/media/
# an older recording: add DIR=build/demo/20261103-152211 (the folder name is the start time)
```

The recording uses the e2e server (port 8401, its own empty database `ar3cockpit_e2e`, user
`demo`), like the real acceptance tests, so your own cockpit data is not touched. In chapter 1
it touches the mtime of `src/ar3_bringup/package.xml` to show the "rebuild needed" check; the
file content does not change.

## Storyboard

| # | Chapter | What happens | Explainer | GIF |
|---|---|---|---|---|
| – | Intro, sign in | title card; login screen (simulated and real arm) | 1× | 1.5× |
| 1 | Install & Setup | Re-check all, scroll the checklist, show a check's CLI command; then really run *Build the workspace* and `make test` with live logs | 1×, builds 8×, tests 20× | 3×, steps cut |
| 2 | Environment | Gazebo + RViz in the browser + Lichtblick, the live command, start (time-lapse), *Components* ("what runs where"), *Viewers* | 1×, start 6× | 4×, start 12× |
| 3 | RViz in the browser | noVNC; a script sends `home`, then three random joint goals: MoveIt plans, RViz shows it, Gazebo moves | 1× | 3× |
| 4 | Lichtblick | Lichtblick web on foxglove_bridge while the arm makes three random moves | 1× | 3× |
| 5 | Monitoring | Telemetry at 10 Hz during five random moves, scrolled down to the 3D view, cameras and topic rates | 1× | 3× |
| 6 | Simulations | the scenario *Tour over three objects*: editor, run, live steps, evaluation, history | 1×, run 2× | 4×, run 6× |
| – | Closing card | | 1× | 1.5× |

The storyboard is `tools/cockpit/frontend/tests/demo/cockpit-demo.spec.ts`; captions are the
`rec.say()` / `rec.caption()` texts there. Speeds are `rec.speed()` (explainer) and `rec.gif()`
(time-lapse, `false` cuts).

## How it works

- `tests/demo/recorder.ts` records the browser with Chrome's screencast (a frame per repaint)
  or, during long waits, one screenshot every 0.5–1 s. It logs chapters, captions and speeds
  with their recording time.
- `random_moves.py` runs in the stack's ROS container: random collision-free joint goals
  around `home`, planned and executed through MoveIt's `move_group` (velocity scaling from
  `config/robot.yaml`).
- `render.py` (Python + Pillow, run through `uv`) maps recording time to video time per the
  speeds, draws captions, chapter labels and the speed badge, and encodes with ffmpeg
  (H.264; the GIF with a two-pass palette).

## Tuning

- GIF length and size: `make demo-gif` fits the time-lapse into 45 s by speeding up the longest
  chapters first (every chapter gets at most the same share; short ones keep their pace), at
  800 px, 8 fps, 96 colours: about 4.5 MB. Change it with
  `RENDER_ARGS="--gif-seconds 35 --gif-width 720 --gif-fps 6 --gif-colors 64"` (smaller) or
  `--gif-seconds 0` for the storyboard's own speeds.
- RViz window not filling the noVNC desktop: the desktop is 1600×900 (`VNC_GEOMETRY` in
  `docker/vnc-start.sh`); the browser scales it to fit 1280×720.
