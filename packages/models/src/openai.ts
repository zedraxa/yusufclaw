import { z } from 'zod';
import type { ModelProvider, ModelRequest, ModelResponse } from './types.js';

const responseSchema = z.object({ choices: z.array(z.object({
  finish_reason: z.enum(['stop', 'tool_calls']),
  message: z.object({ role: z.literal('assistant'), content: z.string().nullish(),
    tool_calls: z.array(z.object({ id: z.string().min(1), type: z.literal('function'),
      function: z.object({ name: z.string().min(1), arguments: z.string() }),
    })).nullish(),
  }),
})).length(1) });

/** Non-streaming OpenAI-compatible Chat Completions transport. */
export class OpenAIProvider implements ModelProvider {
  private readonly endpoint: URL;
  constructor(private readonly options: { baseUrl: string; apiKey: string; model: string; maxTokens?: number; timeoutMs?: number }, private readonly fetcher: typeof fetch = fetch) {
    try {
      const base = new URL(options.baseUrl);
      if (!['http:', 'https:'].includes(base.protocol) || base.username || base.password || base.search || base.hash) throw new Error();
      this.endpoint = new URL(`${base.pathname.replace(/\/$/, '')}/chat/completions`, base.origin);
      if (!options.apiKey.trim() || /[\r\n]/.test(options.apiKey) || !options.model.trim()) throw new Error();
    } catch { throw new Error('Invalid OpenAI-compatible provider configuration'); }
  }
  async complete(request: ModelRequest): Promise<ModelResponse> {
    const timeout = AbortSignal.timeout(this.options.timeoutMs ?? 120_000);
    const signal = request.signal ? AbortSignal.any([request.signal, timeout]) : timeout;
    let response: Response;
    try {
      response = await this.fetcher(this.endpoint, {
        method: 'POST', redirect: 'error', signal,
        headers: { 'content-type': 'application/json', authorization: `Bearer ${this.options.apiKey}` },
        body: JSON.stringify({ model: this.options.model, stream: false, max_tokens: this.options.maxTokens ?? 2048,
          messages: [{ role: 'system', content: request.system }, ...request.messages.map(message => {
            if (message.role === 'tool') return { role: 'tool', tool_call_id: message.callId, content: message.text };
            if (message.role === 'user') return { role: 'user', content: message.text };
            return { role: 'assistant', content: message.text || null,
              ...(message.toolCalls?.length ? { tool_calls: message.toolCalls.map(call => ({ id: call.id, type: 'function', function: { name: call.name, arguments: JSON.stringify(call.input) } })) } : {}),
            };
          })],
          ...(request.tools.length ? { tools: request.tools.map(tool => ({ type: 'function', function: { name: tool.name, description: tool.description, parameters: tool.inputSchema } })) } : {}),
        }),
      });
    } catch { throw new Error(signal.aborted ? 'OpenAI-compatible request cancelled or timed out' : 'OpenAI-compatible network request failed'); }
    if (!response.ok) throw new Error(`OpenAI-compatible request failed (HTTP ${response.status})`);
    try {
      const { message, finish_reason } = responseSchema.parse(await response.json()).choices[0];
      const toolCalls = (message.tool_calls ?? []).map(call => ({ id: call.id, name: call.function.name, input: z.record(z.unknown()).parse(JSON.parse(call.function.arguments)) }));
      if (new Set(toolCalls.map(call => call.id)).size !== toolCalls.length || (finish_reason === 'tool_calls') !== !!toolCalls.length) throw new Error();
      if (!message.content?.trim() && !toolCalls.length) throw new Error();
      return { text: message.content ?? '', toolCalls };
    } catch { throw new Error('Invalid or incomplete OpenAI-compatible response'); }
  }
}
