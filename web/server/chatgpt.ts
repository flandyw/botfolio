/**
 * ChatGPT plan access for the news AI, through OpenAI's Sign in with ChatGPT SDK (vendored, unmodified, in
 * ./vendor/siwc-local under its noncommercial licence).
 *
 *   node --experimental-strip-types server/chatgpt.ts login   one-time sign-in (paste the callback link back)
 *   node --experimental-strip-types server/chatgpt.ts serve   loopback-only API the Python worker calls
 *
 * The sign-in callback goes to 127.0.0.1 on the machine whose browser opens the link. On a server that is another
 * machine, so `login` asks for the link the browser was sent to and delivers it to its own listener here.
 */
import { createCipheriv, createDecipheriv, randomBytes } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { homedir } from "node:os";
import { join } from "node:path";
import { createInterface } from "node:readline/promises";
import { ChatGPTError, createChatGPT, type CredentialEncryption } from "./vendor/siwc-local/index.js";

const DIR = join(homedir(), ".config", "botfolio-chatgpt");
const PORT = Number(process.env.CHATGPT_SIDECAR_PORT ?? 8611);
const CALLBACK_PORT = Number(process.env.CHATGPT_CALLBACK_PORT ?? 1455);

// A headless server has no OS keychain: tokens are AES-256-GCM encrypted with a key file only this user can read.
// shortcut: key and tokens share a disk; move the key to a secret store (e.g. systemd-creds) if the box is shared.
function fileEncryption(): CredentialEncryption {
  mkdirSync(DIR, { recursive: true, mode: 0o700 });
  const path = join(DIR, "token.key");
  if (!existsSync(path)) writeFileSync(path, randomBytes(32), { mode: 0o600 });
  const key = readFileSync(path);
  return {
    id: "botfolio-aes-256-gcm-keyfile-v1",
    isAvailable: () => key.length === 32,
    encrypt(plaintext) {
      const iv = randomBytes(12);
      const cipher = createCipheriv("aes-256-gcm", key, iv);
      const body = Buffer.concat([cipher.update(plaintext, "utf8"), cipher.final()]);
      return Buffer.concat([iv, cipher.getAuthTag(), body]);
    },
    decrypt(data) {
      const buf = Buffer.from(data);
      const decipher = createDecipheriv("aes-256-gcm", key, buf.subarray(0, 12));
      decipher.setAuthTag(buf.subarray(12, 28));
      return Buffer.concat([decipher.update(buf.subarray(28)), decipher.final()]).toString("utf8");
    },
  };
}

let authorizeUrl: ((url: string) => void) | undefined;
const chatgpt = createChatGPT({
  appName: "Botfolio",
  appId: "botfolio",
  redirectPort: CALLBACK_PORT,
  storageDir: DIR,
  credentialEncryption: fileEncryption(),
  openBrowser: (url) => authorizeUrl?.(url),
});

async function login() {
  const shown = new Promise<string>((resolve) => (authorizeUrl = resolve));
  const done = chatgpt.signIn({ reconsent: true });
  done.catch(() => undefined);
  console.log(`\n1. Open this link and approve Botfolio:\n\n${await shown}\n`);
  console.log(`2. Your browser then lands on a page at 127.0.0.1:${CALLBACK_PORT} that will not load. Copy that whole address.`);
  const rl = createInterface({ input: process.stdin, output: process.stdout });
  const pasted = (await rl.question("\nPaste it here: ")).trim();
  rl.close();
  const callback = new URL(pasted);
  if (callback.hostname !== "127.0.0.1" || Number(callback.port) !== CALLBACK_PORT) throw new Error("That is not the sign-in callback address.");
  await fetch(`http://127.0.0.1:${CALLBACK_PORT}${callback.pathname}${callback.search}`);
  const session = await done;
  console.log(`\nSigned in: ${session.status}, plan sharing ${session.sharing ? "on" : "off"}${session.identity?.email ? ` (${session.identity.email})` : ""}`);
  const models = await chatgpt.listModels();
  console.log(`Models: ${models.map((m) => m.slug).join(", ")}`);
}

function serve() {
  const json = (res: import("node:http").ServerResponse, status: number, body: unknown) =>
    res.writeHead(status, { "content-type": "application/json" }).end(JSON.stringify(body));
  createServer(async (req, res) => {
    try {
      if (req.method === "GET" && req.url === "/status") {
        const session = await chatgpt.getSession();
        const models = session.status === "connected" ? (await chatgpt.listModels()).map((m) => m.slug) : [];
        return json(res, 200, { status: session.status, sharing: session.sharing, models });
      }
      if (req.method === "POST" && req.url === "/respond") {
        let raw = "";
        for await (const chunk of req) raw += chunk;
        const { model, instructions, input } = JSON.parse(raw);
        const { text } = await chatgpt.streamResponse({ model, instructions, input });
        return json(res, 200, { text });
      }
      json(res, 404, { error: "not found" });
    } catch (e) {
      const err = e instanceof ChatGPTError ? e : undefined;
      // The plan's 5-hour or weekly limit (which one is not reported): the worker uses its other model and asks
      // again in an hour. A plain 429 is a short rate limit.
      const limit = err?.code === "subscription_sharing_usage_limit_exceeded";
      if (limit || err?.status === 429) res.setHeader("retry-after", limit ? "3600" : "120");
      json(res, limit || err?.status === 429 ? 429 : err?.code === "reauth_required" || err?.status === 401 ? 401 : 502,
        { error: err?.code ?? "error", message: err?.message ?? String(e) });
    }
  }).listen(PORT, "127.0.0.1", () => console.log(`ChatGPT sidecar on 127.0.0.1:${PORT}`));
}

if (process.argv[2] === "login") await login().then(() => process.exit(0), (e) => { console.error(e.message ?? e); process.exit(1); });
else serve();
