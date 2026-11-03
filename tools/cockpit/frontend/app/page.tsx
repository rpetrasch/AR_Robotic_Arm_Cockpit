"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

/** The static export has no server redirects: send "/" to the Environment page. */
export default function Home() {
  const router = useRouter();
  useEffect(() => router.replace("/environment/"), [router]);
  return null;
}
