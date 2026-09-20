import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';

const root = '/home/mikel/.herdr/worktrees/predictive-models/feat-training-devin';
const output = path.dirname(fileURLToPath(import.meta.url));
const delivery = path.dirname(output);
const app = path.join(root, 'apps/app');
const digest = value => crypto.createHash('sha256').update(value).digest('hex');
const json = file => JSON.parse(fs.readFileSync(file, 'utf8'));
const rel = file => path.relative(root, file).split(path.sep).join('/');
const target = 'a3891095f07a0fd0c435132d2bc560e8970c79f2e9bc8945fe02b0a36affacbf';
const before = json(path.join(output, 'protected-before.json'));
const plan = json(path.join(output, 'candidate-1-plan.json'));
const result = json(path.join(output, 'candidate-1-result.json'));
const inspection = json(path.join(output, 'inspection.json'));
const transition = json(path.join(delivery, 'transition.json'));
const sha = file => digest(fs.readFileSync(file));
const actualFiles = Object.fromEntries(Object.keys(before).map(p => {
  const content = fs.readFileSync(path.join(root, p));
  return [p, { sha256: digest(content), bytes: content.length }];
}));
const differences = Object.keys(before).filter(p => actualFiles[p].sha256 !== before[p].sha256);
const sourceVersions = [];
const candidate = json(path.join(output, 'candidate-1.tsbuildinfo'));
for (const [i, name] of candidate.fileNames.entries()) {
  if (name.includes('node_modules') || name.startsWith('./.next/')) continue;
  const p = rel(path.resolve(app, name));
  const info = candidate.fileInfos[i];
  const version = typeof info === 'string' ? info : info.version;
  const modeled = plan.virtual_source_overrides.find(v => v.path === p);
  const expected = p === 'apps/app/next-env.d.ts' ? modeled.sha256 : transition.historical_bindings[p];
  sourceVersions.push({ path: p, compiler_version: version, expected_sha256: expected, equal: version === expected,
    basis: p === 'apps/app/next-env.d.ts' ? 'virtual next typegen production references, not actual file modification' : 'historic sealed binding' });
}
const files = fs.readdirSync(output).filter(name => fs.statSync(path.join(output, name)).isFile()).sort();
const cachePath = 'apps/app/tsconfig.tsbuildinfo';
const value = {
  status: 'STOP_exact_byte_recovery_not_achieved',
  command: 'node reports/modeling/financial-v3/dataset-v1/delivery-v1/cache-recovery-1/finalize.mjs',
  scope: 'One authorized safe targeted recovery attempt. No current app/cache/old seal/transition/scientific files changed. Prior integration evidence retained.',
  target_sha256: target,
  search: { roots: inspection.scan.roots, entries_visited: inspection.scan.entries_visited,
    exact_copies_found: inspection.scan.exact_copies,
    caches_checked: inspection.scan.candidates.map(c => ({ path: c.path, bytes: c.bytes, sha256: c.sha256 })),
    limits: 'Workspace-only named cache scan, ignoring symlinks and dependency directories. Turbopack SST files were listed but not interpreted as historical TypeScript cache backups. No home/secrets/outside-workspace search.' },
  reconstruction: { method: plan.method, typescript_version: plan.typescript_version, next_version: inspection.next_version,
    plan: 'candidate-1-plan.json', detailed_result: 'candidate-1-result.json',
    current_seed_sha256: plan.seed.sha256, candidate: result.candidate,
    compiler_exit_code: result.compiler_exit_code, diagnostics_count: result.diagnostics.length,
    program_files: result.program_inputs.length, compiler_read_inputs: Object.keys(result.input_read_hashes).length,
    excluded_new_v3_files: plan.excluded_new_v3_files,
    original_config_and_virtual_paths_preserved: true, generated_type_inputs: plan.generated_type_inputs,
    historical_application_source_versions: sourceVersions,
    historical_source_version_checks_equal: sourceVersions.every(v => v.equal),
    helper_sha256_at_execution: plan.helper.sha256, helper_sha256_now: sha(path.join(output, 'recovery.mjs')),
    historical_cache_bytes_available_for_structural_diff: false,
    conclusion: 'Compilation success and matching historical application source versions do not imply byte-identical incremental metadata. Candidate hash does not match the sealed cache. Incremental history or previous generated-type state may matter; cause of the exact historic hash difference is not proven.' },
  correction_budget: { principled_candidate_generations: 1, targeted_correction_generations: 0, variants_or_bruteforce: 0,
    reason_for_no_correction: 'No understood historical-byte difference is available to justify a correction. Do not consume the permitted correction on a speculative clean/seed/order variant.' },
  working_cache: { path: cachePath, before: before[cachePath], after: actualFiles[cachePath], unchanged: actualFiles[cachePath].sha256 === before[cachePath].sha256 },
  preservation: { protected_files_checked: Object.keys(before).length, unchanged: differences.length === 0, differences,
    includes: 'Current delivered app/API/MCP/helper/docs source hashes; all source-before captures; generated type declarations; .devin/goal.md; original delivery transition, source-after-blocked and verification; dataset/model manifests; installed compiler package and implementation.',
    full_before_hashes: 'protected-before.json', full_after_hashes: actualFiles },
  commands: [
    { command: inspection.command, exit_code: 0, purpose: 'Bounded cache search and preserved hash capture' },
    { command: 'node reports/modeling/financial-v3/dataset-v1/delivery-v1/cache-recovery-1/recovery.mjs compiler-source', exit_code: 0, purpose: 'Read installed TypeScript compiler path and Next type-check semantics; compiler-source-notes.json' },
    { command: plan.command, exit_code: 2, compiler_exit_code: 0, purpose: 'One candidate; exit 2 explicitly denotes hash mismatch, not a recovered cache' },
    { command: 'node reports/modeling/financial-v3/dataset-v1/delivery-v1/cache-recovery-1/finalize.mjs', success_exit_meaning: 'Evidence creation and unchanged working-file checks only, not recovery success' },
  ],
  read_only_listing_commands: [
    'ls -la apps/app apps/app/.next apps/app/.next/cache apps/app/.next/dev apps/app/.next/dev/cache reports/modeling/financial-v3/dataset-v1/delivery-v1',
    'ls -la reports/modeling/trajectory-v2/app-verification apps/app/.next/types apps/app/.next/dev/types apps/app/.next/cache/turbopack apps/app/node_modules',
    'ls -la apps/app/.next/cache/turbopack/v16.3.5-ca2c75eb apps/app/.next/dev/cache/turbopack reports/modeling/financial-v3/dataset-v1/delivery-v1/source-before/apps/app reports/modeling/financial-v3/dataset-v1/delivery-v1/source-before/apps/app/lib',
  ],
  new_recovery_files_sha256: Object.fromEntries(files.map(name => [name, sha(path.join(output, name))])),
  restored_or_applied: false,
  waived_integrity: false,
  app_source_writes: 0,
  old_seal_or_transition_writes: 0,
  scientific_artifact_writes: 0,
  model_or_outcome_queries: 0,
  real_app_check_or_build_invocations: 0,
  next_safe_action: 'Keep the working cache and fixed transition unchanged. Candidate-1 MUST NOT replace the working cache: its hash is different. Parent must retain the integrity blocker. Further recovery requires an actual exact-byte copy or a newly justified authorized method, not an unapproved metadata variant loop. No old seal/checker waiver.',
  future_current_verification_strategy_not_executed: 'Use an isolated virtual compiler host or isolated tree with build-info outputs intercepted/redirected; avoid real bun run check because next typegen/build also regenerate next-env and caches. Preserve exact generated inputs before any separately authorized verification.',
  scientific_goal: 'Receipt-proxy utility failure, null full health, unsupported Q4 and disabled alerts remain unchanged. This recovery attempt cannot make V3 complete.',
};
fs.writeFileSync(path.join(output, 'recovery-result.json'), JSON.stringify(value, null, 2) + '\n', { flag: 'wx' });
console.log(JSON.stringify({ status: value.status, target_sha256: target, candidate: result.candidate, protected_files_checked: Object.keys(before).length,
  preserved_current_cache_sha256: actualFiles[cachePath].sha256, preservation_unchanged: differences.length === 0,
  historical_source_versions_equal: value.reconstruction.historical_source_version_checks_equal, restored_or_applied: false,
  report: rel(path.join(output, 'recovery-result.json')) }, null, 2));
if (differences.length || !sourceVersions.every(v => v.equal) || plan.helper.sha256 !== sha(path.join(output, 'recovery.mjs'))) process.exitCode = 1;
