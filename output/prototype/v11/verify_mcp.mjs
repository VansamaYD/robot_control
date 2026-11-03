import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';

const root = path.dirname(fileURLToPath(import.meta.url));
const base = process.env.WORKBENCH_URL || 'http://127.0.0.1:8765';
const report = { tests: [], scope: 'official SDK stdio client -> adapter -> shared simulator', status: 'running' };
const clients = [];
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function connect() {
  const transport = new StdioClientTransport({ command: process.execPath, args: [path.join(root, 'mcp_server.mjs')], env: { WORKBENCH_URL: base }, stderr: 'pipe' });
  const client = new Client({ name: 'robot-control-integration-test', version: '0.13.0' }, { capabilities: {} });
  clients.push(client); await client.connect(transport); return client;
}
async function raw(client, name, args = {}) { return client.callTool({ name, arguments: args }, undefined, { timeout: 45000 }); }
async function call(client, name, args = {}) { const value = await raw(client, name, args); assert(!value.isError, JSON.stringify(value)); return value.structuredContent; }
async function check(name, fn) { const details = await fn(); report.tests.push({ name, status: 'passed', details }); console.log('PASS', name); }

try {
  const client = await connect();
  const bootstrap = await (await fetch(base + '/api/bootstrap')).json();
  const saved = await (await fetch(base + '/api/projects', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Session-Token': bootstrap.token }, body: JSON.stringify({ name: 'MCP闭环测试', actions: [{ kind: 'wait', name: 'MCP等待', duration_s: 30 }] }) })).json();
  const programs = await call(client, 'list_programs');
  await check('initialize_discover_and_read_without_credentials_leak', async () => {
    const tools = await client.listTools(); assert.equal(tools.tools.length, 12);
    const capabilities = await call(client, 'get_capabilities'); assert(capabilities.mcp_stdio); assert.equal(capabilities.hardware, false);
    assert(programs.programs.some(p => p.revision === saved.revision));
    const scene = await call(client, 'get_scene'); assert.equal(scene.model.joint_order.length, 5);
    assert(!JSON.stringify(scene).includes(bootstrap.token));
    return { tools: tools.tools.map(t => t.name) };
  });
  await check('validate_saved_program_without_motion', async () => {
    const before = await call(client, 'get_robot_state');
    const validated = await call(client, 'validate_program', { program_revision: saved.revision });
    const after = await call(client, 'get_robot_state');
    assert.equal(validated.level, 'kinematic_path'); assert.equal(validated.liquid_validation, false);
    assert.equal(before.tick, after.tick); assert.deepEqual(before.q_rad, after.q_rad);
    return { samples: validated.samples };
  });
  const request = { program_revision: saved.revision, idempotency_key: 'mcp-' + crypto.randomUUID() };
  const job = await call(client, 'run_program', request);
  const retry = await call(client, 'run_program', request);
  assert.equal(job.job_id, retry.job_id);
  await call(client, 'pause_job', job);
  let snapshot = await call(client, 'get_robot_state');
  await check('shared_job_idempotency_owner_and_pause', async () => {
    const actual = await call(client, 'get_job', job); assert.equal(actual.origin, 'mcp');
    assert(snapshot.paused); await sleep(150);
    assert.equal((await call(client, 'get_robot_state')).tick, snapshot.tick);
    return { job_id: job.job_id };
  });
  await check('render_real_png_at_pinned_state', async () => {
    const image = await raw(client, 'render_snapshot', { state_revision: snapshot.state_revision, incarnation: snapshot.incarnation, width: 1280, height: 720, view: 'side' });
    assert(!image.isError, JSON.stringify(image));
    const block = image.content.find(block => block.type === 'image'); assert(block);
    const buffer = Buffer.from(block.data, 'base64'); assert.equal(buffer.subarray(1, 4).toString(), 'PNG');
    assert.equal(buffer.readUInt32BE(16), 1280); assert.equal(buffer.readUInt32BE(20), 720);
    const meta = image.structuredContent;
    assert.equal(meta.state_revision, snapshot.state_revision); assert.deepEqual(meta.q_rad, snapshot.q_rad); assert.deepEqual(meta.liquid, snapshot.liquid);
    assert.equal(meta.image_sha256, crypto.createHash('sha256').update(buffer).digest('hex'));
    const after = await call(client, 'get_robot_state'); assert.equal(after.tick, snapshot.tick);
    fs.writeFileSync(path.join(root, 'evidence/mcp-snapshot.png'), buffer);
    fs.writeFileSync(path.join(root, 'evidence/mcp-image-metadata.json'), JSON.stringify(meta, null, 2));
    return { bytes: buffer.length, state_revision: meta.state_revision, image_sha256: meta.image_sha256 };
  });
  const second = await connect();
  await check('different_mcp_client_cannot_cancel_foreign_job', async () => {
    const denied = await raw(second, 'cancel_job', job); assert(denied.isError);
    assert.equal(denied.structuredContent.code, 'NOT_AUTHORIZED');
    assert.notEqual((await call(client, 'get_job', job)).status, 'cancelled'); return {};
  });
  await call(client, 'resume_job', job); await call(client, 'cancel_job', job);
  const next = await call(client, 'run_program', { ...request, idempotency_key: crypto.randomUUID() });
  await check('old_cancel_and_foreign_manual_cannot_interrupt_current_job', async () => {
    await call(client, 'cancel_job', job);
    assert.notEqual((await call(client, 'get_job', next)).status, 'cancelled');
    const current = await call(client, 'get_robot_state');
    const response = await fetch(base + '/api/manual', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Session-Token': bootstrap.token }, body: JSON.stringify({ joint_name: 'shoulder_pan', delta_deg: 1, expected_state_revision: current.state_revision, idempotency_key: crypto.randomUUID() }) });
    assert(!response.ok); assert(['CONTROL_BUSY', 'START_STATE_CHANGED'].includes((await response.json()).error));
    await call(client, 'cancel_job', next);
    return {};
  });
  await check('unknown_fields_and_missing_snapshot_fail_explicitly', async () => {
    const bad = await raw(client, 'get_robot_state', { code: 'do not execute' }); assert(bad.isError);
    assert(bad.structuredContent.code.startsWith('UNKNOWN_ARGUMENT'));
    const missing = await raw(client, 'render_snapshot', { state_revision: 999999999, incarnation: snapshot.incarnation });
    assert(missing.isError); assert.equal(missing.structuredContent.code, 'SNAPSHOT_EXPIRED');
    const stale = await raw(client, 'render_snapshot', { state_revision: snapshot.state_revision, incarnation: 'previous-boot' });
    assert(stale.isError); assert.equal(stale.structuredContent.code, 'SESSION_CHANGED');
    return {};
  });
  report.status = 'passed';
} catch (error) { report.status = 'failed'; report.error = error.stack; console.error(error); process.exitCode = 1; }
finally {
  for (const client of clients) await client.close();
  fs.writeFileSync(path.join(root, 'evidence/mcp-report.json'), JSON.stringify(report, null, 2));
}
