import { BedrockRuntimeClient, ConverseCommand, type ConverseCommandOutput, type Message as AwsMessage, type ToolInputSchema } from '@aws-sdk/client-bedrock-runtime';
import { fromNodeProviderChain } from '@aws-sdk/credential-providers';
import type { ModelProvider, ModelRequest, ModelResponse } from './types.js';

export interface BedrockTransport {
  send(command: ConverseCommand, options?: { abortSignal?: AbortSignal }): Promise<ConverseCommandOutput>;
}
export class BedrockProvider implements ModelProvider {
  private readonly client: BedrockTransport;
  constructor(private readonly options: { model: string; region?: string; profile?: string; maxTokens?: number; timeoutMs?: number }, client?: BedrockTransport) {
    this.client = client ?? new BedrockRuntimeClient({
      region: options.region ?? 'us-east-1',
      credentials: fromNodeProviderChain({ profile: options.profile ?? 'yusufclaw' }),
      maxAttempts: 3, retryMode: 'adaptive',
    });
  }
  async complete(request: ModelRequest): Promise<ModelResponse> {
    const messages: AwsMessage[] = [];
    for (const message of request.messages) {
      if (message.role === 'tool') {
        const block = { toolResult: { toolUseId: message.callId, content: [{ text: message.text }], status: message.error ? 'error' as const : 'success' as const } };
        const previous = messages.at(-1);
        if (previous?.role === 'user' && previous.content?.some(b => b.toolResult)) previous.content.push(block);
        else messages.push({ role: 'user', content: [block] });
      } else if (message.role === 'user') {
        messages.push({ role: 'user', content: [{ text: message.text }] });
      } else {
        messages.push({ role: 'assistant', content: [
          ...(message.text ? [{ text: message.text }] : []),
          ...(message.toolCalls ?? []).map(call => ({ toolUse: { toolUseId: call.id, name: call.name, input: call.input as ToolInputSchema.JsonMember['json'] } })),
        ] });
      }
    }
    const timeout = AbortSignal.timeout(this.options.timeoutMs ?? 120_000);
    const output = await this.client.send(new ConverseCommand({
      modelId: this.options.model, system: [{ text: request.system }], messages,
      inferenceConfig: { maxTokens: this.options.maxTokens ?? 2048 },
      ...(request.tools.length ? { toolConfig: { tools: request.tools.map(tool => ({ toolSpec: {
        name: tool.name, description: tool.description, inputSchema: { json: tool.inputSchema as ToolInputSchema.JsonMember['json'] },
      } })) } } : {}),
    }), { abortSignal: request.signal ? AbortSignal.any([request.signal, timeout]) : timeout });
    if (output.stopReason && !['end_turn', 'tool_use', 'stop_sequence'].includes(output.stopReason)) {
      throw new Error(`Bedrock stopped before completion: ${output.stopReason}`);
    }
    const content = output.output?.message?.content;
    if (!content?.length) throw new Error('Bedrock returned no message');
    const toolCalls = content.flatMap(block => {
      if (!block.toolUse) return [];
      const { toolUseId, name, input } = block.toolUse;
      if (!toolUseId || !name || !input || typeof input !== 'object' || Array.isArray(input)) throw new Error('Invalid Bedrock tool call');
      return [{ id: toolUseId, name, input: input as Record<string, unknown> }];
    });
    const text = content.map(block => block.text ?? '').join('');
    if (!text.trim() && !toolCalls.length) throw new Error('Bedrock returned an empty response');
    return { text, toolCalls };
  }
  close(): void { if (this.client instanceof BedrockRuntimeClient) this.client.destroy(); }
}
