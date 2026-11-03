import { appendFileSync, mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import type { CDPSession, Page } from "@playwright/test";

/**
 * Records a Playwright page for the demo videos (tools/demo/README.md).
 *
 * Two capture modes:
 * - live: Chrome's screencast (Page.startScreencast). A frame arrives whenever the page
 *   repaints, so a static page costs nothing and motion is smooth;
 * - lapse: one screenshot every `intervalMs`, for long waits (builds, tests) that the
 *   videos show sped up. It bounds the number of frames on disk.
 *
 * Everything else is an event in events.jsonl with the recording time `t` (seconds):
 * chapters, captions, playback speeds for the explainer (`speed`) and for the GIF (`gif`),
 * and the poster frame. tools/demo/render.py turns frames + events into the videos.
 */
export class Recorder {
  readonly dir: string;
  private readonly framesDir: string;
  private readonly t0 = Date.now();
  private cdp: CDPSession | null = null;
  private page: Page | null = null;
  private mode: "off" | "live" | "lapse" = "off";
  private lapseTimer: NodeJS.Timeout | null = null;
  private lapseBusy = false;
  private n = 0;

  constructor(
    dir: string,
    private readonly size = { width: 1920, height: 1080 },
    private readonly quality = 88,
  ) {
    this.dir = dir;
    this.framesDir = path.join(dir, "frames");
    mkdirSync(this.framesDir, { recursive: true });
    writeFileSync(path.join(dir, "frames.jsonl"), "");
    writeFileSync(path.join(dir, "events.jsonl"), "");
  }

  /** Seconds since the recorder was created. */
  now(): number {
    return (Date.now() - this.t0) / 1000;
  }

  event(type: string, data: Record<string, unknown> = {}): void {
    appendFileSync(path.join(this.dir, "events.jsonl"), JSON.stringify({ t: this.now(), type, ...data }) + "\n");
  }

  private saveFrame(base64: string): void {
    const t = this.now();
    const file = `${String(++this.n).padStart(6, "0")}.jpg`;
    writeFileSync(path.join(this.framesDir, file), Buffer.from(base64, "base64"));
    appendFileSync(path.join(this.dir, "frames.jsonl"), JSON.stringify({ t, f: file }) + "\n");
  }

  /** Start recording `page` in live mode. */
  async start(page: Page): Promise<void> {
    this.page = page;
    this.cdp = await page.context().newCDPSession(page);
    this.cdp.on("Page.screencastFrame", (frame) => {
      if (this.mode === "live") this.saveFrame(frame.data);
      this.cdp?.send("Page.screencastFrameAck", { sessionId: frame.sessionId }).catch(() => {});
    });
    writeFileSync(path.join(this.dir, "meta.json"), JSON.stringify({ size: this.size, started: new Date(this.t0).toISOString() }, null, 2));
    await this.live();
  }

  private async startScreencast(): Promise<void> {
    await this.cdp!.send("Page.startScreencast", {
      format: "jpeg",
      quality: this.quality,
      maxWidth: this.size.width,
      maxHeight: this.size.height,
      everyNthFrame: 1,
    });
    // The screencast sends nothing until the next repaint: take one frame now.
    await this.snapshot();
  }

  private async snapshot(): Promise<void> {
    if (!this.cdp || this.lapseBusy) return;
    this.lapseBusy = true;
    try {
      const shot = await this.cdp.send("Page.captureScreenshot", { format: "jpeg", quality: this.quality });
      this.saveFrame(shot.data);
    } catch {
      // the page is navigating: the next frame comes with the next tick or repaint
    } finally {
      this.lapseBusy = false;
    }
  }

  /** Live mode: every repaint is a frame. Call again after a navigation to another site. */
  async live(): Promise<void> {
    if (this.lapseTimer) clearInterval(this.lapseTimer);
    this.lapseTimer = null;
    if (this.mode === "live") await this.cdp!.send("Page.stopScreencast").catch(() => {});
    this.mode = "live";
    await this.startScreencast();
  }

  /** Lapse mode: one frame every `intervalMs`, for long waits shown sped up. */
  async lapse(intervalMs = 500): Promise<void> {
    if (this.mode === "live") await this.cdp!.send("Page.stopScreencast").catch(() => {});
    if (this.lapseTimer) clearInterval(this.lapseTimer);
    this.mode = "lapse";
    await this.snapshot();
    this.lapseTimer = setInterval(() => void this.snapshot(), intervalMs);
  }

  /** Navigate and keep recording (a cross-site navigation can end the screencast). */
  async goto(url: string): Promise<void> {
    await this.page!.goto(url);
    if (this.mode === "live") await this.live();
  }

  /** A new chapter. `gif` is the GIF's playback speed for it, or false to leave it out. */
  chapter(index: number, total: number, title: string, gif: number | false = 6): void {
    this.event("chapter", { index, total, title });
    this.gif(gif);
  }

  /** Caption for the explainer; it stays until the next caption or `clear()`. */
  caption(text: string): void {
    this.event("caption", { text });
  }

  /** No caption and no chapter label from now on (the closing card). */
  clear(): void {
    this.event("caption", { text: "" });
    this.event("chapter", { index: 0 });
  }

  /** Playback speed of the explainer from now on (1 = real time). */
  speed(x: number): void {
    this.event("speed", { x });
  }

  /** Playback speed of the GIF from now on, or false to cut until the next gif() call. */
  gif(x: number | false): void {
    this.event("gif", { x });
  }

  /** The frame shown now becomes the poster image. */
  poster(): void {
    this.event("poster");
  }

  /** Show a caption at real-time speed long enough to read it. */
  async say(text: string, minMs = 0): Promise<void> {
    this.caption(text);
    this.speed(1);
    const words = text.split(/\s+/).length;
    await this.page!.waitForTimeout(Math.max(minMs, 1200 + words * 330));
  }

  async stop(): Promise<void> {
    if (this.lapseTimer) clearInterval(this.lapseTimer);
    if (this.mode === "live") await this.snapshot();
    if (this.mode === "live") await this.cdp?.send("Page.stopScreencast").catch(() => {});
    this.mode = "off";
    this.event("end");
  }
}
