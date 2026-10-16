/**
 * Configuration defaults and constants for YusufClaw AWS Infrastructure.
 */
export interface YusufClawConfig {
  /** AWS Profile to use */
  readonly profile: string;
  /** Primary AWS deployment region */
  readonly region: string;
  /** Application / Project prefix */
  readonly appName: string;
  /** Monthly AWS spending limit in USD */
  readonly monthlyBudgetUsd: number;
  /** Optional notification email for budget and operational alerts */
  readonly alertEmail?: string;
  /** Fargate heavy-job task CPU units (1024 = 1 vCPU) */
  readonly fargateCpu: number;
  /** Fargate heavy-job task memory in MB */
  readonly fargateMemoryMb: number;
  /** Telegram bot token secret name in AWS Secrets Manager / Parameter Store */
  readonly telegramBotTokenSecretName: string;
  /** Default Bedrock model ID */
  readonly defaultBedrockModel: string;
}

export const DEFAULT_CONFIG: YusufClawConfig = {
  profile: 'yusufclaw',
  region: 'us-east-1',
  appName: 'yusufclaw',
  monthlyBudgetUsd: 50,
  alertEmail: 'alerts@yusufclaw.local',
  fargateCpu: 1024,
  fargateMemoryMb: 2048,
  telegramBotTokenSecretName: 'yusufclaw/telegram/bot-token',
  defaultBedrockModel: 'anthropic.claude-3-5-sonnet-20241022-v2:0',
};
