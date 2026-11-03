"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { Cache, LoadingManager, Vector3, type Object3D } from "three";
import URDFLoader, { type URDFRobot } from "urdf-loader";
import { useI18n } from "@/lib/i18n";
import { STILL_T, clock, sample, type Trajectory } from "./trajectory";

Cache.enabled = true; // both halves load the same meshes: fetch them once

// The URDF's package:// meshes, served by the backend before sign-in (core/frontend.py).
const PACKAGES = {
  annin_ar4_description: "/login-assets/meshes/annin_ar4_description",
  ar3_description: "/login-assets/meshes/ar3_description",
};
// base_link sits at world (-0.32, 0, 0.75) (config/robot.yaml); three.js is y-up. Each half
// shifts the view sideways so the arm stands in its outer part, clear of the login panel.
const CAMERA = { position: [1.25, 1.55, 1.65] as const, target: [-0.2, 0.98, 0] as const, fov: 36 };
const SHIFT_M = 0.32;

interface Assets {
  urdf: string;
  trajectory: Trajectory;
}

function hasWebGL(): boolean {
  try {
    const c = document.createElement("canvas");
    return !!(c.getContext("webgl2") || c.getContext("webgl"));
  } catch {
    return false;
  }
}

function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    const m = matchMedia("(prefers-reduced-motion: reduce)");
    setReduced(m.matches);
    const on = () => setReduced(m.matches);
    m.addEventListener("change", on);
    return () => m.removeEventListener("change", on);
  }, []);
  return reduced;
}

/** Login media present at build time (/login/media.json, written by scripts/brand.mjs): the
 * filmed real arm (§7.3 [OPEN]) and its poster. Until the video exists: a placeholder render,
 * and no request for the missing file. */
type LoginMedia = { realVideo: boolean; poster: boolean };
function useLoginMedia(): LoginMedia {
  const [media, setMedia] = useState<LoginMedia>({ realVideo: false, poster: false });
  useEffect(() => {
    fetch("/login/media.json")
      .then((r) => (r.ok ? r.json() : null))
      .then((m) => m && setMedia({ realVideo: !!m.realVideo, poster: !!m.poster }))
      .catch(() => {});
  }, []);
  return media;
}

type Side = "left" | "right";

function Robot({ assets, still, side }: { assets: Assets; still: boolean; side: Side }) {
  const robot = useMemo<URDFRobot>(() => {
    const loader = new URDFLoader(new LoadingManager());
    loader.packages = PACKAGES;
    const r = loader.parse(assets.urdf);
    r.rotation.x = -Math.PI / 2; // ROS z-up -> three.js y-up
    return r;
  }, [assets.urdf]);
  const { camera, invalidate } = useThree();
  const pose = (t: number) => {
    const q = sample(assets.trajectory, t);
    assets.trajectory.joint_names.forEach((name, i) => robot.setJointValue(name, q[i]));
  };
  useEffect(() => {
    // Look past the arm towards the panel: the arm moves to the half's outer side.
    const target = new Vector3(...CAMERA.target);
    const right = target.clone().sub(camera.position).cross(camera.up).normalize();
    camera.lookAt(target.addScaledVector(right, side === "left" ? SHIFT_M : -SHIFT_M));
    if (still) {
      pose(STILL_T(assets.trajectory));
      invalidate();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- pose depends on robot/assets only
  }, [camera, still, robot, side]);
  useFrame(() => {
    if (!still) pose(clock());
  });
  return <primitive object={robot as unknown as Object3D} />;
}

function Scene({ assets, still, side }: { assets: Assets; still: boolean; side: Side }) {
  return (
    <Canvas
      camera={{ position: [...CAMERA.position], fov: CAMERA.fov }}
      frameloop={still ? "demand" : "always"}
      dpr={[1, 1.5]}
      gl={{ antialias: true, alpha: true }}
    >
      <hemisphereLight args={["#ffffff", "#8a8f99", 1.6]} />
      <directionalLight position={[2, 4, 3]} intensity={2.2} />
      <directionalLight position={[-3, 2, -2]} intensity={0.6} />
      <Robot assets={assets} still={still} side={side} />
    </Canvas>
  );
}

function RealVideo({ duration, still, poster }: { duration: number; still: boolean; poster: boolean }) {
  const ref = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    const v = ref.current;
    if (!v) return;
    if (still) {
      v.pause();
      return;
    }
    // Align to the trajectory clock and correct drift (the video is the same motion).
    const align = () => {
      const want = clock() % duration;
      if (Math.abs(v.currentTime - want) > 0.15) v.currentTime = want;
    };
    v.addEventListener("loadedmetadata", align);
    const timer = setInterval(align, 1000);
    return () => {
      clearInterval(timer);
      v.removeEventListener("loadedmetadata", align);
    };
  }, [duration, still]);
  return (
    <video ref={ref} src="/login/real.mp4" poster={poster ? "/login/poster.jpg" : undefined} muted loop playsInline
      autoPlay={!still} />
  );
}

/**
 * Full-screen split background of the login (docs/specs/spec_cockpit.md §7.3): left the simulated
 * AR3 (three.js, URDF from ar3_description), right the real AR3 (video), same motion in step.
 * Fallbacks: no video → a placeholder render; no WebGL → a still image; reduced motion →
 * both still.
 */
export function LoginBackground() {
  const { t } = useI18n();
  const [assets, setAssets] = useState<Assets | null>(null);
  const [webgl, setWebgl] = useState(true);
  const still = useReducedMotion();
  const media = useLoginMedia();

  useEffect(() => {
    setWebgl(hasWebGL());
    Promise.all([
      fetch("/login/ar3.urdf").then((r) => r.text()),
      fetch("/login/trajectory.json").then((r) => r.json()),
    ])
      .then(([urdf, trajectory]) => setAssets({ urdf, trajectory }))
      .catch(() => setWebgl(false));
  }, []);

  // While the model loads, the gradient shows; the still image only without WebGL.
  const scene = (side: Side) =>
    !webgl ? <StillImage /> : assets ? <Scene assets={assets} still={still} side={side} /> : null;
  return (
    <div className="login-bg" aria-hidden="true" data-testid="login-bg">
      <div className="login-half sim">
        <span className="half-label">{t("login.sim")}</span>
        {scene("left")}
      </div>
      <div className="login-half real">
        <span className="half-label">{t("login.real")}</span>
        {media.realVideo && assets ? (
          <RealVideo duration={assets.trajectory.duration_s} still={still} poster={media.poster} />
        ) : (
          <>
            <div className="placeholder-render">{scene("right")}</div>
            <span className="pending">{t("login.realPending")}</span>
          </>
        )}
      </div>
      <div className="login-divider" />
    </div>
  );
}

function StillImage() {
  // eslint-disable-next-line @next/next/no-img-element -- static fallback, may be missing
  return <img className="still" src="/login/still.png" alt="" onError={(e) => (e.currentTarget.style.display = "none")} />;
}
