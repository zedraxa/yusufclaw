import { randomUUID } from 'node:crypto';
import { z } from 'zod';
import type { ModelProvider, ModelRequest, ModelResponse } from './types.js';
const responseSchema = z.object({
  done: z.literal(true),
  done_reason: z.string().optional(),
  message: z.object({ role: z.literal('assistant'), content: z.string(), tool_calls: z.array(z.object({
    function: z.object({ name: z.string().min(1), arguments: z.record(z.unknown()) }),
  })).optional() }),
});
export class OllamaProvider implements ModelProvider {
  constructor(private readonly options: { model: string; baseUrl?: string; maxTokens?: number; timeoutMs?: number }, private readonly fetcher: typeof fetch = fetch) {}
  async complete(request: ModelRequest): Promise<ModelResponse> {
    const timeout = AbortSignal.timeout(this.options.timeoutMs ?? 120_000);
    const response = await this.fetcher(new URL('api/chat', (this.options.baseUrl ?? 'http://127.0.0.1:11434').replace(/\/$/, '') + '/'), {
      method: 'POST', headers: { 'content-type': 'application/json' },
      signal: request.signal ? AbortSignal.any([request.signal, timeout]) : timeout,
      body: JSON.stringify({ model: this.options.model, stream: false, options: { num_predict: this.options.maxTokens ?? 2048 },
        messages: [{ role: 'system', content: request.system }, ...request.messages.map(message => {
          if (message.role === 'tool') return { role: 'tool', tool_name: message.name, content: message.text };
          if (message.role === 'user') return { role: 'user', content: message.text };
          return { role: 'assistant', content: message.text, ...(message.toolCalls?.length ? { tool_calls: message.toolCalls.map(call => ({ function: { name: call.name, arguments: call.input } })) } : {}) };
        })],
        ...(request.tools.length ? { tools: request.tools.map(tool => ({ type: 'function', function: { name: tool.name, description: tool.description, parameters: tool.inputSchema } })) } : {}),
      }),
    });
    if (!response.ok) throw new Error(`Ollama request failed (HTTP ${response.status})`);
    const data = responseSchema.parse(await response.json());
    if (data.done_reason === 'length') throw new Error('Ollama output token limit reached');
    const toolCalls = (data.message.tool_calls ?? []).map(call => ({ id: randomUUID(), name: call.function.name, input: call.function.arguments }));
    if (!data.message.content.trim() && !toolCalls.length) throw new Error('Ollama returned an empty response');
    return { text: data.message.content, toolCalls };
  }
}
