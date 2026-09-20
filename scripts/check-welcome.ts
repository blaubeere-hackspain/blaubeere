import assert from "node:assert/strict";
import { welcomeProgress } from "../apps/app/components/welcome-onboarding";

assert.equal(welcomeProgress(null), "guide", "First visits open the guide directly");
assert.equal(welcomeProgress("guide"), "guide", "An unfinished guide remains available");
assert.equal(welcomeProgress("done"), "done", "Completed or dismissed onboarding stays closed");
for (const value of ["", "welcome", "old-version", "undefined"]) assert.equal(welcomeProgress(value), "guide");
console.log("Welcome progress checks passed.");
