import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

const runtime = new URL('../runtime/node_modules/@deepseek-ai/', import.meta.url)
const installed = existsSync(new URL('dsh-tools/lib/index.js', runtime))

test('native DSH 0.2.2 runtime mounts and validates all bridge tools', { skip: !installed }, async () => {
  process.env.DSH_HOST_MODULES = fileURLToPath(runtime)
  const [{ Context }, { default: SystemPrompt }, { default: ToolsService }, plugin] = await Promise.all([
    import(new URL('cordis/lib/index.js', runtime)),
    import(new URL('dsh-system-prompt/lib/index.js', runtime)),
    import(new URL('dsh-tools/lib/index.js', runtime)),
    import('../index.js'),
  ])
  const ctx = new Context()
  await ctx.plugin(SystemPrompt, {})
  await ctx.plugin(ToolsService)
  await ctx.plugin(plugin, { baseUrl: 'http://127.0.0.1:8765' })
  try {
    assert.equal(ctx.tools.definitions().length, 8)
    const tool = ctx.tools.get('cdv_chat')
    assert.equal(tool.parameters.type, 'object')
    assert.equal(tool.parameters.properties.message.type, 'string')
    assert.deepEqual(tool.parameters.required, ['goal_id', 'message'])
    assert.ok(tool.output.render({}, { result: {} })[0].text)
  } finally { await ctx.fiber.dispose() }
})
