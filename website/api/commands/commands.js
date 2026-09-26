// api/commands.js
// Deployed by Vercel at /api/commands, and rewritten to /commands by vercel.json.
//
// GET  /commands              -> list every command (optionally filter with ?category= or ?name=)
// POST /commands               -> add a new command (see body shape below)
// PUT  /commands               -> replace full command list or update usage counts
//                               body: { commands?: [...], usage?: [{ name, count }] }
//
// NOTE ON PERSISTENCE:
// Vercel serverless functions run in stateless, ephemeral containers, and the
// deployed filesystem is read-only. Runtime state is kept in memory and WILL
// be lost on the next cold start or redeploy. For permanent storage, swap
// `runtimeCommands` out for a real store (Vercel KV, Postgres, MongoDB, etc.).

const commandsData = require('../commands.json');

let runtimeCommands = [...commandsData.commands];

function mergeCommand(target, source) {
  target.name = source.name || target.name;
  target.usage_count = source.usage_count !== undefined ? source.usage_count : target.usage_count;
  target.category = source.category !== undefined ? source.category : target.category;
  target.description = source.description !== undefined ? source.description : target.description;
  target.arguments = Array.isArray(source.arguments) ? source.arguments : target.arguments;
  target.permissions = Array.isArray(source.permissions) ? source.permissions : target.permissions;
}

module.exports = (req, res) => {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, PUT, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');

  if (req.method === 'OPTIONS') {
    res.status(204).end();
    return;
  }

  if (req.method === 'GET') {
    const { category, name } = req.query;
    let results = runtimeCommands;

    if (category) {
      results = results.filter(
        (c) => c.category.toLowerCase() === String(category).toLowerCase()
      );
    }
    if (name) {
      results = results.filter((c) =>
        c.name.toLowerCase().includes(String(name).toLowerCase())
      );
    }

    res.status(200).json({ count: results.length, commands: results });
    return;
  }

  if (req.method === 'POST') {
    const body = req.body || {};
    const { name, description, category, arguments: args, permissions } = body;

    if (!name || typeof name !== 'string') {
      res.status(400).json({ error: '"name" is required and must be a string.' });
      return;
    }
    if (!description || typeof description !== 'string') {
      res.status(400).json({ error: '"description" is required and must be a string.' });
      return;
    }
    if (args && !Array.isArray(args)) {
      res.status(400).json({ error: '"arguments" must be an array of { name, description, required }.' });
      return;
    }
    if (permissions && !Array.isArray(permissions)) {
      res.status(400).json({ error: '"permissions" must be an array of permission name strings.' });
      return;
    }

    const exists = runtimeCommands.some(
      (c) => c.name.toLowerCase() === name.toLowerCase()
    );
    if (exists) {
      res.status(409).json({ error: `A command named "${name}" already exists.` });
      return;
    }

    const newCommand = {
      name: name.toLowerCase(),
      description,
      category: category || 'uncategorized',
      arguments: (args || []).map((a) => ({
        name: a.name,
        description: a.description || '',
        required: Boolean(a.required),
      })),
      permissions: permissions || [],
    };

    runtimeCommands.push(newCommand);

    res.status(201).json({
      message:
        'Command added. This is held in memory for this server instance only — ' +
        'it will not survive a cold start or redeploy. Connect a real database ' +
        'to persist it permanently.',
      command: newCommand,
    });
    return;
  }

  if (req.method === 'PUT') {
    const body = req.body || {};
    const { commands: incomingCommands, usage } = body;

    if (Array.isArray(incomingCommands)) {
      const index = new Map();
      for (let i = 0; i < runtimeCommands.length; i += 1) {
        index.set(runtimeCommands[i].name.toLowerCase(), i);
      }

      const updatedNames = [];
      incomingCommands.forEach((cmd) => {
        const key = String(cmd.name || '').toLowerCase();
        const i = index.get(key);
        if (i !== undefined) {
          mergeCommand(runtimeCommands[i], cmd);
          updatedNames.push(runtimeCommands[i].name);
        }
      });

      res.status(200).json({ updated: updatedNames, mode: 'commands' });
      return;
    }

    if (Array.isArray(usage)) {
      const index = new Map();
      for (let i = 0; i < runtimeCommands.length; i += 1) {
        index.set(runtimeCommands[i].name.toLowerCase(), i);
      }

      const updated = [];
      usage.forEach((entry) => {
        const key = String(entry.name || '').toLowerCase();
        const count = typeof entry.count === 'number' ? entry.count : 0;
        const i = index.get(key);
        if (i !== undefined) {
          runtimeCommands[i].usage_count = count;
          updated.push(runtimeCommands[i].name);
        }
      });

      res.status(200).json({ updated, mode: 'usage' });
      return;
    }

    res.status(400).json({ error: 'PUT body must include either "commands" or "usage".' });
    return;
  }

  res.setHeader('Allow', 'GET, POST, PUT, OPTIONS');
  res.status(405).json({ error: `Method ${req.method} not allowed.` });
};
