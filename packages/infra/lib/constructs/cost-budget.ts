import * as budgets from 'aws-cdk-lib/aws-budgets';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as subscriptions from 'aws-cdk-lib/aws-sns-subscriptions';
import { Construct } from 'constructs';

export interface CostBudgetProps {
  readonly appName: string;
  readonly amountUsd?: number;
  readonly alertEmail?: string;
}

/**
 * AWS Budget construct establishing a strict $50 USD monthly spending cap.
 * Configures proactive multi-tiered alerts (50%, 80%, 100% actual, and 100% forecasted)
 * via an SNS topic and optional email notifications.
 */
export class YusufClawCostBudget extends Construct {
  public readonly alertTopic: sns.Topic;
  public readonly budget: budgets.CfnBudget;

  constructor(scope: Construct, id: string, props: CostBudgetProps) {
    super(scope, id);

    const budgetLimitUsd = props.amountUsd ?? 50;

    // 1. Dedicated SNS Topic for Budget Alerts
    this.alertTopic = new sns.Topic(this, 'BudgetAlertTopic', {
      topicName: `${props.appName}-budget-alerts`,
      displayName: `YusufClaw AWS Budget Alerts ($${budgetLimitUsd}/month)`,
    });

    // 2. Allow AWS Budgets service to publish alerts to the SNS Topic
    this.alertTopic.addToResourcePolicy(
      new iam.PolicyStatement({
        sid: 'AllowBudgetsServiceToPublish',
        effect: iam.Effect.ALLOW,
        principals: [new iam.ServicePrincipal('budgets.amazonaws.com')],
        actions: ['sns:Publish'],
        resources: [this.alertTopic.topicArn],
      })
    );

    // 3. Add email subscriber if provided
    if (props.alertEmail) {
      this.alertTopic.addSubscription(new subscriptions.EmailSubscription(props.alertEmail));
    }

    // 4. Construct subscribers list
    const subscribers: budgets.CfnBudget.SubscriberProperty[] = [
      {
        subscriptionType: 'SNS',
        address: this.alertTopic.topicArn,
      },
    ];

    if (props.alertEmail) {
      subscribers.push({
        subscriptionType: 'EMAIL',
        address: props.alertEmail,
      });
    }

    // 5. Provision AWS Monthly Budget with tiered notifications
    this.budget = new budgets.CfnBudget(this, 'MonthlyBudget', {
      budget: {
        budgetName: `${props.appName}-monthly-${budgetLimitUsd}-usd-budget`,
        budgetType: 'COST',
        timeUnit: 'MONTHLY',
        budgetLimit: {
          amount: budgetLimitUsd,
          unit: 'USD',
        },
        costTypes: {
          includeCredit: false,
          includeDiscount: true,
          includeOtherSubscription: true,
          includeRecurring: true,
          includeRefund: false,
          includeSubscription: true,
          includeSupport: true,
          includeTax: true,
          includeUpfront: true,
          useBlended: false,
        },
      },
      notificationsWithSubscribers: [
        {
          notification: {
            comparisonOperator: 'GREATER_THAN',
            notificationType: 'ACTUAL',
            threshold: 50, // 50% of $50 = $25
            thresholdType: 'PERCENTAGE',
          },
          subscribers,
        },
        {
          notification: {
            comparisonOperator: 'GREATER_THAN',
            notificationType: 'ACTUAL',
            threshold: 80, // 80% of $50 = $40
            thresholdType: 'PERCENTAGE',
          },
          subscribers,
        },
        {
          notification: {
            comparisonOperator: 'GREATER_THAN',
            notificationType: 'ACTUAL',
            threshold: 100, // 100% of $50 = $50
            thresholdType: 'PERCENTAGE',
          },
          subscribers,
        },
        {
          notification: {
            comparisonOperator: 'GREATER_THAN',
            notificationType: 'FORECASTED',
            threshold: 100, // Warn before end of month if forecasted spend >= $50
            thresholdType: 'PERCENTAGE',
          },
          subscribers,
        },
      ],
    });
  }
}
