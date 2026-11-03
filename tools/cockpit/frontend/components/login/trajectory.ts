/**
 * public/login/trajectory.json (written by tools/cockpit/scripts/record_login_trajectory.py):
 * MoveIt-planned joint positions with timestamps, played in a loop on the login screen.
 */
export interface Trajectory {
  version: number;
  duration_s: number;
  joint_names: string[];
  points: { t: number; q: number[] }[];
}

/** Joint positions at time `t` (seconds, wrapped to the duration), linearly interpolated. */
export function sample(traj: Trajectory, t: number): number[] {
  const pts = traj.points;
  const time = ((t % traj.duration_s) + traj.duration_s) % traj.duration_s;
  if (time <= pts[0].t) return pts[0].q;
  let lo = 0;
  let hi = pts.length - 1;
  if (time >= pts[hi].t) return pts[hi].q;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (pts[mid].t <= time) lo = mid;
    else hi = mid;
  }
  const a = pts[lo];
  const b = pts[hi];
  const f = (time - a.t) / (b.t - a.t || 1);
  return a.q.map((v, i) => v + (b.q[i] - v) * f);
}

/** One clock for both halves and the video, so they move in step (§7.3). */
const T0 = typeof performance === "undefined" ? 0 : performance.now();
export const clock = () => (performance.now() - T0) / 1000;

/** The still frame for prefers-reduced-motion: the arm at the `observe` pose (a third in). */
export const STILL_T = (traj: Trajectory) => traj.duration_s / 3;
