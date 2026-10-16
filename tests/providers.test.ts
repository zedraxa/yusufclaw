import { describe, expect, it, vi } from 'vitest';
import { BedrockProvider, OllamaProvider, type ModelRequest } from '../packages/models/dist/index.js';
import type { BedrockTransport } from '../packages/models/dist/bedrock.js';
const request: ModelRequest = { system: 'system', messages: [{ role: 'user', text: 'hello' }], tools: [{ name: 'clock', description: 'Time', inputSchema: { type: 'object', properties: {} } }] };
describe('Bedrock', () => {
  it('maps Converse requests, multiple tool results, and responses', async () => {
    const send = vi.fn<BedrockTransport['send']>().mockResolvedValue({ $metadata: {}, usage: undefined, metrics: undefined, stopReason: 'tool_use', output: { message: { role: 'assistant', content: [{ text: 'checking' }, { toolUse: { toolUseId: '1', name: 'clock', input: {} } }] } } });
    const provider = new BedrockProvider({ model: 'test-model', maxTokens: 123 }, { send });
    expect(await provider.complete(request)).toEqual({ text: 'checking', toolCalls: [{ id: '1', name: 'clock', input: {} }] });
    await provider.complete({ ...request, messages: [...request.messages,
      { role: 'assistant', text: '', toolCalls: [{ id: '1', name: 'clock', input: {} }, { id: '2', name: 'clock', input: {} }] },
      { role: 'tool', callId: '1', name: 'clock', text: 'now' }, { role: 'tool', callId: '2', name: 'clock', text: 'denied', error: true },
    ] });
    const input = send.mock.calls[1][0].input;
    expect(input.modelId).toBe('test-model'); expect(input.inferenceConfig?.maxTokens).toBe(123);
    expect(input.messages).toHaveLength(3); expect(input.messages?.[2].content).toHaveLength(2);
    expect(input.messages?.[2].content?.[1].toolResult?.status).toBe('error');
    expect(input.toolConfig?.tools?.[0].toolSpec?.name).toBe('clock');
  });
  it('omits empty tool configs and rejects truncation and empty responses', async () => {
    const send = vi.fn<BedrockTransport['send']>().mockResolvedValueOnce({ $metadata: {}, usage: undefined, metrics: undefined, output: undefined, stopReason: 'max_tokens' }).mockResolvedValueOnce({ $metadata: {}, usage: undefined, metrics: undefined, output: undefined, stopReason: undefined });
    const provider = new BedrockProvider({ model: 'test' }, { send });
    await expect(provider.complete({ ...request, tools: [] })).rejects.toThrow('max_tokens');
    expect(send.mock.calls[0][0].input.toolConfig).toBeUndefined();
    await expect(provider.complete(request)).rejects.toThrow('no message');
  });
});
describe('Ollama', () => {
  it('uses nonstreaming chat, maps tool calls, and preserves history', async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async () => new Response(JSON.stringify({ done: true, message: { role: 'assistant', content: '', tool_calls: [{ function: { name: 'clock', arguments: {} } }] } })));
    const provider = new OllamaProvider({ model: 'local', maxTokens: 123 }, fetcher);
    const result = await provider.complete(request);
    expect(result.toolCalls[0]).toMatchObject({ name: 'clock', input: {} }); expect(result.toolCalls[0].id).toBeTruthy();
    await provider.complete({ ...request, messages: [...request.messages, { role: 'assistant', text: '', toolCalls: result.toolCalls }, { role: 'tool', callId: result.toolCalls[0].id, name: 'clock', text: 'now' }] });
    const body = JSON.parse(fetcher.mock.calls[1][1]?.body as string);
    expect(body.stream).toBe(false); expect(body.options.num_predict).toBe(123);
    expect(body.messages.at(-1)).toEqual({ role: 'tool', tool_name: 'clock', content: 'now' });
    expect(body.tools[0].function.parameters).toEqual(request.tools[0].inputSchema);
    expect(String(fetcher.mock.calls[0][0])).toBe('http://127.0.0.1:11434/api/chat');
  });
  it.each([
    [new Response('sensitive body', { status: 500 }), 'HTTP 500'],
    [new Response('{}'), 'Required'],
    [new Response(JSON.stringify({ done: true, message: { role: 'assistant', content: '' } })), 'empty'],
    [new Response(JSON.stringify({ done: true, done_reason: 'length', message: { role: 'assistant', content: 'partial' } })), 'token limit'],
  ])('rejects unsuccessful or malformed responses', async (response, error) => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response);
    await expect(new OllamaProvider({ model: 'local' }, fetcher).complete(request)).rejects.toThrow(error);
  });
});
