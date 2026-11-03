"use client";

import { useEffect, useMemo, useState } from "react";
import { Canvas, useThree } from "@react-three/fiber";
import { BufferGeometry, Line, LineBasicMaterial, LoadingManager, Vector3, type Object3D } from "three";
import URDFLoader, { type URDFRobot } from "urdf-loader";

const PACKAGES = {
  annin_ar4_description: "/login-assets/meshes/annin_ar4_description",
  ar3_description: "/login-assets/meshes/ar3_description",
};

export interface DetectedObject {
  id: string;
  class: string;
  frame: string;
  xyz: [number, number, number];
  color?: string;
  /** scenario preview: box [x y z], cylinder [diameter height], sphere [diameter] */
  shape?: "box" | "cylinder" | "sphere" | "mesh";
  size?: number[];
  yaw?: number;
}

function Scene({
  urdf,
  joints,
  plan,
  objects,
}: {
  urdf: string;
  joints: Record<string, number>;
  plan: Record<string, number>[];
  objects: DetectedObject[];
}) {
  const robot = useMemo<URDFRobot>(() => {
    const loader = new URDFLoader(new LoadingManager());
    loader.packages = PACKAGES;
    const r = loader.parse(urdf);
    r.rotation.x = -Math.PI / 2; // ROS z-up -> three.js y-up
    return r;
  }, [urdf]);

  // The planned trajectory as the path of tool0: forward kinematics on the robot itself,
  // then back to the current joints (synchronous, nothing is rendered in between).
  const path = useMemo(() => {
    if (!plan.length || !robot.links.tool0) return null;
    const pts: Vector3[] = [];
    for (const point of plan) {
      Object.entries(point).forEach(([n, v]) => robot.setJointValue(n, v));
      robot.updateMatrixWorld(true);
      pts.push(robot.links.tool0.getWorldPosition(new Vector3()));
    }
    Object.entries(joints).forEach(([n, v]) => robot.setJointValue(n, v));
    const line = new Line(new BufferGeometry().setFromPoints(pts), new LineBasicMaterial({ color: "#eb6834" }));
    return line;
    // joints are applied below every time; the path only depends on the plan
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [robot, plan]);

  // frameloop="demand": a frame only when the joints change, not 60 a second (the page also
  // has to keep up with the 10 Hz stream, and software WebGL is slow)
  const invalidate = useThree((st) => st.invalidate);
  useEffect(() => {
    Object.entries(joints).forEach(([name, value]) => robot.setJointValue(name, value));
    invalidate();
  }, [robot, joints, invalidate]);

  return (
    <>
      <primitive object={robot as unknown as Object3D} />
      {path && <primitive object={path} />}
      {objects.map((o) => {
        const parent = robot.links[o.frame] ?? robot.links.table;
        if (!parent) return null;
        const p = parent.localToWorld(new Vector3(...o.xyz));
        return (
          <mesh key={o.id} position={p} rotation={[0, o.yaw ?? 0, 0]}>
            {o.shape === "sphere" ? (
              <sphereGeometry args={[(o.size?.[0] ?? 0.03) / 2, 20, 14]} />
            ) : o.shape === "cylinder" ? (
              <cylinderGeometry args={[(o.size?.[0] ?? 0.03) / 2, (o.size?.[0] ?? 0.03) / 2, o.size?.[1] ?? 0.05, 20]} />
            ) : (
              // three.js is y-up: the table's (x, y, z) box is (x, z, y) here
              <boxGeometry args={[o.size?.[0] ?? 0.03, o.size?.[2] ?? 0.03, o.size?.[1] ?? 0.03]} />
            )}
            <meshStandardMaterial color={o.color || "#1baf7a"} />
          </mesh>
        );
      })}
    </>
  );
}

/** URDF at the live joint states, the last planned path, detected objects (§10.3). */
export function Robot3D({
  joints,
  plan = [],
  objects = [],
}: {
  joints: Record<string, number>;
  plan?: Record<string, number>[];
  objects?: DetectedObject[];
}) {
  const [urdf, setUrdf] = useState<string | null>(null);
  useEffect(() => {
    fetch("/login/ar3.urdf")
      .then((r) => r.text())
      .then(setUrdf)
      .catch(() => setUrdf(null));
  }, []);
  return (
    <div className="h-80 w-full rounded-lg bg-default-100" data-testid="robot-3d">
      {urdf && (
        <Canvas frameloop="demand" camera={{ position: [1.0, 1.45, 1.2], fov: 40 }} onCreated={({ camera }) => camera.lookAt(-0.2, 0.95, 0)}>
          <hemisphereLight args={["#ffffff", "#8a8f99", 1.6]} />
          <directionalLight position={[2, 4, 3]} intensity={2.2} />
          <Scene urdf={urdf} joints={joints} plan={plan} objects={objects} />
        </Canvas>
      )}
    </div>
  );
}
