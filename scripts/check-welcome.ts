import assert from "node:assert/strict";
import { welcomeProgress } from "../apps/app/components/welcome-onboarding";

assert.equal(welcomeProgress(null), "welcome", "First visits start at the welcome card");
assert.equal(welcomeProgress("guide"), "guide", "An unfinished introduction resumes at the next step");
assert.equal(welcomeProgress("done"), "done", "Completed or dismissed onboarding stays closed");
for (const value of ["", "welcome", "old-version", "undefined"]) assert.equal(welcomeProgress(value), "welcome");
console.log("Welcome progress checks passed.");
