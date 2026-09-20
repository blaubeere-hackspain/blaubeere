import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const root = '/home/mikel/.herdr/worktrees/predictive-models/feat-training-devin';
const app = path.join(root, 'apps/app');
const output = path.dirname(fileURLToPath(import.meta.url));
const delivery = path.dirname(output);
const target = 'a3891095f07a0fd0c435132d2bc560e8970c79f2e9bc8945fe02b0a36affacbf';
const cache = path.join(app, 'tsconfig.tsbuildinfo');
const digest = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const json = file => JSON.parse(fs.readFileSync(file, 'utf8'));
const rel = file => path.relative(root, file).split(path.sep).join('/');
const requireApp = createRequire(path.join(app, 'package.json'));
const ts = requireApp('typescript');
const transition = json(path.join(delivery, 'transition.json'));
const after = json(path.join(delivery, 'source-after-blocked.json'));
function ensure(ok, message) { if (!ok) throw new Error(message); }
function write(name, bytes) {
  ensure(path.basename(name) === name, 'Output must be directly inside recovery directory');
  fs.writeFileSync(path.join(output, name), bytes, { flag: 'wx' });
}
function report(name, value) { write(name, JSON.stringify(value, null, 2) + '\n'); }
function fileInfo(file) {
  const bytes = fs.readFileSync(file);
  return { path: rel(file), bytes: bytes.length, sha256: digest(bytes) };
}
function appFiles(directory) {
  const found = [];
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    if (entry.isSymbolicLink() || ['node_modules', '.git', '.next'].includes(entry.name)) continue;
    const file = path.join(directory, entry.name);
    if (entry.isDirectory()) found.push(...appFiles(file));
    else if (/\.(tsx?|mjs|json|css)$/.test(entry.name)) found.push(file);
  }
  return found;
}
function protectedFiles() {
  const files = new Set([
    cache, path.join(delivery, 'transition.json'), path.join(delivery, 'transition-pin.json'),
    path.join(delivery, 'source-after-blocked.json'), path.join(delivery, 'verification.json'),
    path.join(delivery, '../manifest.json'), path.join(delivery, '../model-v1/manifest.json'),
    path.join(root, '.devin/goal.md'), ...Object.keys(after.sha256).map(p => path.join(root, p)),
    ...Object.keys(transition.before).map(p => path.join(delivery, 'source-before', p)),
    ...appFiles(app),
    requireApp.resolve('typescript'), requireApp.resolve('typescript/package.json'), requireApp.resolve('next/package.json'),
  ]);
  for (const dir of ['.next/types', '.next/dev/types']) {
    for (const name of fs.readdirSync(path.join(app, dir))) files.add(path.join(app, dir, name));
  }
  return Object.fromEntries([...files].sort().map(file => [rel(file), fileInfo(file)]));
}
function preservation(before) {
  const current = protectedFiles();
  const diffs = Object.keys(before).filter(p => !current[p] || current[p].sha256 !== before[p].sha256);
  return { unchanged: !diffs.length, differences: diffs, hashes: current };
}
function summarizeBuildInfo(file) {
  const info = fileInfo(file);
  let value;
  try { value = json(file); } catch { return { ...info, parseable_build_info: false }; }
  if (!Array.isArray(value.fileNames)) return { ...info, parseable_build_info: false };
  const fileVersion = index => typeof value.fileInfos[index] === 'string' ? value.fileInfos[index] : value.fileInfos[index]?.version;
  const roots = (value.root ?? []).flatMap(r => Array.isArray(r) ? Array.from({ length: r[1] - r[0] + 1 }, (_, i) => i + r[0]) : [r]);
  return { ...info, parseable_build_info: true, version: value.version, keys: Object.keys(value), options: value.options,
    file_count: value.fileNames.length, roots: roots.map(i => value.fileNames[i - 1]),
    app_entries: value.fileNames.map((p, i) => ({ id: i + 1, path: p, version: fileVersion(i), info: value.fileInfos[i] })).filter(p => !p.path.includes('node_modules')),
    diagnostics: value.semanticDiagnosticsPerFile?.filter(Array.isArray) ?? [],
    emit_signatures: value.emitSignatures, pending_emit: value.affectedFilesPendingEmit,
  };
}
function scan() {
  const roots = ['apps/app', 'apps/landing', 'reports/modeling'];
  const candidates = [];
  let entriesVisited = 0;
  function walk(directory) {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      entriesVisited++;
      if (entry.isSymbolicLink() || ['node_modules', '.git', 'cache-recovery-1'].includes(entry.name)) continue;
      const file = path.join(directory, entry.name);
      if (entry.isDirectory()) walk(file);
      else if (/tsbuildinfo/i.test(entry.name)) candidates.push(file);
    }
  }
  for (const directory of roots) walk(path.join(root, directory));
  return { roots, entries_visited: entriesVisited, symlinks_followed: false,
    content_read_scope: 'Only filenames containing tsbuildinfo, no unrelated cache secrets or data artifact contents',
    candidates: candidates.map(summarizeBuildInfo),
    exact_copies: candidates.filter(file => digest(fs.readFileSync(file)) === target).map(rel),
  };
}
function inspect() {
  ensure(!fs.existsSync(path.join(output, 'inspection.json')), 'Inspection already recorded');
  const before = protectedFiles();
  for (const [p, expected] of Object.entries(after.sha256)) ensure(before[p].sha256 === expected, 'Preexisting delivered source drift: ' + p);
  ensure(before[rel(cache)].sha256 === '8ecfc04867158213c1034082f48adf108d0a002a1aa92058617cb4334c4307fc', 'Unexpected working cache; stop');
  report('protected-before.json', before);
  const configuration = ts.getParsedCommandLineOfConfigFile(path.join(app, 'tsconfig.json'), { noEmit: true }, { ...ts.sys, onUnRecoverableConfigFileDiagnostic(d) { throw new Error(ts.flattenDiagnosticMessageText(d.messageText, '\n')); } });
  const value = { target_sha256: target, command: 'node reports/modeling/financial-v3/dataset-v1/delivery-v1/cache-recovery-1/recovery.mjs inspect',
    typescript_version: ts.version, next_version: requireApp('next/package.json').version,
    module_paths: { typescript: rel(requireApp.resolve('typescript')), next: rel(requireApp.resolve('next/package.json')) },
    scan: scan(), parsed_config: { options: configuration.options, root_names: configuration.fileNames.map(rel), errors: configuration.errors.map(d => ts.flattenDiagnosticMessageText(d.messageText, '\n')) },
    source_before_files: Object.keys(transition.before).filter(p => p.startsWith('apps/app/')),
    preservation: preservation(before), writes: 'Exclusive new recovery reports only; no compiler invoked',
  };
  report('inspection.json', value);
  console.log(JSON.stringify(value, null, 2));
}
function compilerSource() {
  const source = fs.readFileSync(requireApp.resolve('typescript'), 'utf8');
  const symbols = ['performIncrementalCompilation', 'createIncrementalProgram', 'emitFilesAndReportErrorsAndGetExitStatus', 'emitFilesAndReportErrors'];
  const snippets = symbols.map(name => {
    const offset = source.indexOf('function ' + name + '(');
    ensure(offset >= 0, 'Installed compiler function missing: ' + name);
    return { name, line: source.slice(0, offset).split('\n').length, text: source.slice(offset, offset + 5500) };
  });
  const value = { source: fileInfo(requireApp.resolve('typescript')), exported_perform_incremental: typeof ts.performIncrementalCompilation, snippets };
  report('compiler-source-notes.json', value);
  console.log(JSON.stringify(value, null, 2));
}
function reconstruct() {
  ensure(!fs.existsSync(path.join(output, 'candidate-1-plan.json')), 'Reconstruction attempt already consumed');
  const before = json(path.join(output, 'protected-before.json'));
  ensure(preservation(before).unchanged, 'Protected files changed since inspection');
  const excluded = new Set(after.new_source_files.filter(p => p.startsWith('apps/app/') && /\.tsx?$/.test(p)).map(p => path.join(root, p)));
  ensure(excluded.size === 3, 'Unexpected new TypeScript source inventory');
  const overrides = new Map();
  for (const [p, binding] of Object.entries(transition.before)) {
    if (!p.startsWith('apps/app/')) continue;
    const bytes = fs.readFileSync(path.join(delivery, 'source-before', p));
    ensure(digest(bytes) === binding.sha256 && transition.historical_bindings[p] === binding.sha256, 'Before snapshot binding mismatch: ' + p);
    overrides.set(path.join(root, p), bytes.toString('utf8'));
  }
  for (const file of appFiles(app)) {
    const p = rel(file);
    if (excluded.has(file) || overrides.has(file)) continue;
    ensure(transition.historical_bindings[p] === digest(fs.readFileSync(file)), 'Unchanged historical app source mismatch: ' + p);
  }
  const seed = fs.readFileSync(cache);
  const seedInfo = JSON.parse(seed.toString());
  const envPath = path.join(app, 'next-env.d.ts');
  const env = fs.readFileSync(envPath, 'utf8').replaceAll('./.next/dev/types/', './.next/types/');
  const recordedEnvIndex = seedInfo.fileNames.indexOf('./next-env.d.ts');
  const recordedEnv = seedInfo.fileInfos[recordedEnvIndex];
  ensure(digest(env) === (typeof recordedEnv === 'string' ? recordedEnv : recordedEnv.version), 'Modeled next typegen declaration differs from observed compiler state');
  overrides.set(envPath, env);
  overrides.set(cache, seed.toString());
  const generatedTypes = seedInfo.fileNames.map((name, i) => ({ name, info: seedInfo.fileInfos[i] })).filter(e => e.name.startsWith('./.next/')).map(e => {
    const file = path.join(app, e.name);
    const expected = typeof e.info === 'string' ? e.info : e.info.version;
    const actual = digest(fs.readFileSync(file));
    ensure(actual === expected, 'Generated type bytes changed since recorded compiler input: ' + e.name);
    return { path: rel(file), sha256: actual };
  });
  const emitted = [];
  const readHashes = new Map();
  const logs = [];
  const inRoot = file => file === root || file.startsWith(root + path.sep);
  const resolve = file => path.resolve(app, file);
  const allowed = file => inRoot(resolve(file)) && !excluded.has(resolve(file));
  const system = {
    ...ts.sys,
    getCurrentDirectory: () => app,
    write: s => logs.push(s),
    getEnvironmentVariable: () => '',
    fileExists: file => allowed(file) && ts.sys.fileExists(resolve(file)),
    directoryExists: file => inRoot(resolve(file)) && ts.sys.directoryExists(resolve(file)),
    getDirectories: file => inRoot(resolve(file)) ? ts.sys.getDirectories(resolve(file)) : [],
    readDirectory: (directory, ...args) => inRoot(resolve(directory)) ? ts.sys.readDirectory(resolve(directory), ...args).filter(allowed) : [],
    realpath: file => {
      const resolved = ts.sys.realpath(resolve(file));
      ensure(inRoot(resolved), 'Compiler resolution outside workspace');
      return resolved;
    },
    readFile: (file, encoding) => {
      file = resolve(file);
      if (!allowed(file)) return undefined;
      if (overrides.has(file)) {
        const content = overrides.get(file);
        readHashes.set(rel(file), { sha256: digest(content), virtual_override: true });
        return content;
      }
      if (ts.sys.fileExists(file)) ensure(inRoot(fs.realpathSync(file)), 'Read outside workspace through symlink');
      const content = ts.sys.readFile(file, encoding);
      if (content !== undefined) readHashes.set(rel(file), { sha256: digest(content), virtual_override: false });
      return content;
    },
    createDirectory: () => { throw new Error('Real directory write forbidden'); },
    deleteFile: () => { throw new Error('Real deletion forbidden'); },
    setModifiedTime: () => { throw new Error('Real timestamp write forbidden'); },
    writeFile: (file, content, bom) => {
      ensure(resolve(file) === cache && emitted.length === 0, 'Unexpected compiler write: ' + file);
      emitted.push({ virtual_path: file, bytes: Buffer.from((bom ? '\ufeff' : '') + content) });
    },
  };
  const configuration = ts.getParsedCommandLineOfConfigFile(path.join(app, 'tsconfig.json'), { noEmit: true }, {
    ...system, onUnRecoverableConfigFileDiagnostic(d) { throw new Error(ts.flattenDiagnosticMessageText(d.messageText, '\n')); },
  });
  ensure(!configuration.errors.length, 'Historical virtual tsconfig parse errors');
  ensure(configuration.fileNames.every(p => !excluded.has(p)), 'New files leaked into historical roots');
  const plan = {
    command: 'node reports/modeling/financial-v3/dataset-v1/delivery-v1/cache-recovery-1/recovery.mjs reconstruct',
    method: 'Single TypeScript performIncrementalCompilation with immutable workspace virtual paths, source-before read overrides, current incremental seed, original config/include order, and intercepted build-info-only output. No actual app/config/cache writes.',
    target_sha256: target, seed: fileInfo(cache), typescript_version: ts.version,
    configuration: { options: configuration.options, root_names: configuration.fileNames.map(rel) },
    excluded_new_v3_files: [...excluded].map(rel),
    virtual_source_overrides: [...overrides].filter(([p]) => p !== cache).map(([p, text]) => ({ path: rel(p), bytes: Buffer.byteLength(text), sha256: digest(text) })),
    next_env_model: 'CLI next typegen regenerates production .next/types imports before tsc. Modeled bytes match version already present in current root compiler cache; working dev next-env file is untouched.',
    generated_type_inputs: generatedTypes,
    budget: { candidate_generations: 1, correction_generations_available_only_for_understood_difference: 1 },
    helper: fileInfo(fileURLToPath(import.meta.url)),
  };
  report('candidate-1-plan.json', plan);
  write('seed-current.tsbuildinfo', seed);
  const diagnostics = [];
  let programInputs = [];
  let exitCode;
  let error;
  try {
    exitCode = ts.performIncrementalCompilation({ rootNames: configuration.fileNames, options: configuration.options,
      configFileParsingDiagnostics: configuration.errors, projectReferences: configuration.projectReferences, system,
      reportDiagnostic: d => diagnostics.push({ code: d.code, file: d.file ? rel(d.file.fileName) : null, start: d.start, message: ts.flattenDiagnosticMessageText(d.messageText, '\n') }),
      afterProgramEmitAndDiagnostics: program => { programInputs = program.getProgram().getSourceFiles().map(f => rel(f.fileName)); },
    });
  } catch (e) { error = String(e); }
  let candidate = null;
  if (emitted.length === 1) {
    write('candidate-1.tsbuildinfo', emitted[0].bytes);
    candidate = { path: rel(path.join(output, 'candidate-1.tsbuildinfo')), bytes: emitted[0].bytes.length, sha256: digest(emitted[0].bytes), matches_historical_target: digest(emitted[0].bytes) === target };
  }
  const result = { status: candidate?.matches_historical_target ? 'EXACT_CANDIDATE_RECOVERED_NOT_APPLIED' : 'candidate_not_exact_or_compilation_failed', compiler_exit_code: exitCode, error, diagnostics, candidate,
    emitted_virtual_paths: emitted.map(e => e.virtual_path), program_inputs: programInputs, input_read_hashes: Object.fromEntries(readHashes), compiler_output: logs,
    preservation: preservation(before), restoration_performed: false, source_outcome_queries: 0, inference_or_model_execution: 0,
  };
  report('candidate-1-result.json', result);
  console.log(JSON.stringify({ ...result, input_read_hashes: { count: readHashes.size, full: 'candidate-1-result.json' }, program_inputs: { count: programInputs.length, full: 'candidate-1-result.json' }, preservation: { unchanged: result.preservation.unchanged, differences: result.preservation.differences } }, null, 2));
  ensure(result.preservation.unchanged, 'Protected workspace file changed');
  if (!candidate?.matches_historical_target) process.exitCode = 2;
}
const action = process.argv[2];
ensure(process.cwd() === root, 'Use authorized workspace cwd');
if (action === 'inspect') inspect();
else if (action === 'compiler-source') compilerSource();
else if (action === 'reconstruct') reconstruct();
else throw new Error('Unknown action');
