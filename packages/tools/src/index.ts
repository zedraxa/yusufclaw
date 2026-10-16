import type { ToolCall, ToolDefinition } from '@yusufclaw/models';
export interface AgentTool extends ToolDefinition {
  requiresApproval?: boolean;
  execute(input: Record<string, unknown>, signal?: AbortSignal): Promise<string>;
}
export type ApproveTool = (call: ToolCall) => Promise<boolean>;
// Phase 1 ships only a read-only tool. Shell/files/browser/delegation are later phases.
export const currentTime: AgentTool = {
  name: 'current_time', description: 'Return the current date and time in UTC.',
  inputSchema: { type: 'object', properties: {}, additionalProperties: false },
  async execute() { return new Date().toISOString(); },
};
