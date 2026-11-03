"""A run's scenario after randomization (docs/specs/spec_cockpit.md §12.1 ``randomize``).

Each object (and obstacle) gets an xy jitter (uniform in ±xy_jitter_m) and a yaw jitter
(uniform in ±yaw_jitter_deg) from a seeded generator, so a run is repeatable from its seed.
A jittered object that would leave the table or overlap another object or a bin is retried
(20 times), then kept where the scenario put it. The resolved YAML is what the run stores.
"""

from __future__ import annotations

import copy
import random

from . import context, validate

RETRIES = 20


def resolve(data: dict, seed: int | None) -> tuple[dict, int | None]:
    sc = copy.deepcopy(data)
    rnd_cfg = (sc.get("scene") or {}).get("randomize")
    if not rnd_cfg:
        return sc, None
    if seed is None:
        seed = int(rnd_cfg.get("seed", random.SystemRandom().randrange(2**31)))
    rnd = random.Random(seed)
    xy = float(rnd_cfg.get("xy_jitter_m", 0.0))
    yaw = float(rnd_cfg.get("yaw_jitter_deg", 0.0))
    site = context.site()
    tx, ty = site["table"]["size"][0] / 2, site["table"]["size"][1] / 2
    bins, _ = validate.resolved_bins(sc, site)
    bin_feet = [validate._bin_footprint(b) for b in {**{b["name"]: b for b in site["bins"]},
                                                     **bins}.values() if "xyz" in b]  # fmt: skip
    placed: list = []
    for key in ("objects", "obstacles"):
        for obj in sc.get("scene", {}).get(key) or []:
            base = dict(obj["pose"])
            for _ in range(RETRIES):
                pose = {
                    **base,
                    "x": round(base["x"] + rnd.uniform(-xy, xy), 4),
                    "y": round(base["y"] + rnd.uniform(-xy, xy), 4),
                    "yaw_deg": round(base.get("yaw_deg", 0.0) + rnd.uniform(-yaw, yaw), 2),
                }
                fp = validate._footprint({**obj, "pose": pose})
                inside = all(abs(x) <= tx and abs(y) <= ty for x, y in fp)
                if inside and not any(validate.overlap(fp, o) for o in placed + bin_feet):
                    obj["pose"] = pose
                    break
            placed.append(validate._footprint(obj))
    sc["scene"]["randomize"] = {**rnd_cfg, "seed": seed}
    return sc, seed
