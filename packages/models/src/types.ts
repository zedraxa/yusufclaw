export interface ToolCall { id: string; name: string; input: Record<string, unknown> }
export interface ToolDefinition { name: string; description: string; inputSchema: Record<string, unknown> }
export type Message =
  | { role: 'user'; text: string }
  | { role: 'assistant'; text: string; toolCalls?: ToolCall[] }
  | { role: 'tool'; callId: string; name: string; text: string; error?: boolean };
export interface ModelRequest {
  system: string;
  messages: Message[];
  tools: ToolDefinition[];
  signal?: AbortSignal;
}
export interface ModelResponse { text: string; toolCalls: ToolCall[] }
export interface ModelProvider { complete(request: ModelRequest): Promise<ModelResponse> }
