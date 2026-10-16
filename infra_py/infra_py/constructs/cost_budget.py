"""AWS Cost Budget construct establishing $50/month spending cap for YusufClaw."""

from aws_cdk import aws_budgets as budgets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as subscriptions
from constructs import Construct


class YusufClawCostBudget(Construct):
    """AWS Budget construct establishing a strict $50 USD monthly spending cap.

    Configures proactive multi-tiered alerts (50%, 80%, 100% actual, and 100% forecasted)
    via an SNS topic and optional email notifications.
    """

    def __init__(
        self,
        scope: Construct,
        id_: str,
        *,
        app_name: str,
        amount_usd: float = 50.0,
        alert_email: str | None = None,
    ) -> None:
        super().__init__(scope, id_)

        budget_limit_usd = amount_usd

        # 1. Dedicated SNS Topic for Budget Alerts
        self.alert_topic = sns.Topic(
            self,
            "BudgetAlertTopic",
            topic_name=f"{app_name}-budget-alerts",
            display_name=f"YusufClaw AWS Budget Alerts (${int(budget_limit_usd)}/month)",
        )

        # 2. Allow AWS Budgets service to publish alerts to the SNS Topic
        self.alert_topic.add_to_resource_policy(
            iam.PolicyStatement(
                sid="AllowBudgetsServiceToPublish",
                effect=iam.Effect.ALLOW,
                principals=[iam.ServicePrincipal("budgets.amazonaws.com")],
                actions=["sns:Publish"],
                resources=[self.alert_topic.topic_arn],
            )
        )

        # 3. Add email subscriber if provided
        if alert_email:
            self.alert_topic.add_subscription(subscriptions.EmailSubscription(alert_email))

        # 4. Construct subscribers list
        subscribers: list[budgets.CfnBudget.SubscriberProperty] = [
            budgets.CfnBudget.SubscriberProperty(
                subscription_type="SNS",
                address=self.alert_topic.topic_arn,
            ),
        ]

        if alert_email:
            subscribers.append(
                budgets.CfnBudget.SubscriberProperty(
                    subscription_type="EMAIL",
                    address=alert_email,
                )
            )

        # 5. Provision AWS Monthly Budget with tiered notifications
        self.budget = budgets.CfnBudget(
            self,
            "MonthlyBudget",
            budget=budgets.CfnBudget.BudgetDataProperty(
                budget_name=f"{app_name}-monthly-{int(budget_limit_usd)}-usd-budget",
                budget_type="COST",
                time_unit="MONTHLY",
                budget_limit=budgets.CfnBudget.SpendProperty(
                    amount=budget_limit_usd,
                    unit="USD",
                ),
                cost_types=budgets.CfnBudget.CostTypesProperty(
                    include_credit=False,
                    include_discount=True,
                    include_other_subscription=True,
                    include_recurring=True,
                    include_refund=False,
                    include_subscription=True,
                    include_support=True,
                    include_tax=True,
                    include_upfront=True,
                    use_blended=False,
                ),
            ),
            notifications_with_subscribers=[
                budgets.CfnBudget.NotificationWithSubscribersProperty(
                    notification=budgets.CfnBudget.NotificationProperty(
                        comparison_operator="GREATER_THAN",
                        notification_type="ACTUAL",
                        threshold=50,  # 50% of $50 = $25
                        threshold_type="PERCENTAGE",
                    ),
                    subscribers=subscribers,
                ),
                budgets.CfnBudget.NotificationWithSubscribersProperty(
                    notification=budgets.CfnBudget.NotificationProperty(
                        comparison_operator="GREATER_THAN",
                        notification_type="ACTUAL",
                        threshold=80,  # 80% of $50 = $40
                        threshold_type="PERCENTAGE",
                    ),
                    subscribers=subscribers,
                ),
                budgets.CfnBudget.NotificationWithSubscribersProperty(
                    notification=budgets.CfnBudget.NotificationProperty(
                        comparison_operator="GREATER_THAN",
                        notification_type="ACTUAL",
                        threshold=100,  # 100% of $50 = $50
                        threshold_type="PERCENTAGE",
                    ),
                    subscribers=subscribers,
                ),
                budgets.CfnBudget.NotificationWithSubscribersProperty(
                    notification=budgets.CfnBudget.NotificationProperty(
                        comparison_operator="GREATER_THAN",
                        notification_type="FORECASTED",
                        threshold=100,  # Warn before end of month if forecasted spend >= $50
                        threshold_type="PERCENTAGE",
                    ),
                    subscribers=subscribers,
                ),
            ],
        )
