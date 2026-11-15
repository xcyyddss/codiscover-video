import test from 'node:test'
import assert from 'node:assert/strict'
import http from 'node:http'
import { apply, localBaseUrl } from '../index.js'

test('bridge accepts only loopback origins', () => {
  assert.equal(localBaseUrl('http://127.0.0.1:8765'), 'http://127.0.0.1:8765')
  for (const url of ['https://example.com', 'http://evil.test', 'http://localhost/a', 'http://user:pass@localhost']) {
    assert.throws(() => localBaseUrl(url))
  }
})

test('registers eight native tools and invokes local status API', async () => {
  const server = http.createServer((req, res) => {
    assert.equal(req.url, '/api/status')
    res.writeHead(200, { 'Content-Type': 'application/json' })
    res.end(JSON.stringify({ mode: 'demo', counts: { videos: 12 } }))
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  try {
    const specs = []
    apply({ tools: { register: spec => specs.push(spec) } }, { baseUrl: `http://127.0.0.1:${server.address().port}` })
    assert.equal(specs.length, 8)
    assert.equal(specs.some(t => /reply|message|upload|delete|shell/.test(t.name)), false)
    const result = await specs.find(t => t.name === 'cdv_status').execute({})
    assert.equal(result.result.mode, 'demo')
  } finally { await new Promise(resolve => server.close(resolve)) }
})
