export type ToolEffect =
  | 'read_private'
  | 'read_untrusted'
  | 'egress'
  | 'write_local'
  | 'exec'
  | 'send_message'
  | 'spend';

export type ToolSource = 'builtin' | 'mcp' | 'skill';

export interface ToolManifest {
  id: string;
  version: string;
  source: ToolSource;
  effects: ToolEffect[];
  resources?: {
    paths?: string[];
    hosts?: string[];
  };
  descriptionSha256?: string;
}
