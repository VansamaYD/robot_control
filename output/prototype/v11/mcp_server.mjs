// Real stdio MCP adapter. No model text, Python or shell command is executed.
import { Server } from '@modelcontextprotocol/sdk/server/index.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { ListToolsRequestSchema, CallToolRequestSchema } from '@modelcontextprotocol/sdk/types.js';

const target = new URL(process.env.WORKBENCH_URL || 'http://127.0.0.1:8765/');
if (target.protocol !== 'http:' || !['127.0.0.1', 'localhost'].includes(target.hostname) || target.pathname !== '/' || target.username || target.password || target.search || target.hash) throw Error('WORKBENCH_URL must be a loopback HTTP origin');
const base = target.origin;
let identityPromise;
async function api(path, body, token) {
  const response = await fetch(base + path, { method: body === undefined ? 'GET' : 'POST',
    headers: body === undefined ? {} : { 'Content-Type': 'application/json', 'X-Session-Token': token },
    body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(35000) });
  const value = await response.json();
  if (!response.ok) throw Error(value.error || 'SERVICE_ERROR');
  return value;
}
async function identity() {
  identityPromise ??= (async () => {
    const bootstrap = await api('/api/bootstrap');
    return api('/api/clients', {}, bootstrap.token);
  })();
  return identityPromise;
}
async function write(path, body) { return api(path, body, (await identity()).token); }
const string = { type: 'string', minLength: 1, maxLength: 200 };
const revision = { ...string, minLength: 64, maxLength: 64 };
const integer = { type: 'integer', minimum: 1 };
const tools = [];
function tool(name, description, properties, required, execute) {
  tools.push({ definition: { name, description, inputSchema: { type: 'object', properties, required, additionalProperties: false } }, execute });
}
tool('get_capabilities', 'Read actual simulator capabilities; hardware and arbitrary Python are unavailable.', {}, [], () => api('/api/capabilities'));
tool('get_scene', 'Read shared scene geometry and robot model. SI units; simulation only.', {}, [], async () => {
  const { model, scene, pour_zones } = await api('/api/bootstrap'); return { model, scene, pour_zones };
});
tool('get_robot_state', 'Read latest state, or a historical revision with its incarnation. SI units and explicit source/quality.', { state_revision: integer, incarnation: string }, [], a => {
  if (a.state_revision && !a.incarnation) throw Error('MISSING_ARGUMENT:incarnation');
  const query = new URLSearchParams();
  if (a.state_revision) query.set('revision', a.state_revision);
  if (a.incarnation) query.set('incarnation', a.incarnation);
  return api('/api/state?' + query);
});
tool('list_programs', 'List saved immutable recipe versions.', {}, [], () => api('/api/projects'));
tool('get_program_revision', 'Read a fixed recipe and generated code view; no code is executed.', { program_revision: revision }, ['program_revision'], a => api('/api/projects/' + encodeURIComponent(a.program_revision)));
tool('validate_program', 'Check kinematic paths from a captured start state. Does not move or validate fluid availability; execution checks again.', { program_revision: revision }, ['program_revision'], a => write('/api/validate', a));
tool('run_program', 'Start a saved version as an asynchronous job. Reuse the same idempotency key when retrying the same request.', { program_revision: revision, idempotency_key: string, configuration_revision: string }, ['program_revision', 'idempotency_key'], a => write('/api/run', a));
tool('get_job', 'Read a job by its actual ID, including terminal jobs.', { job_id: string }, ['job_id'], a => api('/api/jobs/' + encodeURIComponent(a.job_id)));
for (const command of ['cancel', 'pause', 'resume']) {
  tool(command + '_job', command + ' a job owned by this MCP adapter. A stale job ID cannot affect a newer job.', { job_id: string }, ['job_id'], a => write('/api/control', { job_id: a.job_id, command }));
}
tool('render_snapshot', 'Render a pinned historical snapshot as PNG using a separate headless browser. Does not advance simulation or change the UI camera.', {
  state_revision: integer, incarnation: string, view: { type: 'string', enum: ['default', 'top', 'side', 'rack'] },
  width: { type: 'integer', minimum: 320, maximum: 1920 }, height: { type: 'integer', minimum: 240, maximum: 1080 }
}, ['state_revision', 'incarnation'], a => write('/api/images', a));

function validate(schema, args) {
  if (!args || typeof args !== 'object' || Array.isArray(args)) throw Error('INVALID_ARGUMENT');
  for (const key of schema.required) if (!(key in args)) throw Error('MISSING_ARGUMENT:' + key);
  for (const [key, value] of Object.entries(args)) {
    const property = schema.properties[key];
    if (!property) throw Error('UNKNOWN_ARGUMENT:' + key);
    if (property.type === 'integer' && (!Number.isInteger(value) || !Number.isFinite(value))) throw Error('INVALID_ARGUMENT:' + key);
    if (property.type === 'string' && typeof value !== 'string') throw Error('INVALID_ARGUMENT:' + key);
    if (property.minLength && value.length < property.minLength || property.maxLength && value.length > property.maxLength || property.minimum !== undefined && value < property.minimum || property.maximum !== undefined && value > property.maximum || property.enum && !property.enum.includes(value)) throw Error('INVALID_ARGUMENT:' + key);
  }
}
const server = new Server({ name: 'robot-control-simulation', version: '0.19.0' }, { capabilities: { tools: {} } });
server.setRequestHandler(ListToolsRequestSchema, async () => ({ tools: tools.map(t => t.definition) }));
server.setRequestHandler(CallToolRequestSchema, async request => {
  try {
    const entry = tools.find(t => t.definition.name === request.params.name);
    if (!entry) throw Error('UNKNOWN_TOOL');
    const args = request.params.arguments ?? {};
    validate(entry.definition.inputSchema, args);
    const raw = await entry.execute(args);
    const result = Array.isArray(raw) ? { programs: raw } : raw;
    if (entry.definition.name === 'render_snapshot') return { content: [
      { type: 'image', data: result.data, mimeType: result.mimeType },
      { type: 'text', text: JSON.stringify(result.metadata) }
    ], structuredContent: result.metadata };
    return { content: [{ type: 'text', text: JSON.stringify(result) }], structuredContent: result };
  } catch (error) {
    const result = { code: error.message, source: 'simulation', success: false };
    return { isError: true, content: [{ type: 'text', text: JSON.stringify(result) }], structuredContent: result };
  }
});
await server.connect(new StdioServerTransport());
