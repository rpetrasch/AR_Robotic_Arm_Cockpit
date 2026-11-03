/**
 * The editor's single source of truth is the YAML text. The form and the scene editor change it
 * through the `yaml` Document API, which keeps comments and key order, so a scenario edited in
 * the form still reads like the file a person wrote.
 */
import YAML from "yaml";

export type Path = (string | number)[];

export interface Scenario {
  id?: string;
  name?: string;
  description?: string;
  version?: number;
  tags?: string[];
  modes?: string[];
  world?: string;
  start?: { named_pose?: string; joints?: number[]; gripper?: number };
  scene?: {
    objects?: SceneObject[];
    obstacles?: SceneObject[];
    bins?: { name: string; ref?: string; xyz?: number[]; yaw?: number; size?: number[] }[];
    randomize?: { seed?: number; xy_jitter_m?: number; yaw_jitter_deg?: number };
  };
  perception?: string;
  task?: { type?: string; steps?: Step[]; params?: Record<string, unknown> };
  planning?: { pipeline?: string; planner_id?: string; velocity_scaling?: number; acceleration_scaling?: number; planning_time_s?: number };
  success?: { all_steps_succeed?: boolean; objects_in_bins?: Record<string, string>; max_duration_s?: number };
  repeat?: number;
}

export interface SceneObject {
  id: string;
  shape: "box" | "cylinder" | "sphere" | "mesh";
  size: number[];
  color?: string | number[];
  class?: string;
  pose: { x: number; y: number; z?: number; yaw_deg?: number };
  spawn?: boolean;
  mesh?: string;
}

export type Step =
  | { move_named: string }
  | { move_joints: number[] }
  | { move_pose: { frame?: string; x: number; y: number; z: number; rpy_deg?: number[]; planner?: string } }
  | { gripper: string | number }
  | { wait: number };

export function read(text: string): { data: Scenario | null; error: string | null } {
  try {
    const doc = YAML.parseDocument(text);
    if (doc.errors.length) return { data: null, error: doc.errors[0].message };
    const data = doc.toJS();
    return { data: data && typeof data === "object" ? (data as Scenario) : null, error: null };
  } catch (e) {
    return { data: null, error: String(e) };
  }
}

/** The text with `path` set to `value` (undefined deletes it). Invalid text is returned as is. */
export function setIn(text: string, path: Path, value: unknown): string {
  const doc = YAML.parseDocument(text);
  if (doc.errors.length) return text;
  if (value === undefined) doc.deleteIn(path);
  else doc.setIn(path, doc.createNode(value, { flow: isLeafish(value) }));
  return doc.toString({ lineWidth: 100 });
}

function isLeafish(value: unknown): boolean {
  // poses, sizes and steps read best inline: {x: 0.3, y: -0.05}
  if (Array.isArray(value)) return value.every((v) => typeof v !== "object");
  return !!value && typeof value === "object" && Object.values(value).every((v) => typeof v !== "object" || Array.isArray(v));
}

export function stepKind(step: Step): string {
  return Object.keys(step)[0];
}

export const NEW_SCENARIO = `# A new scenario (docs/specs/spec_cockpit.md §12.1). Poses are in the table frame [m, deg].
id: my-scenario
version: 1
name: My scenario
description: ""
tags: []
modes: [sim_gazebo, sim_mock]
world: workcell
start:
  named_pose: home
scene:
  objects: []
task:
  type: sequence
  steps:
    - move_named: observe
    - move_named: home
planning:
  pipeline: ompl
  velocity_scaling: 0.2
  acceleration_scaling: 0.2
  planning_time_s: 5
success:
  all_steps_succeed: true
  max_duration_s: 300
`;
