"use client";

import { useEffect, useMemo, useState } from "react";
import { Canvas } from "@react-three/fiber";
import { LoadingManager, type Object3D } from "three";
import URDFLoader, { type URDFRobot } from "urdf-loader";

// Same URDF and meshes as the login background (served by the backend).
const PACKAGES = {
  annin_ar4_description: "/login-assets/meshes/annin_ar4_description",
  ar3_description: "/login-assets/meshes/ar3_description",
};

function Robot({ urdf, joints }: { urdf: string; joints: Record<string, number> }) {
  const robot = useMemo<URDFRobot>(() => {
    const loader = new URDFLoader(new LoadingManager());
    loader.packages = PACKAGES;
    const r = loader.parse(urdf);
    r.rotation.x = -Math.PI / 2; // ROS z-up -> three.js y-up
    return r;
  }, [urdf]);
  useEffect(() => {
    Object.entries(joints).forEach(([name, value]) => robot.setJointValue(name, value));
  }, [robot, joints]);
  return <primitive object={robot as unknown as Object3D} />;
}

/** The AR3 URDF at the given joint angles [rad] (pre-flight pose check, §9.4 step 5). */
export function RobotPose({ joints }: { joints: Record<string, number> }) {
  const [urdf, setUrdf] = useState<string | null>(null);
  useEffect(() => {
    fetch("/login/ar3.urdf")
      .then((r) => r.text())
      .then(setUrdf)
      .catch(() => setUrdf(null));
  }, []);
  return (
    <div className="h-72 w-full rounded-lg bg-default-100" data-testid="robot-pose">
      {urdf && (
        <Canvas camera={{ position: [1.0, 1.45, 1.2], fov: 40 }} onCreated={({ camera }) => camera.lookAt(-0.2, 0.95, 0)}>
          <hemisphereLight args={["#ffffff", "#8a8f99", 1.6]} />
          <directionalLight position={[2, 4, 3]} intensity={2.2} />
          <Robot urdf={urdf} joints={joints} />
        </Canvas>
      )}
    </div>
  );
}
