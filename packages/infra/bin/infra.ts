#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { DEFAULT_CONFIG } from '../lib/config';
import { YusufClawInfraStack } from '../lib/yusufclaw-infra-stack';

const app = new cdk.App();

// Target region: us-east-1 as specified in SPEC.md
const region = app.node.tryGetContext('region') || process.env.CDK_DEFAULT_REGION || DEFAULT_CONFIG.region;
const account = app.node.tryGetContext('account') || process.env.CDK_DEFAULT_ACCOUNT;

const stack = new YusufClawInfraStack(app, 'YusufClawInfraStack', {
  env: {
    account,
    region,
  },
  description: 'YusufClaw personal AI agent AWS infrastructure (us-east-1)',
});

// Resource tagging for cost allocation and inventory tracking
cdk.Tags.of(stack).add('Project', 'YusufClaw');
cdk.Tags.of(stack).add('ManagedBy', 'AWS-CDK');
cdk.Tags.of(stack).add('Owner', 'yusuf');
cdk.Tags.of(stack).add('Budget', '50USD');

app.synth();
