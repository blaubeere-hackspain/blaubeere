import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { randomBytes } from "node:crypto";

mkdirSync(".local", { recursive: true, mode: 0o700 });
if (existsSync(".env")) {
  console.log("Existing .env preserved. Run bun run dev.");
} else {
  const password = randomBytes(18).toString("base64url");
  writeFileSync(".env", readFileSync(".env.example", "utf8").replace("BOOTSTRAP_PASSWORD=", `BOOTSTRAP_PASSWORD=${password}`), { mode: 0o600 });
  console.log(`Local sign-in: finance@blaubeere.local\nPassword: ${password}\nRun bun run dev.`);
}
