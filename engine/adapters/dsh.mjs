import { execFile } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { promisify } from 'node:util'

const run = promisify(execFile)
const settings = JSON.parse(readFileSync(new URL('./binding.json', import.meta.url), 'utf8'))
export const name = 'aiws'
export const inject = ['tools']
export function apply(ctx) {
  for (const tool of settings.tools) {
    ctx.tools.register({
      name: 'aiws_' + tool.name,
      description: tool.description || tool.name,
      parameters: tool.inputSchema,
      output: {
        schema: { type: 'string' },
        render: (_args, value) => [{ type: 'text', text: value }],
      },
      async execute(args, execution) {
        const { stdout } = await run(settings.command,
          ['--workspace', settings.workspace, 'call', tool.name, '--params', JSON.stringify(args)],
          { signal: execution.signal, maxBuffer: 8 * 1024 * 1024 })
        return stdout.trim()
      },
    })
  }
}
