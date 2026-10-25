// Self-check (INFRA-996): the running-orphan logic embedded in
// k8s/synapse-pod-reaper.yaml must reap dead poll runners and spare live ones.
// Extracts the node script from the YAML by indentation (no yaml dep) and runs
// the instanceIsDead asserts against it. Run: node tests/synapse-reaper-selfcheck.mjs
import { readFileSync } from 'node:fs';
import assert from 'node:assert';
import vm from 'node:vm';

const yaml = readFileSync(new URL('../k8s/synapse-pod-reaper.yaml', import.meta.url), 'utf8');
const after = yaml.slice(yaml.indexOf('name: running-orphan-reaper'));
const lines = after.slice(after.indexOf('args:\n')).split('\n');
const body = [];
for (const line of lines.slice(2)) {
  if (line.trim() === '') { body.push(''); continue; }
  if (!line.startsWith(' '.repeat(18))) break;
  body.push(line.slice(18));
}
let script = body.join('\n')
  .replaceAll("fs.readFileSync('/var/run/secrets/kubernetes.io/serviceaccount/token', 'utf8')", "'t'")
  .replaceAll("fs.readFileSync('/var/run/secrets/kubernetes.io/serviceaccount/ca.crt')", "'c'");
script = script.slice(0, script.indexOf('main().catch'));

const H = (h) => new Date(Date.now() - h * 3600000).toISOString();
const tests = `
  const pod = { metadata: { creationTimestamp: ${JSON.stringify(H(3))} } };
  assert.strictEqual(instanceIsDead(null, pod), true);
  assert.strictEqual(instanceIsDead(JSON.stringify({status:{phase:'faulted',startedAt:${JSON.stringify(H(3))},endedAt:${JSON.stringify(H(3))}}}), pod), true);
  assert.strictEqual(instanceIsDead(JSON.stringify({status:{phase:'completed',startedAt:${JSON.stringify(H(3))},endedAt:${JSON.stringify(H(3))}}}), pod), true);
  assert.strictEqual(instanceIsDead(JSON.stringify({status:{phase:'running',startedAt:${JSON.stringify(H(3))}}}), pod), true);
  assert.strictEqual(instanceIsDead(JSON.stringify({status:{phase:'running',startedAt:${JSON.stringify(H(1))}}}), {metadata:{creationTimestamp:${JSON.stringify(H(1))}}}), false);
  assert.strictEqual(instanceIsDead(JSON.stringify({status:{phase:'suspended',startedAt:${JSON.stringify(H(0.5))}}}), pod), false);
  assert.strictEqual(instanceIsDead('no-json', pod), false);
  'SELFTEST_OK';
`;
const modules = {
  crypto: (await import('node:crypto')).default,
  fs: { readFileSync: () => '' },
  https: {},
  net: {},
};
const result = vm.runInNewContext(script + tests, {
  process: { env: { RUNNING_ORPHAN_MIN_AGE_HOURS: '2' }, exitCode: 0 },
  console: { log() {}, error() {} },
  require: (m) => modules[m],
  assert,
  Date,
  Number,
  JSON,
  Buffer,
});
console.log(result);
