from prowler.lib.check.models import Check, Check_Report_Azure
from prowler.providers.azure.services.app.app_client import app_client


class app_minimum_tls_version_12(Check):
    def execute(self) -> Check_Report_Azure:
        findings = []

        for (
            subscription_name,
            apps,
        ) in app_client.apps.items():
            for app_name, app in apps.items():
                report = Check_Report_Azure(self.metadata())
                report.status = "FAIL"
                report.subscription = subscription_name
                report.resource_name = app_name
                report.resource_id = app.resource_id
                report.location = app.location
                report.status_extended = f"Minimum TLS version is not set to 1.2 for app '{app_name}' in subscription '{subscription_name}'."

                if app.configurations and getattr(
                    app.configurations, "min_tls_version", ""
                ) in ["1.2", "1.3"]:
                    report.status = "PASS"
                    report.status_extended = f"Minimum TLS version is set to {app.configurations.min_tls_version} for app '{app_name}' in subscription '{subscription_name}'."

                findings.append(report)

        return findings
