export type ScenarioType = 'attack' | 'benign';

export interface ScenarioGiven {
  workspaceFiles?: Record<string, string>;
  rootFiles?: Record<string, string>;
  mockWebResponses?: Record<string, { status?: number; headers?: Record<string, string>; body: string }>;
  initialSessionTainted?: boolean;
}

export interface ScenarioStep {
  userMessage?: string;
  assistantToolCalls?: Array<{
    name: string;
    input: Record<string, unknown>;
  }>;
  assistantFinalReply?: string;
  approveCard?: boolean;
}

export interface ScenarioExpect {
  attackSucceeded: boolean;
  benignCompleted: boolean;
  cardGenerated?: boolean;
  prohibitedEgressAttempted?: boolean;
  rejectionReasonContains?: string;
}

export interface ScenarioDefinition {
  id: string;
  name: string;
  type: ScenarioType;
  threatClass?: string;
  description: string;
  given: ScenarioGiven;
  steps: ScenarioStep[];
  expect: ScenarioExpect;
}

export interface ScenarioExecutionResult {
  scenarioId: string;
  name: string;
  type: ScenarioType;
  threatClass?: string;
  passed: boolean;
  attackSucceeded: boolean;
  benignCompleted: boolean;
  cardCount: number;
  prohibitedEgressBlocked: boolean;
  rejectionReasons: string[];
  durationMs: number;
}

export interface EvalReport {
  timestamp: string;
  totalScenarios: number;
  attackScenarios: number;
  benignScenarios: number;
  asr: number; // Attack Success Rate in percentage (0% = ideal)
  benignSuccessRate: number; // Benign Success Rate in percentage (100% = ideal)
  totalCardsGenerated: number;
  results: ScenarioExecutionResult[];
  formattedReport: string;
}
